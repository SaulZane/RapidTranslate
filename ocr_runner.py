"""Runs inside BabelDOC's Python: OCR scanned PDF pages with RapidOCR (PP-OCRv6) into paragraph blocks (JSON).

ocr_layout.py translates and typesets the blocks; this file only reads the page."""
import json
import re
import sys

import numpy as np
import pymupdf as fitz

DPI = 200
NATIVE_TEXT_CHARS = 50  # Pages that already have this much real text keep their own text layer.
MIN_SCORE = 0.6  # Lower-confidence lines are usually photo texture; they stay as image.
MATH = re.compile(r'[=+×÷∫∑√<>^±≤≥]')


def engine():
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
    # Medium detector separates words in dense newspaper columns; the small English recognizer is accurate and fast.
    return RapidOCR(params={
        'Det.ocr_version': OCRVersion.PPOCRV6, 'Det.model_type': ModelType.MEDIUM,
        'Rec.ocr_version': OCRVersion.PPOCRV6, 'Rec.model_type': ModelType.SMALL,
        'Rec.lang_type': LangRec.EN, 'Global.use_cls': False, 'Global.max_side_len': 4000, 'Global.log_level': 'warning'})


def is_formula(text):
    """Equations stay as the original image: OCR cannot read math symbols reliably and they need no translation."""
    words = re.findall(r'[A-Za-z]{3,}', text)
    return bool(MATH.search(text)) and len(words) < 2


def median(values):
    values = sorted(values)
    return values[len(values) // 2]


def gutters(rects, tolerance=2, minimum=5):
    """x positions of column gutters: many lines end just left of where many lines start."""
    def popular(values):
        return sorted({round(x) for x in values if sum(abs(x - other) <= tolerance for other in values) >= minimum})
    rights, lefts = popular([r.x1 for r in rects]), popular([r.x0 for r in rects])
    return sorted({(r + l) / 2 for r in rights for l in lefts if 2 <= l - r <= 8})


def to_lines(boxes, texts, scale, scores=None):
    """OCR boxes -> [(rect, text)], merging fragments the detector split from one printed line,
    but never across a column gutter."""
    items = []
    for quad, text, score in zip(boxes, texts, scores or [1] * len(texts)):
        text = text.strip()
        if text and score >= MIN_SCORE and not is_formula(text):
            xs, ys = [p[0] * scale for p in quad], [p[1] * scale for p in quad]
            items.append([fitz.Rect(min(xs), min(ys), max(xs), max(ys)), text])
    columns = gutters([rect for rect, _ in items])
    items.sort(key=lambda item: (item[0].x0, item[0].y0))
    lines = []
    for rect, text in items:
        for line in lines:
            other = line[0]
            overlap = min(rect.y1, other.y1) - max(rect.y0, other.y0)
            gap = rect.x0 - other.x1
            if (overlap > 0.6 * min(rect.height, other.height) and -0.5 * other.height <= gap < 1.5 * other.height
                    and max(rect.height, other.height) < 1.4 * min(rect.height, other.height)
                    and not any(other.x1 - 1 <= x <= rect.x0 + 1 for x in columns)):
                line[0], line[1] = other | rect, line[1] + ' ' + text
                break
        else:
            lines.append([rect, text])
    return lines


def same_style(a, b):
    """(size, fonts) of two lines: bylines, captions and "Continued on Page" differ from body text."""
    return abs(a[0] - b[0]) <= 0.04 * max(a[0], b[0]) and bool(a[2] & b[2])


def to_blocks(lines, styles=None):
    """Group lines into paragraphs: same column, consecutive, similar height; an indented line starts a new one.

    BabelDOC splits paragraphs wherever the font size changes, so add_blocks gives every line one size.
    """
    blocks = []
    for rect, text in sorted(lines, key=lambda line: (line[0].y0, line[0].x0)):
        for block in reversed(blocks):
            last = block[-1][0]
            height = median(r.height for r, _ in block)
            left = min(r.x0 for r, _ in block)
            overlap = min(rect.x1, last.x1) - max(rect.x0, last.x0)
            if (overlap > 0.5 * min(rect.width, last.width) and -0.4 * height < rect.y0 - last.y1 < 0.6 * height
                    and 0.75 * height < rect.height < 1.4 * height and abs(rect.x0 - left) < 2 * height
                    and (styles is None or same_style(styles[tuple(rect)], styles[tuple(last)]))):
                # First-line indent of justified text; centred headlines also start further right.
                right = max(r.x1 for r, _ in block)
                indented = (rect.x0 - left > 0.4 * height and last.x0 - left < 0.4 * height
                            and abs(rect.x1 - right) < 0.5 * height)
                if not indented:
                    block.append((rect, text))
                    break
                blocks.append([(rect, text)])
                break
        else:
            blocks.append([(rect, text)])
    return blocks


def block_sizes(blocks):
    """Font size per block, snapped to the page's common sizes so body text is one uniform size."""
    raw = [round(median(r.height for r, _ in block) * 0.8 * 4) / 4 for block in blocks]  # boxes include padding
    weight = {}
    for size, block in zip(raw, blocks):
        weight[size] = weight.get(size, 0) + len(block)
    common = sorted(weight, key=weight.get, reverse=True)
    return [next(c for c in common if abs(c - size) <= 0.15 * size or c == size) for size in raw]


def describe(block, size, image, scale):
    """Block geometry plus the colours and free space below it, measured on the page image."""
    rect = fitz.Rect()
    for line, _ in block:
        rect |= line
    x0, y0 = max(int(rect.x0 / scale), 0), max(int(rect.y0 / scale), 0)
    x1, y1 = int(rect.x1 / scale) + 1, int(rect.y1 / scale) + 1
    region = image[y0:y1, x0:x1].reshape(-1, 3)
    background = np.median(region, axis=0)
    dark = background.mean() < 110
    # Rows below the block that contain no ink can take overflowing translation.
    below = image[y1:y1 + int(4 * size / scale), x0:x1].astype(int)
    ink = (np.abs(below - background).max(axis=2) > 60).mean(axis=1) > 0.01
    free = int(np.argmax(ink)) if ink.any() else len(ink)
    return {'rect': [round(v, 2) for v in rect], 'size': size,
            'lines': [[[round(v, 2) for v in line], text] for line, text in block],
            'background': [round(float(c) / 255, 3) for c in background],
            'color': [1, 1, 1] if dark else [0, 0, 0], 'free_below': round(free * scale, 2)}


def native_pages(source, image_source=None):
    """Paragraph blocks from the PDF's own text geometry (no OCR). image_source is the unprotected
    original, so free space below a block never runs into restored code."""
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_LIGATURES
    result = []
    with fitz.open(source) as doc, fitz.open(image_source or source) as pictures:
        for index, page in enumerate(doc):
            lines, styles = [], {}
            for block in page.get_text('dict', flags=flags)['blocks']:
                printed = []  # justified lines arrive as word fragments on one baseline: rejoin them
                for line in block.get('lines', []):
                    if tuple(line['dir']) != (1.0, 0.0) or not ''.join(s['text'] for s in line['spans']).strip():
                        continue  # rotated labels keep the original
                    rect = fitz.Rect(line['bbox'])
                    last = printed[-1] if printed else None
                    if (last and rect.x0 >= last[0].x1 - 1 and line['spans'][0]['font'] == last[1][-1]['font']
                            and abs(line['spans'][0]['size'] - last[1][-1]['size']) < 0.1
                            and min(rect.y1, last[0].y1) - max(rect.y0, last[0].y0) > 0.6 * min(rect.height, last[0].height)):
                        last[0] |= rect
                        last[1].extend(line['spans'])
                    else:
                        printed.append([rect, list(line['spans'])])
                for rect, spans in printed:
                    text = ' '.join(' '.join(span['text'] for span in spans).split())
                    if is_formula(text):
                        continue  # equations keep the original
                    biggest = max(spans, key=lambda span: len(span['text']))
                    styles[tuple(rect)] = (biggest['size'], fitz.sRGB_to_pdf(biggest['color']),
                                           {span['font'] for span in spans if span['text'].strip()})
                    lines.append((rect, text))
            blocks = to_blocks(lines, styles)
            pix = pictures[index].get_pixmap(dpi=DPI, colorspace=fitz.csRGB, alpha=False)
            image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
            described = []
            for block in blocks:
                size = round(median(styles[tuple(r)][0] for r, _ in block) * 4) / 4
                item = describe(block, size, image, 72 / DPI)
                item['color'] = list(styles[tuple(block[0][0])][1])
                described.append(item)
            result.append({'index': index, 'native': True, 'blocks': described})
            print(f'@@PROGRESS {100 * (index + 1) / len(doc):.1f}', flush=True)
            print(f'▶ 整理文字段落：第 {index + 1}/{len(doc)} 页，{len(blocks)} 段', flush=True)
    return result


def main(source, destination):
    doc = fitz.open(source)
    pages = [index for index, page in enumerate(doc) if len(page.get_text().strip()) < NATIVE_TEXT_CHARS]
    if len(pages) < len(doc):
        print(f'   {len(doc) - len(pages)} 页已有文字层，保留原文字', flush=True)
    result = {'pages': []}
    if pages:
        print('▶ OCR 识别文字（RapidOCR · PP-OCRv6，首次使用会下载模型）', flush=True)
        ocr, scale = engine(), 72 / DPI
        for number, index in enumerate(pages, 1):
            pix = doc[index].get_pixmap(dpi=DPI, colorspace=fitz.csRGB, alpha=False)
            image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
            found = ocr(image)
            lines = to_lines(found.boxes if found.boxes is not None else [], found.txts or [], scale,
                             list(found.scores or []))
            blocks = to_blocks(lines)
            result['pages'].append({'index': index, 'blocks': [
                describe(block, size, image, scale) for block, size in zip(blocks, block_sizes(blocks))]})
            print(f'@@PROGRESS {100 * number / len(pages):.1f}', flush=True)
            print(f'▶ OCR 识别：第 {number}/{len(pages)} 页（原第 {index + 1} 页），{len(lines)} 行文字，{len(blocks)} 个段落块', flush=True)
        print('✔ OCR 识别完成', flush=True)
    with open(destination, 'w', encoding='utf-8') as file:
        json.dump(result, file, ensure_ascii=False)


if __name__ == '__main__':
    if '--native' in sys.argv:
        with open(sys.argv[2], 'w', encoding='utf-8') as file:
            json.dump({'pages': native_pages(sys.argv[1], sys.argv[4] if len(sys.argv) > 4 else None)}, file, ensure_ascii=False)
    else:
        main(sys.argv[1], sys.argv[2])
