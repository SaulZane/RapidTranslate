from pathlib import Path
import os
import subprocess
import sys
from zipfile import ZipFile

from lxml import etree
import pytest

import engine
from book_formats import XMLPart, translate_parts, translate_zip
from code_comments import comment_ranges
from ebook_chapters import index_epub, select_chapters
from page_selection import parse_pages, extract_pdf_pages, extract_docx_pages
from prompt_templates import combine_requirements, TEMPLATES
from translation_client import TranslationClient

SAMPLES = Path(__file__).resolve().parents[1] / 'samples' / 'input'


@pytest.mark.parametrize('value', ['0', '3-1', '1,,3', 'abc', '1-', '-2', '2.5', '1;3'])
def test_bad_page_ranges(value):
    with pytest.raises(ValueError): parse_pages(value, 12)


def test_combined_page_ranges():
    assert parse_pages('6-8,9,11', 12) == [6, 7, 8, 9, 11]
    assert parse_pages('3,1-2,2， 5', 8) == [1, 2, 3, 5]
    with pytest.raises(ValueError): parse_pages('1,5', 4)


def test_pdf_partial_output(tmp_path):
    from pypdf import PdfReader
    source = SAMPLES / 'sample_book.pdf'
    target = tmp_path / 'selected.pdf'
    assert extract_pdf_pages(source, target, '1,3') == [1, 3]
    reader = PdfReader(target)
    assert len(reader.pages) == 2
    assert 'Signals and materials' in reader.pages[0].extract_text()
    assert 'A first Python function' in reader.pages[1].extract_text()
    assert 'Silver Bay' not in ''.join(p.extract_text() for p in reader.pages)


@pytest.mark.skipif(os.name != 'nt', reason='Native Word is Windows-only')
@pytest.mark.parametrize('pages, expected', [('2', [2]), ('1,3', [1, 3])])
def test_word_physical_page_selection(tmp_path, pages, expected):
    pytest.importorskip('win32com.client')
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r'Word.Application\CLSID').Close()
    except OSError:
        pytest.skip('Word is not installed')
    source, output = SAMPLES / 'sample_book.docx', tmp_path / 'selected.docx'
    original = source.read_bytes()
    assert extract_docx_pages(source, output, pages) == expected
    assert source.read_bytes() == original
    with ZipFile(output) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
        text = ''.join(root.itertext())
        assert ('Silver Bay' in text) == (2 in expected)
        assert ('Signals and materials' in text) == (1 in expected)
        assert ('A first Python function' in text) == (3 in expected)
        assert ('Mira Stone' in text) == (2 in expected)


@pytest.mark.parametrize('chosen, included, excluded', [
    (['1.1'], 'Zyphron', 'The signal path contains'),
    (['1.2'], 'The signal path contains', 'Zyphron'),
    (['1'], 'Zyphron', 'Mira Stone'),
    (['1.1', '2'], 'Mira Stone', 'def greet'),
    (['3'], 'def greet', 'Mira Stone'),
])
def test_epub_exact_anchor_chapter_selection(chosen, included, excluded, tmp_path, client):
    source = SAMPLES / 'sample_book.epub'
    output = tmp_path / 'selected.epub'
    translate_zip(source, output, client, 'epub', lambda _: None, chapters=chosen)
    with ZipFile(output) as archive:
        book = index_epub(archive)
        content = ''.join(''.join(etree.fromstring(archive.read(name)).itertext()) for name in book.spine)
        assert included in content
        assert excluded not in content
        # Every surviving navigation entry points to a surviving chapter anchor.
        for chapter in book.chapters:
            assert chapter.name in book.spine
            if chapter.fragment:
                root = etree.fromstring(archive.read(chapter.name))
                assert root.xpath('//*[@id=$anchor or @name=$anchor]', anchor=chapter.fragment)
    submitted = repr(client.groups)
    assert excluded not in submitted


def test_invalid_chapter_selection():
    with ZipFile(SAMPLES / 'sample_book.epub') as archive:
        for ids in ([], ['99']):
            with pytest.raises(ValueError): select_chapters(index_epub(archive), ids)


def test_templates_manual_priority():
    for key in ('science', 'fiction', 'tutorial', 'magazine'):
        combined = combine_requirements('Mira Stone 译为米拉·斯通', key)
        assert '用户手工要求（与模板冲突时优先）' in combined
        assert TEMPLATES[key][1] in combined
    assert combine_requirements('manual', 'none') == 'manual'


@pytest.mark.parametrize('language, code', [
    ('python', 'def greet(name):\n    # Print a friendly greeting\n    return "Hello, " + name  # Keep the original value'),
    ('javascript', 'const url = "https://example.com"; // Read the source\n/* Keep the value */\nconsole.log(url);'),
    ('cpp', '#include <iostream>\nint x = 1; // Read the sensor\nconst char* s = "/* not a comment */";'),
])
def test_tutorial_only_translates_comments(language, code, client):
    root = etree.Element('pre')
    child = etree.SubElement(root, 'code', attrib={'class': 'language-' + language})
    child.text = code
    part = XMLPart('code.xhtml', etree.tostring(root), 'html', 'tutorial')
    translate_parts([part], client, lambda _: None)
    translated = ''.join(part.tree.getroot().itertext())
    assert '译文：' in translated
    # Fake translation adds a prefix only; after stripping it, all code bytes agree.
    assert translated.replace('译文：', '') == code
    assert 'https://example.com' not in repr(client.groups)
    assert 'not a comment' not in repr(client.groups)
    assert '#include' not in repr(client.groups)


def test_code_is_unchanged_without_tutorial(client):
    raw = b'<pre><code class="language-python">x = "Hello" # Keep this comment</code></pre>'
    part = XMLPart('code.xhtml', raw, 'html')
    translate_parts([part], client, lambda _: None)
    assert part.render() == raw
    assert not client.groups


def test_docx_code_runs_with_breaks(client):
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    raw = f'<w:p xmlns:w="{ns}"><w:pPr><w:pStyle w:val="Code"/></w:pPr><w:r><w:t>def greet(name):</w:t><w:br/><w:t xml:space="preserve">    # Print a greeting</w:t><w:br/><w:t xml:space="preserve">    return "Hello"</w:t></w:r></w:p>'.encode()
    part = XMLPart('document.xml', raw, 'docx', 'tutorial')
    translate_parts([part], client, lambda _: None)
    assert client.groups == [['Print a greeting']]
    output = part.render().decode()
    assert 'def greet(name):' in output and 'return "Hello"' in output
    assert '# 译文：Print a greeting' in output


@pytest.mark.parametrize('extension, selected, expected', [('epub', ['1.1'], 'Zyphron'), ('azw3', ['2'], 'Mira Stone'), ('mobi', ['4'], 'Mira Stone')])
def test_partial_dispatch_with_api(extension, selected, expected, api_server, monkeypatch, tmp_path):
    if extension != 'epub' and engine.find_calibre_debug() is None:
        pytest.skip('Calibre is required')
    endpoint, requests = api_server
    monkeypatch.setattr(engine, 'TranslationClient', lambda m, r, log, cancel, **options: TranslationClient(m, r, log, cancel, endpoint, **options))
    output = engine.translate(SAMPLES / ('sample_book.' + extension), 'mock', '指定要求', tmp_path,
                              on_line=lambda _: None, chapters=selected, template='fiction')
    assert output.suffix == '.' + extension
    prompts = '\n'.join(body['messages'][0]['content'] for _, body in requests)
    assert expected in prompts
    assert '语言优美流畅' in prompts
    assert '指定要求' in prompts
    if expected == 'Mira Stone':
        assert 'Zyphron' not in prompts


def test_pdf_code_keeps_layout_and_executable_text(tmp_path, client):
    import pymupdf as fitz
    from pdf_code import protect_code, restore_code
    source = SAMPLES / 'sample_book.pdf'
    protected, overlay, regions = protect_code(source, tmp_path, client, 'tutorial', lambda _: None, None)
    assert len(regions) == 1
    assert client.groups == [['Print a friendly greeting'], ['Keep the original value']]
    with fitz.open(protected) as doc:
        assert 'def greet' not in doc[2].get_text()
    result = restore_code(protected, overlay, regions, tmp_path)
    with fitz.open(result) as doc:
        text = doc[2].get_text()
        assert 'def greet(name):' in text
        assert 'return "Hello, " + name' in text
        assert '译文' in text
        assert len(doc) == 3
