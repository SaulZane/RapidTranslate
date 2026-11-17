import threading
from zipfile import ZipFile, ZIP_STORED

from lxml import etree
import pytest

from book_formats import XMLPart, translate_zip, translate_parts, parse_xml
from translation_client import TranslationCancelled


def structure(raw):
    tree = parse_xml(raw, 'test')
    for element in tree.iter():
        if isinstance(element.tag, str):
            element.text = None
        element.tail = None
    return etree.tostring(tree, method='c14n')


@pytest.mark.parametrize('format', ['epub', 'docx'])
def test_package_preserves_resources_structure_and_styles(format, request, tmp_path, client):
    source = request.getfixturevalue(format)
    before = source.read_bytes()
    output = tmp_path / ('translated.' + format)
    translate_zip(source, output, client, format, lambda _: None)
    assert source.read_bytes() == before
    with ZipFile(source) as src, ZipFile(output) as dst:
        assert set(src.namelist()) == set(dst.namelist())
        assert src.comment == dst.comment
        changed = []
        for name in src.namelist():
            a, b = src.read(name), dst.read(name)
            if a != b:
                changed.append(name)
                assert structure(a) == structure(b), name
                assert '译文：' in b.decode()
        assert changed
        if format == 'epub':
            assert dst.infolist()[0].filename == 'mimetype'
            assert dst.infolist()[0].compress_type == ZIP_STORED
            assert dst.read('OEBPS/styles/main.css') == src.read('OEBPS/styles/main.css')
            assert 'OEBPS/toc.ncx' in changed and 'OEBPS/nav.xhtml' in changed
            assert 'OEBPS/content.opf' not in changed
        else:
            assert {'word/header1.xml', 'word/footer1.xml', 'word/footnotes.xml', 'word/endnotes.xml', 'word/comments.xml'} <= set(changed)
            assert dst.read('word/styles.xml') == src.read('word/styles.xml')
            root = parse_xml(dst.read('word/document.xml'), 'document.xml')
            text = ''.join(root.getroot().itertext())
            assert '译文：November' not in text and '译文：Author name' not in text
    submitted = repr(client.groups)
    assert 'Do not translate' not in submitted and 'print(' not in submitted
    assert 'MERGEFIELD' not in submitted and 'Deleted words' not in submitted


def test_inline_context_order_and_whitespace():
    part = XMLPart('chapter.xhtml', b'<p>The <span><b>little cow</b> lives</span> on the <i>farm</i>.</p>', 'html')
    assert [slot.source for slot in part.groups[0]] == ['The ', 'little cow', ' lives', ' on the ', 'farm', '.']


def test_long_text_batched_and_controls_preserved(client):
    original = ' Hello\tworld\n' + 'This is a sentence. ' * 800 + '\r\nEnd '
    raw = ('<p>' + original.replace('\r', '&#13;') + '</p>').encode()
    part = XMLPart('long.xhtml', raw, 'html')
    translate_parts([part], client, lambda _: None)
    assert all(sum(len(text) for text in group) <= 1800 for group in client.groups)
    text = part.tree.getroot().text
    assert text.startswith(' 译文：Hello\t译文：world\n')
    assert text.endswith('\r\n译文：End ')
    assert text.count('This is a sentence.') == 800


def test_cancel_does_not_mutate_tree(client):
    part = XMLPart('a.xhtml', b'<p>Hello</p>', 'html')
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(TranslationCancelled):
        translate_parts([part], client, lambda _: None, cancelled)
    assert part.render() == b'<p>Hello</p>'
    assert not client.groups


def test_failed_translation_does_not_mutate_tree():
    part = XMLPart('a.xhtml', b'<p>Hello</p>', 'html')
    class BadClient:
        def translate(self, groups):
            return []
    with pytest.raises(ValueError):
        translate_parts([part], BadClient(), lambda _: None)
    assert part.render() == b'<p>Hello</p>'


def test_invalid_xml_not_repaired():
    with pytest.raises(ValueError, match='XML'):
        XMLPart('a.xhtml', b'<p>Bad <b>markup</p>', 'html')


def test_no_external_entity_access(client, tmp_path):
    secret = tmp_path / 'private.txt'
    secret.write_text('PRIVATE FILE CONTENT')
    raw = f'<!DOCTYPE p [<!ENTITY secret SYSTEM "{secret.as_uri()}">]><p>Hello &secret; again</p>'.encode()
    part = XMLPart('a.xhtml', raw, 'html')
    translate_parts([part], client, lambda _: None)
    assert 'PRIVATE FILE' not in repr(client.groups)
    assert b'&secret;' in part.render()


def test_non_utf8_encoding_and_cdata(client):
    raw = b'<?xml version="1.0" encoding="ISO-8859-1"?><p><![CDATA[Caf\xe9]]></p>'
    part = XMLPart('a.xhtml', raw, 'html')
    translate_parts([part], client, lambda _: None)
    output = part.render()
    assert '译文：Café' == parse_xml(output, 'out').getroot().text


def test_no_text_returns_identical_part(client):
    raw = '<p>中文 123 <br/>456</p>'.encode()
    part = XMLPart('a.xhtml', raw, 'html')
    translate_parts([part], client, lambda _: None)
    assert part.render() == raw


def test_epub_encryption_and_font_obfuscation(epub, tmp_path, client):
    with ZipFile(epub) as src:
        original = {name: src.read(name) for name in src.namelist()}
    for method, allowed in [('http://www.idpf.org/2008/embedding', True), ('http://example.com/drm', False)]:
        altered = tmp_path / ('allowed.epub' if allowed else 'protected.epub')
        with ZipFile(altered, 'w') as z:
            for name, data in original.items():
                z.writestr(name, data)
            z.writestr('META-INF/encryption.xml', f'<encryption><EncryptedData><EncryptionMethod Algorithm="{method}"/></EncryptedData></encryption>')
        output = tmp_path / 'out.epub'
        if allowed:
            translate_zip(altered, output, client, 'epub', lambda _: None)
        else:
            with pytest.raises(ValueError, match='加密'):
                translate_zip(altered, output, client, 'epub', lambda _: None)
