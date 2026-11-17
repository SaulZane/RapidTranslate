"""Edit text nodes in original EPUB/OOXML packages, preserving all other parts."""
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
import copy
import posixpath
import re
import shutil
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile, ZIP_STORED

from lxml import etree

from translation_client import check_cancelled, translate_all
from code_comments import mark_code_slots, css_code_classes

WORD_NS = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
           "http://purl.oclc.org/ooxml/wordprocessingml/main"}
DRAWING_NS = {"http://schemas.openxmlformats.org/drawingml/2006/main",
              "http://purl.oclc.org/ooxml/drawingml/main"}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
BLOCKS = set("p div section article aside header footer nav blockquote li dt dd h1 h2 h3 h4 h5 h6 td th caption figcaption title text tspan".split())
SKIP_HTML = {"script", "style", "kbd", "samp", "math"}
HTML_MIMES = {"application/xhtml+xml", "text/html", "image/svg+xml"}
NCX_MIME = "application/x-dtbncx+xml"
MAX_BATCH_CHARS = 900
MAX_BATCH_SPANS = 30


def xml_parser():
    return etree.XMLParser(remove_blank_text=False, strip_cdata=False, resolve_entities=False,
                           load_dtd=False, no_network=True, recover=False)


def parse_xml(raw, name):
    try:
        return etree.parse(BytesIO(raw), xml_parser())
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"{name} 的 XML 无法安全读取，已停止以免改变格式：{exc}") from exc


def local_name(node):
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def namespace(node):
    return etree.QName(node).namespace if isinstance(node.tag, str) else ""


@dataclass
class TextSlot:
    node: object
    attribute: str
    source: str
    word: bool = False
    ranges: object = None
    prefix: str = ''

    def set(self, text):
        setattr(self.node, self.attribute, text)
        if self.word and text != text.strip() and self.node.get(XML_SPACE) is None:
            self.node.set(XML_SPACE, "preserve")


class XMLPart:
    def __init__(self, name, raw, kind, template='none', code_classes=None):
        self.name, self.raw, self.kind = name, raw, kind
        self.template, self.code_classes = template, code_classes or set()
        self.tree = parse_xml(raw, name)
        self.groups = self._word_groups() if kind == "docx" else self._html_groups()

    def _word_groups(self):
        groups = OrderedDict()
        fields = []
        pending_breaks = {}
        for node in self.tree.iter():
            ns, name = namespace(node), local_name(node)
            if ns in WORD_NS and name in {'br', 'cr', 'tab'}:
                paragraph = next((p for p in node.iterancestors() if local_name(p) == 'p'), None)
                if paragraph is not None:
                    pending_breaks[paragraph] = pending_breaks.get(paragraph, '') + ('\t' if name == 'tab' else '\n')
            if ns in WORD_NS and name == "fldChar":
                typ = node.get(f"{{{ns}}}fldCharType")
                if typ == "begin":
                    fields.append("")
                elif typ == "end" and fields:
                    fields.pop()
            elif ns in WORD_NS and name == "instrText" and fields:
                fields[-1] += node.text or ""
            if name != "t" or ns not in WORD_NS | DRAWING_NS or not node.text:
                continue
            parents = list(node.iterancestors())
            if any(namespace(p) in WORD_NS and local_name(p) in {"del", "moveFrom", "fldSimple"}
                   for p in parents):
                continue
            # Preserve field codes and their generated results, except TOC labels.
            if any(not re.match(r"\s*TOC\b", field, re.I) for field in fields):
                continue
            paragraph = next((p for p in parents if local_name(p) == "p"
                              and namespace(p) in WORD_NS | DRAWING_NS), node)
            slot = TextSlot(node, "text", node.text, True, prefix=pending_breaks.pop(paragraph, ''))
            groups.setdefault(paragraph, []).append(slot)
        pending_code = []
        def flush_code():
            if pending_code:
                mark_code_slots(pending_code, self.template == 'tutorial')
                pending_code.clear()
        for paragraph, slots in groups.items():
            styles = paragraph.xpath('./*[local-name()="pPr"]/*[local-name()="pStyle"]/@*[local-name()="val"]')
            fonts = paragraph.xpath('.//*[local-name()="rFonts"]/@*[local-name()="ascii"]')
            is_code = any(re.search(r'code|代码|preformatted|verbatim', style, re.I) for style in styles)
            is_code = is_code or bool(len(fonts) == len(slots) and fonts
                                     and all(re.search(r'consolas|courier|mono', font, re.I) for font in fonts))
            if is_code:
                if pending_code:
                    slots[0].prefix = '\n' + slots[0].prefix
                pending_code.extend(slots)
            else:
                flush_code()
        flush_code()
        return list(groups.values())

    def _html_groups(self):
        groups = OrderedDict()
        code_groups = {}
        pending_break = ['']

        def code_node(node):
            return (local_name(node) in {'pre', 'code', 'tt'}
                    or bool(set(node.get('class', '').split()) & self.code_classes)
                    or bool(re.search(r'monospace|courier|consolas', node.get('face', '') + node.get('style', ''), re.I)))

        def collect(node, attribute, context):
            text = getattr(node, attribute)
            if not text or context is None:
                return
            parents = [context, *context.iterancestors()]
            if self.kind == "ncx":
                if local_name(context) != "text":
                    return
            else:
                if any(local_name(p).lower() in SKIP_HTML or p.get("translate") == "no"
                       or "notranslate" in p.get("class", "").split() for p in parents):
                    return
                if any(local_name(p) == "head" for p in parents) and local_name(context) != "title":
                    return
            block = next((p for p in parents if local_name(p).lower() in BLOCKS), context)
            code = next((p for p in reversed(parents) if code_node(p)), None)
            if code is not None:
                block = code
                hints = [p.get('class', '') for p in parents]
                match = re.search(r'(?:language-|lang-)([\w+-]+)', ' '.join(hints))
                code_groups[block] = match.group(1) if match else ''
            groups.setdefault(block, []).append(TextSlot(node, attribute, text, prefix=pending_break[0]))
            pending_break[0] = ''

        def visit(node):
            if local_name(node) == 'br':
                pending_break[0] += '\n'
            if isinstance(node.tag, str):
                collect(node, "text", node)
                for child in node:
                    visit(child)
            # Follow document order, including tails after nested inline markup.
            # Comments/entities are never translated, but their tails are.
            collect(node, "tail", node.getparent())

        visit(self.tree.getroot())
        for block, language in code_groups.items():
            mark_code_slots(groups[block], self.template == 'tutorial', language)
        return list(groups.values())

    def render(self):
        if not getattr(self, 'fonts_changed', False) and all(getattr(slot.node, slot.attribute) == slot.source
               for group in self.groups for slot in group):
            return self.raw
        # Retain the source encoding, namespaces, whitespace, comments and doctype.
        # lxml writes character references when that encoding lacks Chinese glyphs.
        declaration = bool(re.match(br"(?:\xef\xbb\xbf)?\s*<\?xml", self.raw))
        if self.raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            declaration = "<?xml" in self.raw[:100].decode("utf-16", errors="ignore")
        kwargs = {"encoding": self.tree.docinfo.encoding or "UTF-8", "xml_declaration": declaration,
                  "pretty_print": False}
        header = re.match(br"(?:\xef\xbb\xbf)?\s*<\?xml[^?]*\?>", self.raw)
        if header and b"standalone" in header.group():
            kwargs["standalone"] = self.tree.docinfo.standalone
        result = etree.tostring(self.tree, **kwargs)
        # The output must be parseable before it can replace the source part.
        parse_xml(result, self.name)
        return result


def translatable(text):
    # Numerals, page references, punctuation and existing Chinese need no request.
    if re.fullmatch(r"(?:https?://|mailto:|www\.)\S+", text.strip(), re.I):
        return False
    return bool(re.search(r"[A-Za-z\u00c0-\u024f]", text))


def split_long(text):
    while len(text) > MAX_BATCH_CHARS:
        window = text[:MAX_BATCH_CHARS]
        cuts = [m.end() for m in re.finditer(r"[.!?;。！？；]\s*|\s+", window)]
        cut = next((i for i in reversed(cuts) if i >= MAX_BATCH_CHARS // 2), MAX_BATCH_CHARS)
        yield text[:cut]
        text = text[cut:]
    if text:
        yield text


def translate_parts(parts, client, on_line=print, cancel_event=None):
    # Every task holds a target slot and fragment index. XML structure is never sent.
    results, units = {}, []
    for part in parts:
        for group in part.groups:
            unit = []
            for slot in group:
                fragments = []
                results[id(slot)] = (slot, fragments)
                regions = []
                if slot.ranges is None:
                    regions = [(slot.source, True)]
                else:
                    position = 0
                    for start, end in slot.ranges:
                        regions.extend([(slot.source[position:start], False), (slot.source[start:end], True)])
                        position = end
                    regions.append((slot.source[position:], False))
                lines = [(line, allowed) for region, allowed in regions
                         for line in re.split(r"([\r\n\t]+)", region)]
                for line, allowed in lines:
                    if not allowed:
                        fragments.append(line)
                        continue
                    if not line or line.isspace():
                        fragments.append(line)
                        continue
                    for piece in split_long(line):
                        match = re.fullmatch(r"(\s*)(.*?)(\s*)", piece, re.S)
                        leading, core, trailing = match.groups()
                        fragments.append(piece)
                        if translatable(core):
                            unit.append((slot, len(fragments) - 1, leading, core, trailing))
            if unit:
                units.append(unit)
    batches, current, size, count = [], [], 0, 0
    for unit in units:
        subgroup = []
        for task in unit:
            if size + len(task[3]) > MAX_BATCH_CHARS or count >= MAX_BATCH_SPANS:
                if subgroup:
                    current.append(subgroup)
                if current:
                    batches.append(current)
                current, subgroup, size, count = [], [], 0, 0
            subgroup.append(task)
            size += len(task[3])
            count += 1
        if subgroup:
            current.append(subgroup)
    if current:
        batches.append(current)
    on_line(f"▶ 翻译文字（{len(parts)} 个文档部件，{len(batches)} 批）")
    if not batches:
        on_line("   未发现需要翻译的英文正文；保留原文档。")
    answers = translate_all(client, [[[task[3] for task in group] for group in batch] for batch in batches],
                            cancel_event, on_line, (5, 90))
    for batch, translated in zip(batches, answers):
        # Check again here, including injected/custom clients used by integrations.
        if len(translated) != len(batch) or any(len(a) != len(b) for a, b in zip(translated, batch)):
            raise ValueError("接口译文数量与原文不匹配")
        for group, texts in zip(batch, translated):
            for task, text in zip(group, texts):
                slot, fragment, leading, _, trailing = task
                if slot.ranges is not None and any(marker in text for marker in ('*/', '-->', '\n', '\r')):
                    raise ValueError('代码注释译文包含可能改变代码结构的分隔符')
                results[id(slot)][1][fragment] = leading + text.strip() + trailing
    check_cancelled(cancel_event)
    for slot, fragments in results.values():
        slot.set("".join(fragments))


def package_path(base, href):
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parsed.path)))
    if path.startswith(("../", "/")) or "\\" in path:
        raise ValueError(f"文档包含不安全的资源路径：{href}")
    return path


def epub_parts(archive, template='none'):
    names = set(archive.namelist())
    if "mimetype" not in names or archive.read("mimetype") != b"application/epub+zip":
        raise ValueError("文件不是有效的 EPUB（缺少正确的 mimetype）")
    if "META-INF/encryption.xml" in names:
        encryption = parse_xml(archive.read("META-INF/encryption.xml"), "encryption.xml")
        allowed = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
        for method in encryption.xpath('//*[local-name()="EncryptionMethod"]'):
            if method.get("Algorithm") not in allowed:
                raise ValueError("此 EPUB 的内容已加密，无法翻译；请使用未加密的版本。")
    container = parse_xml(archive.read("META-INF/container.xml"), "container.xml")
    roots = container.xpath('//*[local-name()="rootfile"]/@full-path')
    if not roots:
        raise ValueError("EPUB 中未找到书籍清单")
    selected = OrderedDict()
    for root in roots:
        opf_name = package_path("", root)
        if not opf_name or opf_name not in names:
            raise ValueError(f"EPUB 缺少书籍清单：{root}")
        opf = parse_xml(archive.read(opf_name), opf_name)
        items = opf.xpath('//*[local-name()="manifest"]/*[local-name()="item"]')
        by_id = {item.get("id"): item for item in items}
        order = opf.xpath('//*[local-name()="spine"]/*[local-name()="itemref"]/@idref')
        ordered = [by_id[key] for key in order if key in by_id] + items
        for item in ordered:
            mime = item.get("media-type")
            if mime not in HTML_MIMES | {NCX_MIME}:
                continue
            name = package_path(opf_name, item.get("href", ""))
            if name is None:
                raise ValueError("EPUB 引用了远程正文，无法保留完整本地书籍。")
            if name not in names:
                raise ValueError(f"EPUB 缺少正文资源：{name}")
            selected[name] = "ncx" if mime == NCX_MIME else "html"
    classes = css_code_classes([archive.read(name).decode('utf-8', errors='replace')
                                for name in names if name.lower().endswith('.css')])
    return [XMLPart(name, archive.read(name), kind, template, classes) for name, kind in selected.items()]


def docx_parts(archive, template='none'):
    if "word/document.xml" not in archive.namelist() or "[Content_Types].xml" not in archive.namelist():
        raise ValueError("文件不是有效的 DOCX（缺少正文或内容类型清单）")
    content_types = parse_xml(archive.read("[Content_Types].xml"), "[Content_Types].xml")
    types = {item.get("PartName", "").lstrip("/"): item.get("ContentType", "")
             for item in content_types.getroot()}
    selected = []
    for name in archive.namelist():
        mime = types.get(name, "")
        is_text = any(token in mime for token in ("document.main+xml", "header+xml", "footer+xml",
                      "footnotes+xml", "endnotes+xml", "comments+xml", "glossary+xml"))
        is_drawing = any(token in mime for token in ("drawingml.chart+xml", "drawingml.diagramData+xml"))
        if name == "word/document.xml" or (name.startswith("word/") and (is_text or is_drawing)):
            selected.append(XMLPart(name, archive.read(name), "docx", template))
    return selected


def translate_zip(source, destination, client, kind, on_line=print, cancel_event=None,
                  chapters=None, template='none', body_font=None):
    on_line(f"▶ 读取 {kind.upper()} 原始结构")
    with ZipFile(source) as archive:
        if len(set(archive.namelist())) != len(archive.namelist()):
            raise ValueError("文档包含重名资源，无法安全回写")
        if any(info.flag_bits & 1 for info in archive.infolist()):
            raise ValueError("文档已加密，请使用未加密的版本")
        parts = epub_parts(archive, template) if kind == "epub" else docx_parts(archive, template)
        replacements = {}
        if chapters is not None:
            from ebook_chapters import index_epub, select_chapters
            book = index_epub(archive)
            replacements, visible = select_chapters(book, chapters)
            parts = [XMLPart(part.name, replacements.get(part.name, part.raw), part.kind,
                             template, part.code_classes)
                     for part in parts if part.name in visible or part.name in replacements]
        translate_parts(parts, client, on_line, cancel_event)
        if body_font is not None:
            from document_fonts import apply_fonts
            for part in parts:
                apply_fonts(part, body_font)
        replacements.update({part.name: part.render() for part in parts})
        check_cancelled(cancel_event)
        on_line("▶ 回写译文并保留原始样式、图片及文档关系")
        with ZipFile(destination, "w") as output:
            output.comment = archive.comment
            infos = archive.infolist()
            if kind == "epub":
                infos = sorted(infos, key=lambda info: info.filename != "mimetype")
            for original_info in infos:
                check_cancelled(cancel_event)
                info = copy.copy(original_info)
                if kind == "epub" and info.filename == "mimetype":
                    info.compress_type, info.extra = ZIP_STORED, b""
                if info.filename in replacements:
                    output.writestr(info, replacements[info.filename])
                else:
                    with archive.open(original_info) as src, output.open(info, "w") as dst:
                        shutil.copyfileobj(src, dst)
    with ZipFile(destination) as output:
        if output.testzip() is not None:
            raise ValueError("输出文档完整性校验失败")
