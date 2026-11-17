"""Generate original, redistributable fixtures for translation and layout QA."""
from pathlib import Path
from zipfile import ZipFile, ZIP_STORED, ZIP_DEFLATED
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pagesizes import A4

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'samples' / 'input'
OUT.mkdir(parents=True, exist_ok=True)

CHAPTERS = [
    ('Signals and materials', [
        'This short book tests the translation of scientific writing, fiction and tutorials while retaining document layout.',
        'A sensor converts a physical signal into a measurable value. The Zyphron interface is an experimental term; do not invent a Chinese explanation for it.',
        'The signal path contains three stages: input, processing and output. Bold and italic words identify terms that should keep their emphasis.',
    ]),
    ('A letter from Silver Bay', [
        'Mira Stone reached Silver Bay before sunrise. A warm light shone through the library window, and she slowed her steps to listen to the sea.',
        'Inside the library, Mira Stone found a letter addressed to her. It carried the familiar scent of cedar and a promise to return to Silver Bay.',
        'She folded the letter carefully. For the first time in years, the quiet town felt like home.',
    ]),
    ('A first Python function', [
        'This tutorial shows how to write a small function. Keep executable code and string literals unchanged, but translate the comments.',
        'The function accepts a name and returns a greeting. Run the same function with another name to check the result.',
    ]),
]
CODE = 'def greet(name):\n    # Print a friendly greeting\n    return "Hello, " + name  # Keep the original value'


def make_image():
    image = Image.new('RGB', (600, 140), 'white')
    draw = ImageDraw.Draw(image)
    for i, x in enumerate((20, 220, 420), 1):
        draw.rounded_rectangle((x, 25, x + 150, 115), radius=10, outline='#263b53', width=3, fill='#edf3f9')
        draw.text((x + 70, 63), str(i), fill='#263b53')
        if i < 3:
            draw.line((x + 150, 70, x + 190, 70), fill='#263b53', width=3)
            draw.polygon([(x + 190, 70), (x + 180, 64), (x + 180, 76)], fill='#263b53')
    image.save(OUT / 'signal.png')


def make_docx():
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.27), Inches(11.69)
    section.top_margin = section.bottom_margin = Inches(0.8)
    section.left_margin = section.right_margin = Inches(0.85)
    normal = doc.styles['Normal']
    normal.font.name, normal.font.size = 'Calibri', Pt(11)
    normal.paragraph_format.space_after = Pt(9)
    normal.paragraph_format.line_spacing = 1.15
    for name in ['Title', 'Heading 1', 'Heading 2']:
        doc.styles[name].font.color.rgb = RGBColor(0, 0, 0)
    code_style = doc.styles.add_style('Code', 1)
    code_style.font.name, code_style.font.size = 'Consolas', Pt(10)
    code_style.paragraph_format.space_after = Pt(0)
    header = section.header.paragraphs[0]
    header.text = 'Book translation sample'
    header.style = doc.styles['Caption']
    footer = section.footer.paragraphs[0]
    footer.add_run('Page ')
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'PAGE')
    footer._p.append(field)
    for index, (title, paragraphs) in enumerate(CHAPTERS):
        if index:
            doc.add_page_break()
        doc.add_paragraph(title, 'Title' if index == 0 else 'Heading 1')
        for text in paragraphs:
            doc.add_paragraph(text)
        if index == 0:
            p = doc.add_paragraph('Preserve the emphasis on ')
            p.add_run('input').bold = True
            p.add_run(' and ')
            p.add_run('output').italic = True
            p.add_run('.')
            doc.add_picture(str(OUT / 'signal.png'), width=Inches(5.4))
            doc.add_paragraph('Figure 1 The signal path', 'Caption')
            table = doc.add_table(rows=1, cols=2)
            table.style = 'Light Shading Accent 1'
            table.cell(0, 0).text, table.cell(0, 1).text = 'Stage', 'Purpose'
            for a, b in [('Input', 'Read the sensor'), ('Output', 'Display the value')]:
                cells = table.add_row().cells
                cells[0].text, cells[1].text = a, b
        if index == 2:
            doc.add_paragraph(CODE, 'Code')
    doc.save(OUT / 'sample_book.docx')


def make_pdf():
    from reportlab.lib.utils import simpleSplit
    canvas = Canvas(str(OUT / 'sample_book.pdf'), pagesize=A4)
    width, height = A4
    for index, (title, paragraphs) in enumerate(CHAPTERS):
        canvas.setFont('Helvetica', 9)
        canvas.drawString(55, height - 38, 'Book translation sample')
        canvas.setFont('Helvetica-Bold', 21)
        canvas.drawString(55, height - 90, title)
        y = height - 130
        for paragraph in paragraphs:
            canvas.setFont('Helvetica', 11)
            for line in simpleSplit(paragraph, 'Helvetica', 11, width - 110):
                canvas.drawString(55, y, line)
                y -= 17
            y -= 14
        if index == 0:
            canvas.setFont('Helvetica-Bold', 11)
            canvas.drawString(55, y, 'Input')
            canvas.setFont('Helvetica-Oblique', 11)
            canvas.drawString(130, y, 'Output')
            canvas.drawImage(str(OUT / 'signal.png'), 55, y - 120, width=440, height=103)
        if index == 2:
            canvas.setFont('Courier', 10)
            for line in CODE.splitlines():
                canvas.drawString(55, y, line)
                y -= 16
        canvas.setFont('Helvetica', 9)
        canvas.drawRightString(width - 55, 35, str(index + 1))
        canvas.showPage()
    canvas.save()


def make_epub():
    chapters = {}
    for i, (title, paragraphs) in enumerate(CHAPTERS, 1):
        body = f'<h1 id="chapter{i}">{escape(title)}</h1>'
        for j, text in enumerate(paragraphs):
            if i == 1 and j == 1:
                body += '<h2 id="terms">Scientific terminology</h2>'
            if i == 1 and j == 2:
                body += '<h2 id="signal">The signal path</h2>'
            body += f'<p>{escape(text)}</p>'
        if i == 1:
            body += '<p>Preserve <b>input</b> and <i>output</i>.</p><img src="images/signal.png" alt="Signal diagram"/><table><tr><th>Stage</th><th>Purpose</th></tr><tr><td>Input</td><td>Read the sensor</td></tr><tr><td>Output</td><td>Display the value</td></tr></table>'
        if i == 3:
            body += '<pre><code class="language-python">' + escape(CODE) + '</code></pre>'
        chapters[f'chapter{i}.xhtml'] = f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en"><head><title>{escape(title)}</title><link rel="stylesheet" href="style.css"/></head><body>{body}</body></html>'
    toc = '<li><a href="chapter1.xhtml#chapter1">Signals and materials</a><ol><li><a href="chapter1.xhtml#terms">Scientific terminology</a></li><li><a href="chapter1.xhtml#signal">The signal path</a></li></ol></li><li><a href="chapter2.xhtml#chapter2">A letter from Silver Bay</a></li><li><a href="chapter3.xhtml#chapter3">A first Python function</a></li>'
    nav = '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><h1>Contents</h1><ol>' + toc + '</ol></nav></body></html>'
    manifest = ''.join(f'<item id="ch{i}" href="chapter{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(1, 4))
    spine = ''.join(f'<itemref idref="ch{i}"/>' for i in range(1, 4))
    opf = f'''<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">urn:uuid:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa</dc:identifier><dc:title>Book translation sample</dc:title><dc:creator>Translation test</dc:creator><dc:language>en</dc:language><meta property="dcterms:modified">2026-11-10T00:00:00Z</meta></metadata><manifest>{manifest}<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="css" href="style.css" media-type="text/css"/><item id="image" href="images/signal.png" media-type="image/png"/></manifest><spine>{spine}</spine></package>'''
    with ZipFile(OUT / 'sample_book.epub', 'w', ZIP_DEFLATED) as z:
        z.writestr('mimetype', 'application/epub+zip', compress_type=ZIP_STORED)
        z.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr('OEBPS/content.opf', opf)
        z.writestr('OEBPS/nav.xhtml', nav)
        z.writestr('OEBPS/style.css', 'body { margin: 5%; font-family: serif; line-height: 1.5; } h1 { font-size: 1.6em; } h2 { font-size: 1.25em; } img { max-width: 100%; } table { border-collapse: collapse; width: 100%; } td, th { border: 1px solid #aaa; padding: .4em; } pre { white-space: pre-wrap; font-family: monospace; padding: 1em; background: #f2f2f2; }')
        z.write(OUT / 'signal.png', 'OEBPS/images/signal.png')
        for name, content in chapters.items():
            z.writestr('OEBPS/' + name, content)


if __name__ == '__main__':
    make_image()
    make_docx()
    make_pdf()
    make_epub()
    print(OUT)
