import os
import subprocess

import pytest

import engine


def test_ocr_pages_are_typeset_without_babeldoc(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    source = tmp_path / 'scan.pdf'
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(source)
    calls = []

    def fake_ocr(pdf, destination, *args):
        calls.append('ocr')
        return {'pages': [{'index': 0, 'blocks': []}]}

    monkeypatch.setattr(engine, 'ocr_pdf', fake_ocr)
    monkeypatch.setattr(engine, '_translate_pdf', lambda *args: calls.append('babeldoc'))
    monkeypatch.setattr(engine, 'babeldoc_ready', lambda *args: calls.append('runtime'))
    output = engine.translate(source, 'model', '', tmp_path, on_line=lambda _: None, ocr=True)
    assert output.name == 'scan_zh.pdf'
    assert calls == ['ocr']  # Every page was scanned, so BabelDOC is not needed.


def test_ocr_rejects_non_pdf(epub, tmp_path):
    with pytest.raises(ValueError, match='OCR'):
        engine.translate(epub, 'model', '', tmp_path, on_line=lambda _: None, ocr=True)


def block(lines, size=5.0):
    rect = [min(l[0][0] for l in lines), min(l[0][1] for l in lines),
            max(l[0][2] for l in lines), max(l[0][3] for l in lines)]
    return {'rect': rect, 'size': size, 'lines': lines, 'background': [1, 1, 1], 'color': [0, 0, 0],
            'free_below': 0}


def test_block_text_rejoins_hyphenated_lines():
    from ocr_layout import block_text
    lines = [[[0, 0, 70, 6], 'TAIPEI, Taiwan - In Decem-'], [[0, 6, 70, 12], 'ber 2023, a Chinese'],
             [[0, 12, 70, 18], 'executive met Mr.'], [[0, 18, 70, 24], 'Huang.']]
    assert block_text(block(lines)) == 'TAIPEI, Taiwan - In December 2023, a Chinese executive met Mr. Huang.'


def test_ocr_lines_merge_within_a_column_only():
    pytest.importorskip('numpy')
    import numpy as np
    import ocr_runner

    def box(x0, y0, x1, y1):
        return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)
    boxes, texts = [], []
    for row in range(6):  # two justified columns with a 4pt gutter at x=100
        y = row * 10
        boxes += [box(10, y, 98, y + 7), box(102, y, 190, y + 7)]
        texts += [f'left {row}', f'right {row}']
    boxes.append(box(10, 60, 40, 67))  # a line the detector split in two
    boxes.append(box(42, 60, 98, 67))
    texts += ['split', 'line']
    lines = ocr_runner.to_lines(boxes, texts, 1)
    assert sorted(text for _, text in lines if text.startswith('left'))[0] == 'left 0'
    assert not any('left' in text and 'right' in text for _, text in lines)
    assert any(text == 'split line' for _, text in lines)
    blocks = ocr_runner.to_blocks(lines)
    assert len(blocks) == 2 and all(len(b) in (6, 7) for b in blocks)


def test_typeset_uses_one_size_per_style_and_hides_source(tmp_path):
    fitz = pytest.importorskip('pymupdf')
    import ocr_layout

    class Client:
        def translate(self, groups):
            return [['港湾大桥将于明年春天开放，比原计划晚四年。工程师表示该结构跨度为1340米。' * 2] for _ in groups]

    source = tmp_path / 'scan.pdf'
    doc = fitz.open()
    doc.new_page(width=300, height=300)
    doc.save(source)
    blocks = [block([[[20, 20 + 7 * i, 120, 26 + 7 * i], 'The bridge will open next spring.'] for i in range(6)]),
              block([[[150, 20 + 7 * i, 250, 26 + 7 * i], 'Engineers say it spans 1,340 metres.'] for i in range(3)])]
    output = ocr_layout.typeset(source, [{'index': 0, 'blocks': blocks}], Client(), None, tmp_path / 'out.pdf',
                                lambda _: None)
    with fitz.open(output) as result:
        sizes = {round(span['size'], 2) for b in result[0].get_text('dict')['blocks']
                 for line in b.get('lines', []) for span in line['spans']}
        text = result[0].get_text()
    assert len(sizes) == 1 and min(sizes) >= 5 * ocr_layout.MIN_SCALE
    assert '港湾大桥' in text and 'bridge' not in text


@pytest.fixture
def scanned_pdf(tmp_path):
    fitz = pytest.importorskip('pymupdf')
    text = ('The Harbor Bridge will open next spring, four years behind schedule. '
            'Engineers say the structure spans 1,340 metres.')
    page_doc = fitz.open()
    page_doc.new_page(width=612, height=792).insert_htmlbox(
        fitz.Rect(54, 54, 558, 300), f'<h1>Harbor Bridge</h1><p style="font-family:serif">{text}</p>'
        '<p style="text-align:center"><i>E</i> = <i>m</i><i>c</i><sup>2</sup> + &#8747; <i>x</i></p>')
    pixmap = page_doc[0].get_pixmap(dpi=150)
    scan = fitz.open()
    scan.new_page(width=612, height=792).insert_image(fitz.Rect(0, 0, 612, 792), stream=pixmap.tobytes('png'))
    path = tmp_path / 'scan.pdf'
    scan.save(path)
    return path


def test_ocr_runner_finds_paragraphs(scanned_pdf, tmp_path):
    python = engine.find_babeldoc_python()
    if python is None or subprocess.run([str(python), '-c', 'import rapidocr'], capture_output=True).returncode:
        pytest.skip('RapidOCR runtime is not installed')
    import json
    output = tmp_path / 'ocr.json'
    subprocess.run([str(python), str(engine.OCR_RUNNER), str(scanned_pdf), str(output)], check=True,
                   capture_output=True, timeout=900, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    from ocr_layout import block_text
    blocks = json.loads(output.read_text(encoding='utf-8'))['pages'][0]['blocks']
    texts = [block_text(b) for b in blocks]
    assert 'Harbor Bridge' in texts[0]
    body = next(t for t in texts if 'four years behind schedule' in t)
    assert '1,340 metres' in body  # The wrapped paragraph is one block.
    assert not any('=' in t for t in texts)  # Equations stay as the scanned image.


def test_native_columns_and_document_progress(tmp_path):
    fitz = pytest.importorskip('pymupdf')
    pytest.importorskip('numpy')
    from ocr_runner import native_pages
    from ocr_layout import typeset, block_text

    source = tmp_path / 'native.pdf'
    with fitz.open() as doc:
        for _ in range(2):
            page = doc.new_page(width=400, height=300)
            for x, word in [(20, 'Left'), (220, 'Right')]:
                for row in range(4):
                    page.insert_text((x, 40 + 13 * row), f'{word} column sentence.', fontsize=10)
        doc.save(source)
    pages = native_pages(source)
    assert len(pages) == 2
    assert all(len(p['blocks']) == 2 for p in pages)
    assert all(not ('Left' in block_text(b) and 'Right' in block_text(b))
               for p in pages for b in p['blocks'])

    class Client:
        def translate(self, groups):
            return [['中文段落测试。'] for _ in groups]

    logs = []
    output = typeset(source, pages, Client(), None, tmp_path / 'result.pdf', logs.append)
    values = [float(line.split()[1]) for line in logs if line.startswith('@@PROGRESS')]
    assert values == sorted(values)
    assert 10 < values[0] < values[-1] < 100
    assert any('第 2/2 页' in line for line in logs)
    with fitz.open(output) as doc:
        assert all('column' not in page.get_text() and '中文' in page.get_text() for page in doc)


def test_layout_option_uses_native_paragraphs(tmp_path, monkeypatch):
    fitz = pytest.importorskip('pymupdf')
    source = tmp_path / 'paper.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((50, 50), 'Native column text.', fontsize=10)
        doc.save(source)
    calls = []
    monkeypatch.setattr(engine, 'babeldoc_ready', lambda *args: 'python')
    monkeypatch.setattr(engine, '_translate_pdf', lambda *args: calls.append('babeldoc'))

    def fake_stream(cmd, *args):
        calls.append(cmd[-3:])
        Path(cmd[3]).write_text('{"pages": [{"index": 0, "native": true, "blocks": []}]}', encoding='utf-8')
        return 0
    from pathlib import Path
    monkeypatch.setattr(engine, 'stream', fake_stream)
    lines = []
    output = engine.translate(source, 'model', '', tmp_path, on_line=lines.append, layout=True)
    assert output.name == 'paper_zh.pdf'
    assert calls[0][1] == '--native' and 'babeldoc' not in calls
    values = [float(line.split()[1]) for line in lines if line.startswith('@@PROGRESS')]
    assert values == sorted(values) and values[-1] == 100


def test_typeset_failure_releases_the_pdf(tmp_path):
    fitz = pytest.importorskip('pymupdf')
    import ocr_layout

    class Client:
        def translate(self, groups):
            raise RuntimeError('翻译失败')

    source = tmp_path / 'scan.pdf'
    with fitz.open() as doc:
        doc.new_page(width=300, height=300)
        doc.save(source)
    with pytest.raises(RuntimeError, match='翻译失败'):
        ocr_layout.typeset(source, [{'index': 0, 'blocks': [block([[[20, 20, 120, 26], 'The bridge.']])]}],
                           Client(), None, tmp_path / 'out.pdf', lambda _: None)
    source.unlink()  # Windows refuses this while the document is still open.


def test_sentence_broken_by_a_column_is_translated_whole(tmp_path):
    fitz = pytest.importorskip('pymupdf')
    import ocr_layout

    def column(x, y, texts):
        return block([[[x, y + 7 * i, x + 100, y + 6 + 7 * i], text] for i, text in enumerate(texts)])
    blocks = [
        block([[[20, 20, 230, 44], 'Headline']], size=20.0),
        column(20, 60, ['  The ground stop eclipsed', 'previous air traffic communica-']),
        column(130, 60, ['tions equipment failures.', 'Officials said it was fixed.']),
        column(130, 80, ['A new paragraph starts', 'here and ends here.']),
        column(20, 120, ['This sentence stops before a', 'jump line and so stays open in']),
        block([[[30, 135, 110, 141], 'Continued on Page 14']]),
    ]
    blocks[1]['lines'][0][0][0] += 5  # first-line indent
    blocks[3]['lines'][0][0][0] += 5
    assert ocr_layout.link_columns(blocks) == [[0], [1, 2], [3], [4], [5]]

    seen = []

    class Client:
        def translate(self, groups):
            seen.extend(group[0] for group in groups)
            return [['地面停飞的影响超过了此前空中交通通信设备故障造成的后果。官员们表示问题已经解决。' if 'ground stop' in group[0] else '短句。']
                    for group in groups]

    source = tmp_path / 'paper.pdf'
    with fitz.open() as doc:
        doc.new_page(width=300, height=300)
        doc.save(source)
    logs = []
    output = ocr_layout.typeset(source, [{'index': 0, 'blocks': blocks}], Client(), None, tmp_path / 'out.pdf',
                                logs.append)
    assert 'previous air traffic communications equipment failures. Officials' in ' '.join(seen)
    assert any('跨栏' in line for line in logs)
    with fitz.open(output) as doc:
        left = doc[0].get_text(clip=(15, 55, 125, 80)).replace('\n', '')
        right = doc[0].get_text(clip=(125, 55, 240, 78)).replace('\n', '')
    assert left.startswith('地面停飞') and right and (left + right).endswith('问题已经解决。')
