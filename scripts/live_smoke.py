"""Opt-in real API checks using only this project's self-authored samples."""
import argparse
import json
from pathlib import Path
import re
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine
from book_formats import parse_xml
from ebook_chapters import index_epub, BookIndex


def visible_text(path):
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        import pymupdf
        with pymupdf.open(path) as doc:
            return '\n'.join(page.get_text() for page in doc), len(doc)
    if suffix == '.docx':
        with ZipFile(path) as archive:
            root = parse_xml(archive.read('word/document.xml'), 'document.xml')
            return ''.join(root.getroot().itertext()), None
    if suffix == '.epub':
        with ZipFile(path) as archive:
            book = index_epub(archive)
            return '\n'.join(''.join(parse_xml(book.files[name], name).getroot().itertext())
                             for name in book.spine), len(book.spine)
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / 'parts.zip'
        command = [str(engine.find_calibre_debug()), '-e', str(engine.AZW3_RUNNER), '--',
                   'export', str(path), str(bundle)]
        if engine.stream(command, lambda _: None):
            raise RuntimeError('无法重新打开输出电子书')
        metadata = json.loads(Path(str(bundle) + '.json').read_text(encoding='utf-8'))
        opf = next(name for name, item in metadata.items() if item['kind'] == 'opf')
        with ZipFile(bundle) as archive:
            files = {name: archive.read(name) for name in metadata}
        book = BookIndex(opf, files)
        return '\n'.join(''.join(parse_xml(files[name], name).getroot().itertext())
                         for name in book.spine), len(book.spine)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly enable real API calls for synthetic samples')
    parser.add_argument('--model', default='Index-Translate-9B')
    parser.add_argument('--retry-failed', action='store_true', help='Only rerun failed/missing cases from the saved report')
    args = parser.parse_args()
    if not args.live:
        parser.error('Use --live to run real API tests against the self-authored samples')
    output = ROOT / 'samples/verified'
    output.mkdir(parents=True, exist_ok=True)
    report = (json.loads((output / 'results.json').read_text(encoding='utf-8'))
              if args.retry_failed and (output / 'results.json').exists() else [])
    cases = []
    for ext in ['docx', 'epub', 'azw3', 'mobi', 'pdf']:
        cases.append(('full', ext, 'tutorial', {}))
        options = {'pages': '3'} if ext in {'docx', 'pdf'} else {'chapters': ['5' if ext == 'mobi' else '3']}
        cases.append(('partial', ext, 'tutorial', options))
    cases += [('science', 'epub', 'science', {'chapters': ['1.1']}),
              ('fiction', 'epub', 'fiction', {'chapters': ['2']})]
    for scope, ext, template, options in cases:
        source = ROOT / 'samples/input' / ('sample_book.' + ext)
        original = source.read_bytes()
        key = scope + '_' + ext
        if any(entry['case'] == key and entry['status'] == 'passed' for entry in report):
            continue
        print('VERIFY', key, flush=True)
        entry = {'case': key, 'template': template, 'selection': options}
        log_lines = []
        def log(line):
            log_lines.append(line)
            if not line.startswith('@@'):
                print(line, flush=True)
        try:
            result = engine.translate(source, args.model,
                                      'Mira Stone 统一译为“米拉·斯通”，Silver Bay 统一译为“银湾”，Zyphron 保留原文。',
                                      output / scope, on_line=log, template=template, **options)
            assert result is not None
            assert source.read_bytes() == original
            text, count = visible_text(result)
            assert re.search(r'[\u4e00-\u9fff]', text), '未发现中文译文'
            assert result.suffix == '.' + ext
            if template == 'tutorial':
                compact = re.sub(r'\s+', '', text)
                assert 'defgreet(name):' in compact, '函数声明被更改'
                assert 'return"Hello,"+name' in compact, '代码或字符串被更改'
                assert 'Print a friendly greeting' not in text, '第一条注释未翻译'
                assert 'Keep the original value' not in text, '第二条注释未翻译'
                if scope == 'partial':
                    assert 'Zyphron' not in text and '米拉' not in text, '包含未选中的正文'
                    if ext == 'pdf':
                        assert count == 1
            if template == 'science':
                assert 'Zyphron' in text
            if template == 'fiction':
                assert text.count('米拉·斯通') >= 2, '人物译名未按手工要求保持一致'
                assert text.count('银湾') >= 2, '地名未按手工要求保持一致'
            entry.update(status='passed', output=str(result), visible_units=count)
            print('PASSED', key, result, flush=True)
        except Exception as exc:
            entry.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            print('FAILED', key, entry['error'], flush=True)
        report = [old for old in report if old['case'] != key]
        report.append(entry)
        (output / (key + '.log')).write_text('\n'.join(log_lines), encoding='utf-8')
        (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(entry['status'] == 'passed' for entry in report) else 1


if __name__ == '__main__':
    raise SystemExit(main())
