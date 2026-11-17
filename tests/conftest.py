import base64
import json
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zipfile import ZipFile, ZIP_STORED, ZIP_DEFLATED
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR1sAAAAASUVORK5CYII=')
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'


class FakeTranslator:
    def __init__(self):
        self.groups = []

    def translate(self, groups):
        self.groups.extend(groups)
        return [['译文：' + text for text in group] for group in groups]


@pytest.fixture
def client():
    return FakeTranslator()


def make_epub(path):
    files = {
        'mimetype': b'application/epub+zip',
        'META-INF/container.xml': b'''<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>''',
        'OEBPS/content.opf': b'''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">urn:uuid:00000000-0000-0000-0000-000000000001</dc:identifier><dc:title>Farm animals</dc:title><dc:language>en</dc:language><dc:creator>Test Author</dc:creator><meta property="dcterms:modified">2026-11-10T00:00:00Z</meta></metadata><manifest><item id="ch" href="Text/chapter%201.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="css" href="styles/main.css" media-type="text/css"/><item id="img" href="images/cow.png" media-type="image/png"/><item id="svg" href="images/label.svg" media-type="image/svg+xml"/></manifest><spine toc="ncx"><itemref idref="ch"/></spine></package>''',
        'OEBPS/Text/chapter 1.xhtml': b'''<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en"><head><title>Farm animals</title><link rel="stylesheet" href="../styles/main.css"/></head><body><h1 id="title">Farm animals</h1><p class="lead">The <span><b>little cow</b> lives</span> on the <i>farm</i>. <a href="#title">Read again</a></p><p>Line one<br/>Line two<!--comment--> and more.</p><table><tr><th>Animal</th><td>Milk</td></tr></table><p><img src="../images/cow.png" alt="Cow" width="100" height="100"/></p><p translate="no">Do not translate</p><pre>print("Cow")</pre><p>123</p></body></html>''',
        'OEBPS/nav.xhtml': b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol><li><a href="Text/chapter%201.xhtml#title">Farm animals</a></li></ol></nav></body></html>''',
        'OEBPS/toc.ncx': b'''<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="test"/></head><docTitle><text>Farm animals</text></docTitle><navMap><navPoint id="one" playOrder="1"><navLabel><text>Chapter one</text></navLabel><content src="Text/chapter%201.xhtml#title"/></navPoint></navMap></ncx>''',
        'OEBPS/styles/main.css': b'body { margin: 1em; } .lead { font-size: 120%; color: #123456; } b { font-weight: bold; } i { font-style: italic; }',
        'OEBPS/images/cow.png': PNG,
        'OEBPS/images/label.svg': b'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="30"><text x="5" y="20" font-size="12">Cow</text><path d="M0,0 L10,10"/></svg>''',
        'extra.bin': b'\x00\xff\x80untouched',
    }
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data, compress_type=ZIP_STORED if name == 'mimetype' else ZIP_DEFLATED)
        z.comment = b'Original archive comment'
    return path


def make_docx(path):
    parts = {'document': 'document.main', 'header1': 'header', 'footer1': 'footer',
             'footnotes': 'footnotes', 'endnotes': 'endnotes', 'comments': 'comments'}
    overrides = ''.join(f'<Override PartName="/word/{name}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"/>' for name, kind in parts.items())
    files = {
        '[Content_Types].xml': f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="png" ContentType="image/png"/>{overrides}</Types>'.encode(),
        '_rels/.rels': b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/document.xml': f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p w:rsidR="12345678"><w:pPr><w:pStyle w:val="Heading1"/><w:spacing w:after="200"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>Farm animals</w:t></w:r></w:p><w:p><w:r><w:t xml:space="preserve">The </w:t></w:r><w:r><w:rPr><w:i/><w:color w:val="112233"/></w:rPr><w:t>little cow</w:t></w:r><w:hyperlink r:id="rIdLink"><w:r><w:t xml:space="preserve"> lives on the farm.</w:t></w:r></w:hyperlink><w:r><w:tab/><w:br w:type="page"/></w:r></w:p><w:tbl><w:tblPr><w:tblW w:w="4000" w:type="dxa"/></w:tblPr><w:tr><w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>Milk</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:fldSimple w:instr="DATE"><w:r><w:t>November</w:t></w:r></w:fldSimple><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>MERGEFIELD Author</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>Author name</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r><w:del><w:r><w:delText>Deleted words</w:delText></w:r></w:del></w:p><w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>'''.encode(),
        'word/_rels/document.xml.rels': b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/animals" TargetMode="External"/></Relationships>',
        'word/styles.xml': f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="Heading1"><w:rPr><w:b/></w:rPr></w:style></w:styles>'.encode(),
        'word/numbering.xml': f'<w:numbering xmlns:w="{W}"/>'.encode(),
        'word/media/cow.png': PNG,
        'word/fontTable.xml': f'<w:fonts xmlns:w="{W}"/>'.encode(),
    }
    for part, root in [('header1','hdr'), ('footer1','ftr'), ('footnotes','footnotes'), ('endnotes','endnotes'), ('comments','comments')]:
        files[f'word/{part}.xml'] = f'<w:{root} xmlns:w="{W}"><w:p><w:r><w:t>{part} text</w:t></w:r></w:p></w:{root}>'.encode()
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


@pytest.fixture
def epub(tmp_path):
    return make_epub(tmp_path / 'book.epub')


@pytest.fixture
def docx(tmp_path):
    return make_docx(tmp_path / 'book.docx')


@pytest.fixture
def api_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((dict(self.headers), body))
            prompt = body['messages'][-1]['content']
            source = prompt.split('## Here is the input:\n', 1)[1]
            root = ET.fromstring(source)
            for node in root.iter('s'):
                node.text = '译文：' + (node.text or '')
            response = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': ET.tostring(root, encoding='unicode')}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
