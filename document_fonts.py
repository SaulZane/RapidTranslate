"""Apply requested output fonts without changing paragraph/run/layout properties."""
from lxml import etree

from font_preferences import CODE_CSS, body_css, font_choice


def apply_fonts(part, key):
    if key is None or part.kind == 'ncx':
        return
    label, family, _, _ = font_choice(key)
    changed = False
    seen = set()
    for group in part.groups:
        for slot in group:
            code = slot.ranges is not None
            if not code and getattr(slot.node, slot.attribute) == slot.source:
                continue
            if part.kind == 'docx':
                run = slot.node.getparent()
                if etree.QName(run).localname != 'r' or run in seen:
                    continue
                seen.add(run)
                ns = etree.QName(run).namespace
                prop = run.find(f'{{{ns}}}rPr')
                if prop is None:
                    prop = etree.Element(f'{{{ns}}}rPr')
                    run.insert(0, prop)
                if ns.endswith('/wordprocessingml/2006/main') or ns.endswith('/wordprocessingml/main'):
                    fonts = prop.find(f'{{{ns}}}rFonts')
                    if fonts is None:
                        fonts = etree.Element(f'{{{ns}}}rFonts')
                        # rStyle precedes rFonts in WordprocessingML.
                        prop.insert(1 if len(prop) and etree.QName(prop[0]).localname == 'rStyle' else 0, fonts)
                    for theme in ('asciiTheme', 'hAnsiTheme', 'eastAsiaTheme', 'cstheme', 'csTheme'):
                        fonts.attrib.pop(f'{{{ns}}}{theme}', None)
                    for attribute in ('ascii', 'hAnsi', 'cs'):
                        fonts.set(f'{{{ns}}}{attribute}', 'Consolas' if code else family)
                    fonts.set(f'{{{ns}}}eastAsia', '宋体' if code else label)
                else:  # DrawingML text in charts/textboxes.
                    for tag, value in (('latin', 'Consolas' if code else family),
                                       ('ea', '宋体' if code else label),
                                       ('cs', 'Consolas' if code else family)):
                        font = prop.find(f'{{{ns}}}{tag}')
                        if font is None:
                            font = etree.SubElement(prop, f'{{{ns}}}{tag}')
                        font.set('typeface', value)
                changed = True
            else:
                element = slot.node if slot.attribute == 'text' else slot.node.getparent()
                if element is None or element in seen:
                    continue
                seen.add(element)
                if etree.QName(element).localname in {'title', 'head'}:
                    continue
                css = CODE_CSS if code else body_css(key)
                style = element.get('style', '').rstrip().rstrip(';')
                element.set('style', (style + '; ' if style else '') + f'font-family: {css} !important;')
                changed = True
    part.fonts_changed = changed
