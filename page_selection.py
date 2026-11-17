"""Physical page ranges, shared by PDF and Word."""
import re


def parse_pages(value, total=None):
    if not value or not value.strip():
        return list(range(1, total + 1)) if total is not None else []
    result = set()
    for token in value.replace('，', ',').split(','):
        match = re.fullmatch(r'\s*([1-9]\d*)\s*(?:-\s*([1-9]\d*)\s*)?', token)
        if not match:
            raise ValueError('页码格式不正确，请使用 6-8,9,11；页码从 1 开始。')
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start:
            raise ValueError(f'页码范围不能倒序：{token}')
        if total is not None and end > total:
            raise ValueError(f'页码 {end} 超出文档总页数 {total}')
        if end - start > 100000:
            raise ValueError('页码范围过大')
        result.update(range(start, end + 1))
    return sorted(result)


def extract_pdf_pages(source, destination, pages):
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(source)
    if reader.is_encrypted and not reader.decrypt(''):
        raise ValueError('PDF 已加密，无法读取页面')
    selected = parse_pages(pages, len(reader.pages))
    writer = PdfWriter()
    writer.append(reader, pages=[page - 1 for page in selected])
    with open(destination, 'wb') as output:
        writer.write(output)
    return selected


def extract_docx_pages(source, destination, pages, cancel_event=None):
    """Ask a separate native Word instance for page boundaries; never estimate pages."""
    import os
    import shutil
    from translation_client import check_cancelled
    if os.name != 'nt':
        raise RuntimeError('DOCX 按实际页码截取需要 Windows 和 Microsoft Word；整本 DOCX 翻译不需要 Word。')
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError('DOCX 页码功能缺少 pywin32，请安装 requirements.txt 中的依赖。') from exc
    parse_pages(pages)
    check_cancelled(cancel_event)
    shutil.copyfile(source, destination)
    pythoncom.CoInitialize()
    app = document = None
    try:
        try:
            app = win32com.client.DispatchEx('Word.Application')
        except Exception as exc:
            raise RuntimeError('DOCX 指定页码需要安装并能正常启动 Microsoft Word。') from exc
        app.Visible = False
        app.DisplayAlerts = 0
        app.AutomationSecurity = 3
        document = app.Documents.Open(str(destination), ReadOnly=False, AddToRecentFiles=False,
                                      ConfirmConversions=False, Visible=False)
        track_revisions = document.TrackRevisions
        document.TrackRevisions = False
        document.Repaginate()
        total = document.ComputeStatistics(2)  # wdStatisticPages
        selected = parse_pages(pages, total)
        starts = [document.GoTo(What=1, Which=1, Count=i).Start for i in range(1, total + 1)]
        starts.append(document.Content.End - 1)  # Keep the final paragraph/section properties.
        # Remove unselected original pages from the end, so all offsets remain valid.
        keep = set(selected)
        gaps, begin = [], None
        for i in range(1, total + 2):
            if i <= total and i not in keep:
                if begin is None:
                    begin = i
            elif begin is not None:
                gaps.append((begin, i - 1))
                begin = None
        for first, last in reversed(gaps):
            check_cancelled(cancel_event)
            start, end = starts[first - 1], starts[last]
            if end > start:
                document.Range(start, end).Delete()
        text = document.Content.Text
        trimmed = text.rstrip('\r\n\f')
        if len(trimmed) < len(text) - 1:
            document.Range(document.Content.Start + len(trimmed.encode('utf-16-le')) // 2,
                           document.Content.End - 1).Delete()
        document.TrackRevisions = track_revisions
        check_cancelled(cancel_event)
        document.Save()
        return selected
    finally:
        try:
            if document is not None:
                document.Close(SaveChanges=0)
        finally:
            document = None
            try:
                if app is not None:
                    app.Quit(SaveChanges=0)
            finally:
                app = None
                pythoncom.CoUninitialize()
