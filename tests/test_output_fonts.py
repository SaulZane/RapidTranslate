from pathlib import Path
from zipfile import ZipFile

from lxml import etree
import pytest

from book_formats import XMLPart, translate_parts, translate_zip
from document_fonts import apply_fonts
from font_preferences import BODY_FONTS


@pytest.mark.parametrize('key', list(BODY_FONTS))
def test_docx_output_font_preserves_formatting_and_code(key, tmp_path, client):
    source = Path(__file__).resolve().parents[1] / 'samples/input/sample_book.docx'
    target = tmp_path / 'translated.docx'
    translate_zip(source, target, client, 'docx', lambda _: None, template='tutorial', body_font=key)
    with ZipFile(source) as original, ZipFile(target) as output:
        assert original.read('word/styles.xml') == output.read('word/styles.xml')
        for name in original.namelist():
            if name.startswith('word/media/'):
                assert original.read(name) == output.read(name)
        root = etree.fromstring(output.read('word/document.xml'))
        w = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        runs = root.xpath('//w:r[w:t]', namespaces=w)
        body = next(run for run in runs if '译文：' in ''.join(run.itertext()) and 'greet' not in ''.join(run.itertext()))
        font = body.find('w:rPr/w:rFonts', w)
        assert font.get(f'{{{w["w"]}}}eastAsia') == BODY_FONTS[key][0]
        code = next(run for run in runs if 'def greet(name):' in ''.join(run.itertext()))
        assert code.find('w:rPr/w:rFonts', w).get(f'{{{w["w"]}}}ascii') == 'Consolas'
        assert root.xpath('//w:b', namespaces=w) and root.xpath('//w:i', namespaces=w)
        assert root.xpath('//w:tbl', namespaces=w)
        assert 'return "Hello, " + name' in ''.join(root.itertext())


def test_html_font_does_not_modify_layout_or_executable_code(client):
    raw = b'<html><body><p style="margin: 2em; color: red">A <b>bold</b> statement.</p><pre style="padding: 1em"><code>x = "Hello" # Keep this comment</code></pre></body></html>'
    part = XMLPart('sample.xhtml', raw, 'html', 'tutorial')
    translate_parts([part], client, lambda _: None)
    apply_fonts(part, 'kai')
    root = etree.fromstring(part.render())
    paragraph = root.find('.//p')
    assert 'margin: 2em; color: red' in paragraph.get('style')
    assert 'KaiTi' in paragraph.get('style')
    assert root.find('.//b') is not None
    code = root.find('.//code')
    assert 'Consolas' in code.get('style') and 'monospace' in code.get('style')
    assert code.text.startswith('x = "Hello" # ')
    assert '译文：' in code.text
    assert root.find('.//pre').get('style') == 'padding: 1em'


def test_font_only_code_update_is_serialized():
    part = XMLPart('code.xhtml', b'<pre><code>print("Hello")</code></pre>', 'html')
    apply_fonts(part, 'song')
    output = part.render()
    assert b'Consolas' in output
    assert b'print("Hello")' in output


def test_installed_babeldoc_uses_requested_faces():
    """Exercise the pinned BabelDoc runtime and its actual font mapper."""
    import subprocess
    import engine
    python = engine.find_babeldoc_python()
    if python is None:
        pytest.skip('BabelDoc is not installed')
    result = subprocess.run([str(python), '-X', 'utf8', '-c', '''
import os, json
from types import SimpleNamespace
from importlib.metadata import version
import bd_runner
from babeldoc import rust_run_context, _rust_il
from font_preferences import BODY_FONTS
assert version('BabelDOC') == '0.7.1'
for key, entry in BODY_FONTS.items():
    os.environ['INDEX_TRANSLATE_BODY_FONT'] = key
    family, paths, checksums = rust_run_context._font_assets(SimpleNamespace(lang_out='zh'))
    mapper, styles = _rust_il.font_mapper_handle_with_source_styles(
        json.dumps(family), json.dumps(paths), json.dumps(checksums), entry[3])
    for bold, italic, role in [(False, False, 'body'), (True, False, 'bold'),
                               (False, True, 'italic'), (True, True, 'bold')]:
        for character in ('中', 'A'):
            actual = mapper.mapped_font_id(bold, italic, False, False, character)
            assert actual.startswith('index_' + key + '_') and actual.endswith('_' + role), actual
    assert styles[family['normal'][1]]['bold']
'''],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_parser_preserves_standard_font_emphasis(tmp_path):
    import subprocess
    import engine
    python = engine.find_babeldoc_python()
    if python is None:
        pytest.skip('BabelDoc is not installed')
    result = subprocess.run([str(python), '-X', 'utf8', '-c', '''
import sys, pymupdf
from babeldoc_fonts import embed_standard_fonts
from babeldoc.rust_parser_service import parse_pdf_to_il
with pymupdf.open('samples/input/sample_book.pdf') as original, pymupdf.open() as nested:
    page = nested.new_page(width=original[0].rect.width, height=original[0].rect.height)
    page.show_pdf_page(page.rect, original, 0)
    before = page.get_text('dict')
    embed_standard_fonts(nested)
    nested.save(sys.argv[1])
with pymupdf.open(sys.argv[1]) as rendered:
    after = rendered[0].get_text('dict')
    spans = lambda p: [(s['text'], s['origin']) for b in p['blocks']
                      for l in b.get('lines', []) for s in l['spans']]
    assert spans(before) == spans(after)
document = parse_pdf_to_il(sys.argv[1])
fonts = document.page[0].pdf_font[:]
for xobject in document.page[0].pdf_xobject:
    fonts.extend(xobject.pdf_font)
by_name = {font.name: font for font in fonts}
assert by_name['Helvetica-Bold'].bold
assert by_name['Helvetica-Oblique'].italic
assert by_name['Courier'].monospace
''', str(tmp_path / 'embedded.pdf')], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_indirect_page_resources_are_inlined(tmp_path):
    import subprocess
    import engine
    python = engine.find_babeldoc_python()
    if python is None:
        pytest.skip('BabelDoc is not installed')
    result = subprocess.run([str(python), '-X', 'utf8', '-c', '''
import pymupdf
from babeldoc_fonts import inline_page_resources
document = pymupdf.open()
document.new_page().insert_text((72, 72), 'Hello world')
page = document[0]
kind, value = document.xref_get_key(page.xref, 'Resources')
if kind != 'xref':
    resources = document.get_new_xref()
    document.update_object(resources, value)
    document.xref_set_key(page.xref, 'Resources', f'{resources} 0 R')
assert document.xref_get_key(page.xref, 'Resources')[0] == 'xref'
inline_page_resources(document)
kind, value = document.xref_get_key(page.xref, 'Resources')
assert kind == 'dict' and '/Font' in value, (kind, value)
assert 'Hello world' in document[0].get_text()
'''], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
