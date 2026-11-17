"""Executed by calibre-debug: native Kindle readers, text edits, native writers."""
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED


def open_book(source, work):
    from calibre.ebooks.oeb.polish.container import get_container, Container
    if Path(source).suffix.lower() != '.mobi':
        return get_container(source, tdir=work, tweak_mode=True), None
    from calibre import CurrentDir
    from calibre.customize.ui import plugin_for_input_format
    from calibre.ebooks.conversion.plumber import Plumber
    from calibre.ebooks.mobi.reader.headers import MetadataHeader
    from calibre.utils.logging import default_log
    from calibre.ebooks import DRMError
    with open(source, 'rb') as stream:
        header = MetadataHeader(stream, default_log)
        if header.encryption_type:
            raise DRMError()
        subtype = 'old' if header.kf8_type is None else ('both' if header.kf8_type == 'joint' else 'new')
    plumber = Plumber(source, str(Path(work) / 'unused.mobi'), default_log)
    plumber.setup_options()
    reader = plugin_for_input_format('mobi')
    # MOBI6's reader normally derives a TOC only from an inline contents page.
    # Feed binary NCX offsets into its own anchor insertion algorithm as well.
    from calibre.ebooks.mobi.reader.mobi6 import MobiReader
    from calibre.ebooks.mobi.reader.ncx import read_ncx
    original_anchors = MobiReader.add_anchors
    entries = []
    def add_anchors_with_ncx(mobi):
        entries[:] = read_ncx(mobi.sections, mobi.book_header.ncxidx, mobi.book_header.codec)
        original = mobi.mobi_html
        marker = b'<div id="index_translate_ncx_offsets">'
        extra = marker + b''.join(b'<a filepos="%d"></a>' % entry['pos'] for entry in entries if entry['pos'] >= 0) + b'</div>'
        try:
            mobi.mobi_html = original + extra
            original_anchors(mobi)
            mobi.processed_html = mobi.processed_html.split(marker, 1)[0]
        finally:
            mobi.mobi_html = original
    if subtype == 'old':
        MobiReader.add_anchors = add_anchors_with_ncx
    try:
        with CurrentDir(work), open(source, 'rb') as stream:
            opf = reader.convert(stream, plumber.opts, 'mobi', default_log, {})
    finally:
        MobiReader.add_anchors = original_anchors
    if entries:
        restore_mobi_ncx(opf, entries)
    # Diagnostic dumps produced by the reader are not book resources.
    Path(work, 'debug-raw.html').unlink(missing_ok=True)
    return Container(rootpath=work, opfpath=opf, log=default_log), subtype


def restore_mobi_ncx(opf_path, entries):
    from lxml import etree
    ns = 'http://www.daisy.org/z3986/2005/ncx/'
    opf = etree.parse(opf_path)
    manifests = opf.xpath('//*[local-name()="manifest"]')
    spines = opf.xpath('//*[local-name()="spine"]')
    items = {item.get('id'): item for item in manifests[0]}
    first = next(iter(spines[0]))
    body_name = items[first.get('idref')].get('href')
    root = etree.Element('{' + ns + '}ncx', nsmap={None: ns}, version='2005-1')
    etree.SubElement(root, '{' + ns + '}head')
    title = etree.SubElement(root, '{' + ns + '}docTitle')
    etree.SubElement(title, '{' + ns + '}text').text = 'Contents'
    nav = etree.SubElement(root, '{' + ns + '}navMap')
    parents = {-1: nav}
    for number, entry in enumerate(entries):
        parent = parents.get(entry['parent'], nav)
        node = etree.SubElement(parent, '{' + ns + '}navPoint', id=f'nav-{number}', playOrder=str(number + 1))
        label = etree.SubElement(node, '{' + ns + '}navLabel')
        etree.SubElement(label, '{' + ns + '}text').text = entry['text']
        target = body_name + (f"#filepos{entry['pos']}" if entry['pos'] > 0 else '')
        etree.SubElement(node, '{' + ns + '}content', src=target)
        parents[entry.get('num', number)] = node
    ncx = next((item for item in manifests[0] if item.get('media-type') == 'application/x-dtbncx+xml'), None)
    if ncx is None:
        package_ns = etree.QName(manifests[0]).namespace
        ncx = etree.SubElement(manifests[0], '{' + package_ns + '}item',
                              id='index-translate-ncx', href='toc.ncx', **{'media-type': 'application/x-dtbncx+xml'})
    spines[0].set('toc', ncx.get('id'))
    path = Path(opf_path).parent / ncx.get('href')
    path.write_bytes(etree.tostring(root, encoding='utf-8', xml_declaration=True))
    opf.write(opf_path, encoding='utf-8', xml_declaration=True)


def save_mobi(container, output, subtype):
    from calibre.customize.ui import plugin_for_input_format, plugin_for_output_format
    from calibre.ebooks.conversion.plumber import Plumber, create_oebbook
    from calibre.ebooks.mobi.tweak import set_cover
    opf = container.name_path_map[container.opf_name]
    plumber = Plumber(opf, output, container.log)
    plumber.setup_options()
    options = plumber.opts
    options.mobi_file_type = subtype
    options.no_inline_toc = True
    options.mobi_keep_original_images = True
    options.keep_ligatures = True
    options.mobi_passthrough = True
    options.source = options.input_profile
    options.dest = options.output_profile
    book = create_oebbook(container.log, opf, options)
    set_cover(book)
    plugin_for_output_format('mobi').convert(book, output, plugin_for_input_format('mobi'), options, container.log)


def main():
    action, source, bundle, *rest = sys.argv[1:]
    with TemporaryDirectory(prefix="index-azw3-") as work:
        container, subtype = open_book(source, work)
        if action == "export":
            metadata = {}
            with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
                for name, mime in container.mime_map.items():
                    if mime not in {"application/xhtml+xml", "text/html", "image/svg+xml", "application/x-dtbncx+xml", "text/css"} and name != container.opf_name:
                        continue
                    data = container.raw_data(name, decode=False)
                    kind = ('opf' if name == container.opf_name else 'css' if mime == 'text/css'
                            else 'ncx' if mime == 'application/x-dtbncx+xml' else 'html')
                    metadata[name] = {"kind": kind,
                                      "sha256": hashlib.sha256(data).hexdigest()}
                    archive.writestr(name, data)
            Path(bundle + ".json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        elif action == "apply":
            metadata = json.loads(Path(bundle + ".json").read_text(encoding="utf-8"))
            with ZipFile(bundle) as archive:
                for name in archive.namelist():
                    if name not in container.name_path_map or name not in metadata:
                        raise ValueError(f"AZW3 部件不存在：{name}")
                    original = container.raw_data(name, decode=False)
                    if hashlib.sha256(original).hexdigest() != metadata[name]["sha256"]:
                        raise ValueError(f"AZW3 原文在翻译期间发生变化：{name}")
                    with container.open(name, "wb") as stream:
                        stream.write(archive.read(name))
            if subtype:
                save_mobi(container, rest[0], subtype)
            else:
                container.commit(outpath=rest[0])
        else:
            raise ValueError(f"Unknown action: {action}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if "drm" in type(exc).__name__.lower():
            print("ERROR 此电子书已加密，请使用未加密的版本。", flush=True)
        else:
            print(f"ERROR Kindle 电子书处理失败：{type(exc).__name__}: {exc}", flush=True)
        raise SystemExit(1)
