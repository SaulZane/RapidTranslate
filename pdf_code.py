"""Keep PDF code glyphs in place while BabelDoc translates prose around them."""
import re

from code_comments import comment_ranges
from translation_client import check_cancelled, translate_all


def protect_code(source, work, client, template, on_line, cancel_event):
    import pymupdf as fitz
    original = fitz.open(source)
    protected = fitz.open(source)
    regions, comments = [], []
    try:
        for page_index, page in enumerate(original):
            check_cancelled(cancel_event)
            candidates = []
            for block in page.get_text('rawdict')['blocks']:
                if block.get('type') != 0:
                    continue
                records, mono, total = [], 0, 0
                for line in block.get('lines', []):
                    for span in line['spans']:
                        monospaced = bool(re.search(r'courier|consolas|mono|code', span['font'], re.I))
                        for char in span['chars']:
                            records.append((char['c'], char['bbox'], char['origin'], span['size']))
                            total += 1
                            mono += monospaced
                    records.append(('\n', None, None, None))
                text = ''.join(record[0] for record in records)
                if not total or mono / total < 0.6:
                    continue
                rect = fitz.Rect(block['bbox']) + (-1, -1, 1, 1)
                if candidates and 0 <= rect.y0 - candidates[-1][0].y1 < 20:
                    previous_rect, previous_records = candidates[-1]
                    previous_records.extend(records)
                    candidates[-1] = (previous_rect | rect, previous_records)
                else:
                    candidates.append((rect, records))
            for rect, records in candidates:
                text = ''.join(record[0] for record in records)
                looks_like_code = bool(re.search(r'^\s*(?:def\s+\w+\(|class\s+\w+|from\s+\S+\s+import|import\s+\w|return\b|function\b|(?:const|let|var)\s|#\s|//|/\*|\w+\s*=|\w+(?:\.\w+)*\(.*\)\s*;?\s*$)', text, re.M))
                if not looks_like_code:
                    continue
                regions.append({'page': page_index, 'rect': tuple(rect)})
                protected[page_index].add_redact_annot(rect, fill=False, cross_out=False)
                if template == 'tutorial':
                    for start, end in comment_ranges(text):
                        chars = records[start:end]
                        boxes = [fitz.Rect(char[1]) for char in chars if char[1] is not None]
                        if not boxes:
                            continue
                        box = boxes[0]
                        for other in boxes[1:]:
                            box |= other
                        comments.append((page_index, box, chars[0][2], chars[0][3], text[start:end]))
            if any(region['page'] == page_index for region in regions):
                protected[page_index].apply_redactions(images=0, graphics=0, text=0)
        if not regions:
            return source, None, []
        on_line(f'▶ 保护 {len(regions)} 个 PDF 代码区域，保留代码缩进与换行')
        if comments:
            on_line(f'▶ 翻译 {len(comments)} 段代码注释')
            # Comments are independent and short; batch at most eight at a time.
            chunks = [comments[offset:offset + 8] for offset in range(0, len(comments), 8)]
            answers = translate_all(client, [[[item[4]] for item in chunk] for chunk in chunks], cancel_event)
            replacements = [(item, translated[0]) for chunk, texts in zip(chunks, answers)
                            for item, translated in zip(chunk, texts)]
            for (page_index, rect, origin, size, _), text in replacements:
                original[page_index].add_redact_annot(rect, fill=False, cross_out=False)
            for page_index in {item[0] for item in comments}:
                original[page_index].apply_redactions(images=0, graphics=0, text=0)
            from font_preferences import windows_font
            font_path = windows_font('simsun.ttc')
            font = fitz.Font(fontfile=str(font_path)) if font_path else fitz.Font('china-s')
            font_name = 'code_comment_song' if font_path else 'china-s'
            for (page_index, rect, origin, size, _), text in replacements:
                width = font.text_length(text, fontsize=size)
                fitted = min(size, size * rect.width / max(width, 1))
                original[page_index].insert_text(origin, text, fontsize=fitted, fontname=font_name,
                                                  fontfile=str(font_path) if font_path else None)
        input_path, overlay_path = work / 'body.pdf', work / 'code_overlay.pdf'
        protected.save(input_path)
        original.save(overlay_path)
        return input_path, overlay_path, regions
    finally:
        protected.close()
        original.close()


def restore_code(result, overlay, regions, work):
    if overlay is None:
        return result
    import pymupdf as fitz
    destination = work / 'restored.pdf'
    with fitz.open(result) as output, fitz.open(overlay) as original:
        for region in regions:
            rect = fitz.Rect(region['rect'])
            output[region['page']].show_pdf_page(rect, original, region['page'], clip=rect)
        output.save(destination, garbage=3, deflate=True)
    return destination
