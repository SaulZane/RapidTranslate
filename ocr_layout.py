"""Translate OCR paragraph blocks and typeset the Chinese over the scanned page.

Each block is one paragraph, so the model sees whole sentences and the translation is set at one
uniform size per text style instead of BabelDOC's per-line rescaling of OCR fragments.
"""
import re

from translation_client import check_cancelled, translate_all

MAX_BATCH_CHARS = 900
MAX_BATCH_BLOCKS = 20
LINE_HEIGHT = 1.3
MIN_SCALE = 0.6
NO_LINE_START = set('，。、；：？！）》」』”’,.;:?!)%')


def join_lines(lines):
    """Rejoin printed lines, undoing end-of-line hyphenation (Decem- / ber)."""
    text = ''
    for line in lines:
        if re.search(r'[A-Za-z]-$', text) and line[:1].islower():
            text = text[:-1] + line
        else:
            text = f'{text} {line}' if text else line
    return text


def block_text(block):
    return join_lines(line for _, line in block['lines'])


def continues(block):
    """The block starts mid-sentence: 2 = lowercase start, 1 = no first-line indent, 0 = a new paragraph."""
    lines = block['lines']
    if lines[0][1][:1].islower():
        return 2
    if len(lines) > 1 and lines[0][0][0] - min(line[0][0] for line in lines[1:]) < 0.3 * block['size']:
        return 1
    return 0


JUMP_LINE = re.compile(r'^(continued|see)\b.*\bpage\b', re.I)  # "Continued on Page 14": the rest is elsewhere


def link_columns(blocks):
    """Chains of block indices that are one paragraph broken by a column end (or by a picture in the column).

    A sentence left open at the bottom of a column continues at the top of the next column to the right;
    translating the halves separately breaks the sentence, so each chain is translated as one paragraph.
    """
    texts = [block_text(block) for block in blocks]

    def shares_column(a, b):
        return min(a[2], b[2]) - max(a[0], b[0]) > 0.5 * min(a[2] - a[0], b[2] - b[0])

    following, taken = {}, set()
    for i, (block, text) in enumerate(zip(blocks, texts)):
        hyphen = bool(re.search(r'[A-Za-z]-$', text))
        if not (hyphen or re.search(r'[a-z,;—–]$', text)) or not translatable(text):
            continue
        rect = x0, y0, x1, y1 = block['rect']
        size, width = block['size'], x1 - x0
        if any(JUMP_LINE.match(texts[j]) and shares_column(rect, other['rect']) and 0 <= other['rect'][1] - y1 < 2 * size
               for j, other in enumerate(blocks)):
            continue
        # Headlines bound an article: its text never continues above or across one.
        heads = [other['rect'] for other in blocks if other['size'] > 1.25 * size and shares_column(rect, other['rect'])]
        top = max((head[3] for head in heads if head[3] <= y0 + size), default=0)
        bottom = min((head[1] for head in heads if head[1] >= y1 - size), default=float('inf'))
        best = None
        for j, other in enumerate(blocks):
            ox0, oy0, ox1, oy1 = other['rect']
            if j == i or j in taken or other['size'] != size or not translatable(texts[j]):
                continue
            start = continues(other)
            if start < (2 if hyphen or len(block['lines']) < 2 else 1) or abs((ox1 - ox0) - width) > 0.25 * width:
                continue
            if 0 < ox0 - x1 < 4 * size and top - size <= oy0 < y1:  # the next column of the same article
                key = (-start, oy0)
            elif start == 2 and shares_column(rect, other['rect']) and y1 <= oy0 < bottom:  # past a picture
                key = (-1, oy0)
            else:
                continue
            if best is None or key < best[0]:
                best = (key, j)
        if best:
            following[i] = best[1]
            taken.add(best[1])
    chains = []
    for i in range(len(blocks)):
        if i not in taken:
            chain = [i]
            while chain[-1] in following and following[chain[-1]] not in chain:
                chain.append(following[chain[-1]])
            chains.append(chain)
    return chains


def translatable(text):
    return bool(re.search(r'[A-Za-z]{2,}', text))


def translate_pages(pages, client, on_line, cancel_event, progress=(10, 90)):
    """Per page [chains, source texts, translated texts], one text per chain of link_columns.

    All pages share one queue of batches, so pages with little text are translated concurrently too."""
    plans, todo = [], []
    for number, page in enumerate(pages):
        chains = link_columns(page['blocks'])
        texts = [join_lines(block_text(page['blocks'][i]) for i in chain) for chain in chains]
        plans.append([chains, texts, list(texts)])
        todo += [(number, i) for i, text in enumerate(texts) if translatable(text)]
    linked = sum(len(chain) > 1 for chains, _, _ in plans for chain in chains)
    if linked:
        on_line(f'   已接续 {linked} 处跨栏断开的段落，整段翻译')
    batches, current, size = [], [], 0
    for page, i in todo:
        length = len(plans[page][1][i])
        if current and (size + length > MAX_BATCH_CHARS or len(current) >= MAX_BATCH_BLOCKS):
            batches.append(current)
            current, size = [], 0
        current.append((page, i))
        size += length
    if current:
        batches.append(current)
    on_line(f'▶ 按段落翻译文字（{len(pages)} 页，{len(todo)} 段，{len(batches)} 批）')
    results = translate_all(client, [[[plans[page][1][i]] for page, i in batch] for batch in batches],
                            cancel_event, on_line, progress)
    for batch, translated in zip(batches, results):
        for (page, i), spans in zip(batch, translated):
            plans[page][2][i] = spans[0].strip() or plans[page][1][i]
    return plans


def tokens(text):
    """Line-break units: one CJK character, or a Latin word/number with its trailing space."""
    return re.findall(r'[A-Za-z0-9][A-Za-z0-9.,\'’%$:/@&+\-]*\s*|\s+|.', text)


def wrap(text, font, size, width):
    lines, line, used = [], '', 0.0
    for token in tokens(text):
        length = font.text_length(token, fontsize=size)
        if line and used + length > width and token.strip() and token[0] not in NO_LINE_START:
            lines.append(line.rstrip())
            line, used = token.lstrip(), font.text_length(token.lstrip(), fontsize=size)
        else:
            line, used = line + token, used + length
    if line.strip():
        lines.append(line.rstrip())
    return lines


def unwrap(lines):
    text = ''
    for line in lines:
        text += (' ' if text[-1:].isascii() and text[-1:].isalnum() and line[:1].isascii() else '') + line
    return text


def flow(text, blocks, font):
    """Split one translated paragraph over the blocks of its chain, in proportion to their height."""
    size = blocks[0]['size']
    pitch = size * LINE_HEIGHT
    room = [max(1, int((b['rect'][3] - b['rect'][1] + pitch - size) / pitch + 0.01)) for b in blocks]
    needed = len(wrap(text, font, size, blocks[0]['rect'][2] - blocks[0]['rect'][0]))
    share = needed / sum(room)  # Chinese is usually shorter: every column keeps the same share of its lines
    parts = []
    for block, lines_here in zip(blocks[:-1], room):
        lines = wrap(text, font, size, block['rect'][2] - block['rect'][0])
        count = max(1, round(lines_here * share))
        parts.append(unwrap(lines[:count]))
        text = unwrap(lines[count:])
    return parts + [text]


def fit_scale(block, text, font):
    """Largest scale (<=1) at which the translation fits the block plus the free space below it."""
    x0, y0, x1, y1 = block['rect']
    height = y1 - y0 + block['free_below']
    scale = 1.0
    while scale > MIN_SCALE:
        size = block['size'] * scale
        if len(wrap(text, font, size, x1 - x0)) * size * LINE_HEIGHT <= height + size * (LINE_HEIGHT - 1):
            return scale
        scale -= 0.05
    return MIN_SCALE


def uniform_scales(blocks, texts, font):
    """Blocks of one style share the scale most of them need, so body text keeps one size."""
    need = [fit_scale(block, text, font) for block, text in zip(blocks, texts)]
    shared = {}
    for block, scale in zip(blocks, need):
        shared.setdefault(block['size'], []).append(scale)
    common = {size: sorted(values)[len(values) // 4] for size, values in shared.items()}  # 25th percentile
    return [min(scale, common[block['size']]) for block, scale in zip(blocks, need)]


def draw(page, block, text, scale, font_name, font, cover=True):
    x0, y0, x1, y1 = block['rect']
    for (lx0, ly0, lx1, ly1), _ in block['lines']:
        pad = (ly1 - ly0) * 0.12
        if cover:  # scanned text is pixels; native text was redacted, so keep rules and backgrounds
            page.draw_rect((lx0 - pad, ly0 - pad, lx1 + pad, ly1 + pad), color=None,
                           fill=block['background'], overlay=True)
    size = block['size'] * scale
    lines = wrap(text, font, size, x1 - x0)
    # Short blocks (headlines, labels) stay vertically centred on the original text.
    total = len(lines) * size * LINE_HEIGHT - size * (LINE_HEIGHT - 1)
    top = y0 + max(0.0, (y1 - y0 - total) / 2) if len(block['lines']) == 1 else y0
    for number, line in enumerate(lines):
        baseline = top + size * 0.88 + number * size * LINE_HEIGHT
        page.insert_text((x0, baseline), line, fontsize=size, fontname=font_name, color=block['color'])


def typeset(pdf_path, ocr_pages, client, font_path, destination, on_line, cancel_event=None,
            progress=(10, 95)):
    import pymupdf as fitz
    font = fitz.Font(fontfile=str(font_path)) if font_path else fitz.Font('china-s')
    low, high = progress
    translated_at = low + (high - low) * 0.9  # progress: translation first, then typesetting
    plans = translate_pages(ocr_pages, client, on_line, cancel_event, (low, translated_at))
    untouched = sum(source == translated for _, source, translated in plans if source)
    if untouched and getattr(getattr(client, 'progress', None), 'failed', False):
        on_line(f'⚠ {untouched} 页未翻译，保留原页面')
    with fitz.open(pdf_path) as doc:  # closed even on failure, so Windows can remove the work folder
        for page_number, (page_data, (chains, source, translated)) in enumerate(zip(ocr_pages, plans), 1):
            check_cancelled(cancel_event)
            on_line(f'@@PROGRESS {translated_at + (high - translated_at) * page_number / len(ocr_pages):.1f}')
            if source == translated:
                continue  # nothing was translated here: the page stays as it is
            blocks = page_data['blocks']
            label = f'第 {page_number}/{len(ocr_pages)} 页 · '
            texts = [''] * len(blocks)
            for chain, text in zip(chains, translated):
                for i, part in zip(chain, flow(text, [blocks[i] for i in chain], font)):
                    texts[i] = part
            on_line(f'▶ {label}正在排版')
            page = doc[page_data['index']]
            if page_data.get('native'):
                for block in blocks:
                    for (x0, y0, x1, y1), _ in block['lines']:
                        middle, half = (y0 + y1) / 2, (y1 - y0) / 4  # the middle band: never touches neighbours
                        page.add_redact_annot((x0, middle - half, x1, middle + half), fill=False)
                page.apply_redactions(images=0, graphics=0)
            font_name = 'china-s'
            if font_path:
                font_name = 'ocrzh'
                page.insert_font(fontname=font_name, fontfile=str(font_path))
            for block, text, scale in zip(blocks, texts, uniform_scales(blocks, texts, font)):
                draw(page, block, text, scale, font_name, font, not page_data.get('native'))
        check_cancelled(cancel_event)
        on_line('▶ 正在嵌入字体并保存 PDF')
        doc.subset_fonts()
        doc.save(destination, garbage=3, deflate=True)
    return destination
