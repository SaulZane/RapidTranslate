"""Local font adapter for BabelDoc 0.7.1's native layout pipeline.

MuPDF embeds Windows fonts before parsing. The native mapper measures those
same faces, and the native writer reuses their existing page resources.
"""
import functools
import hashlib
import json
import os
from pathlib import Path
import tempfile

from font_preferences import font_choice, pdf_font_path, windows_font

_workspace = None
_faces = {}
_installed = False


def embed_standard_fonts(document):
    """Preserve Base-14 style flags in the native parser's embedded-font path.

    Its unembedded-font fallback otherwise treats even Helvetica-Bold as
    regular. MuPDF supplies matching standard-font programs and metrics.
    """
    import pymupdf
    names = {'Helvetica': 'helv', 'Helvetica-Bold': 'hebo',
             'Helvetica-Oblique': 'heit', 'Helvetica-BoldOblique': 'hebi',
             'Times-Roman': 'tiro', 'Times-Bold': 'tibo',
             'Times-Italic': 'tiit', 'Times-BoldItalic': 'tibi',
             'Courier': 'cour', 'Courier-Bold': 'cobo',
             'Courier-Oblique': 'coit', 'Courier-BoldOblique': 'cobi'}
    seen = set()
    for page in document:
        for row in page.get_fonts(full=True):
            xref, _, subtype, name = row[:4]
            if xref in seen or subtype != 'Type1' or name not in names:
                continue
            seen.add(xref)
            kind, value = document.xref_get_key(xref, 'FontDescriptor')
            if kind == 'xref':
                descriptor = int(value.split()[0])
                if any(document.xref_get_key(descriptor, key)[0] != 'null'
                       for key in ('FontFile', 'FontFile2', 'FontFile3')):
                    continue
            elif kind != 'null':
                continue
            else:
                descriptor = document.get_new_xref()
                face = pymupdf.Font(fontname=names[name])
                bounds = face.bbox
                bbox = ' '.join(str(v * 1000) for v in
                                (bounds.x0, bounds.y0, bounds.x1, bounds.y1))
                flags = 32 | (64 if face.is_italic else 0) | (1 if face.is_monospaced else 0)
                document.update_object(descriptor,
                    f'<< /Type /FontDescriptor /FontName /{name} /Flags {flags} '
                    f'/FontBBox [{bbox}] /ItalicAngle {-12 if face.is_italic else 0} '
                    f'/Ascent {face.ascender * 1000} /Descent {face.descender * 1000} '
                    f'/StemV {120 if face.is_bold else 80} >>')
                document.xref_set_key(xref, 'FontDescriptor', f'{descriptor} 0 R')
            face = pymupdf.Font(fontname=names[name])
            stream = document.get_new_xref()
            document.update_object(stream, '<< /Subtype /Type1C >>')
            document.update_stream(stream, face.buffer)
            document.xref_set_key(descriptor, 'FontFile3', f'{stream} 0 R')


@functools.lru_cache(maxsize=12)
def standalone_face(path):
    """Extract the first TTC face, matching MuPDF's font selection."""
    import pymupdf
    if path.suffix.lower() != '.ttc':
        return path
    global _workspace
    if _workspace is None:
        _workspace = tempfile.TemporaryDirectory(prefix='index-fonts-')
    with pymupdf.open() as probe:
        xref = probe.new_page().insert_font(fontname='local', fontfile=str(path))
        _, extension, _, data = probe.extract_font(xref)
    target = Path(_workspace.name) / (path.stem + '.' + extension)
    target.write_bytes(data)
    return target


def local_faces(key):
    import pymupdf
    marker = 'serif' if font_choice(key)[3] == 'serif' else 'sans'
    paths = {'body': pdf_font_path(key), 'bold': windows_font('msyhbd.ttc'),
             'italic': windows_font('simkai.ttf')}
    result = {}
    for role, path in paths.items():
        if path is None:
            continue
        path = standalone_face(path)
        face = pymupdf.Font(fontfile=str(path))
        identifier = f'index_{key}_{marker}_{role}'
        metadata = {
            'font_name': face.name, 'ascent': face.ascender * 1000,
            'descent': face.descender * 1000, 'encoding_length': 2,
            'bold': face.is_bold, 'italic': face.is_italic,
            'monospace': face.is_monospaced, 'serif': face.is_serif,
            'sha3_256': hashlib.sha3_256(path.read_bytes()).hexdigest(),
            'file_name': path.name, 'size': path.stat().st_size,
        }
        result[role] = (identifier, path, metadata)
    return result


def inline_page_resources(document):
    """The native writer drops translated text when page resources are an indirect object."""
    for page in document:
        kind, value = document.xref_get_key(page.xref, 'Resources')
        if kind == 'xref':
            document.xref_set_key(page.xref, 'Resources',
                                  document.xref_object(int(value.split()[0]), compressed=True))


def install():
    global _installed
    if _installed:
        return
    from babeldoc import _rust_il, rust_run_context
    from babeldoc.assets.embedding_assets_metadata import EMBEDDING_FONT_METADATA
    from babeldoc.format.pdf import high_level

    original_assets = rust_run_context._font_assets
    original_mapper = _rust_il.font_mapper_handle_with_source_styles
    original_open = high_level.open_pdf_with_save_fallback
    original_writer = high_level._rust_writer_inputs

    def font_assets(config):
        family, paths, checksums = original_assets(config)
        key = os.environ.get('INDEX_TRANSLATE_BODY_FONT')
        if not key:
            return family, paths, checksums
        family = {group: list(ids) for group, ids in family.items()}
        faces = local_faces(key)
        _faces.clear()
        ids = {}
        for role, (identifier, path, metadata) in faces.items():
            ids[role] = identifier
            _faces[identifier] = path
            paths[identifier] = str(path)
            checksums[identifier] = metadata['sha3_256']
            EMBEDDING_FONT_METADATA[identifier] = metadata
        family['normal'] = [ids[r] for r in ('body', 'bold') if r in ids] + family['normal']
        family['script'] = [ids[r] for r in ('bold', 'italic') if r in ids] + family['script']
        family['fallback'] = [ids['body']] + family['fallback']
        family['base'] = [ids['body']]
        _faces['base'] = _faces[ids['body']]
        return family, paths, checksums

    def mapper(family_json, paths_json, checksums_json, primary=None):
        if not os.environ.get('INDEX_TRANSLATE_BODY_FONT'):
            return original_mapper(family_json, paths_json, checksums_json, primary)
        paths, checksums = json.loads(paths_json), json.loads(checksums_json)
        styles = {}
        for identifier, filename in paths.items():
            if hashlib.sha3_256(Path(filename).read_bytes()).hexdigest() != checksums[identifier]:
                raise RuntimeError(f'字体文件校验失败：{filename}')
            metadata = EMBEDDING_FONT_METADATA[identifier]
            styles[identifier] = {
                'bold': bool(metadata['bold']), 'italic': bool(metadata['italic']),
                'monospaced': bool(metadata['monospace']), 'serif': bool(metadata['serif']),
            }
        return _rust_il.font_mapper_handle(family_json, paths_json, primary), styles

    def open_pdf(source, destination):
        document = original_open(source, destination)
        embed_standard_fonts(document)
        if os.environ.get('INDEX_TRANSLATE_BODY_FONT'):
            references = {}
            inline_page_resources(document)
            for page in document:
                for identifier, path in _faces.items():
                    references[identifier] = page.insert_font(fontname=identifier, fontfile=str(path))
            # A translated paragraph can remain inside a Form XObject. Font
            # resources there do not inherit the page's resource dictionary.
            for xref in range(1, document.xref_length()):
                for prefix in ('Resources/', ''):
                    kind, value = document.xref_get_key(xref, prefix + 'Font')
                    owner, key = xref, prefix + 'Font/'
                    if kind == 'xref':
                        owner, key = int(value.split()[0]), ''
                        kind = 'dict'
                    if kind == 'dict':
                        for identifier, font_xref in references.items():
                            document.xref_set_key(owner, key + identifier, f'{font_xref} 0 R')
        return document

    def writer_inputs(config, context, source):
        writer, assets, paths = original_writer(config, context, source)
        if os.environ.get('INDEX_TRANSLATE_BODY_FONT'):
            # Native embedding only accepts its bundled font hashes. Local
            # fonts are already present in the parsed PDF and its font table.
            assets = [asset for asset in assets if asset['id'] not in _faces]
        return writer, assets, paths

    rust_run_context._font_assets = font_assets
    _rust_il.font_mapper_handle_with_source_styles = mapper
    high_level.open_pdf_with_save_fallback = open_pdf
    high_level._rust_writer_inputs = writer_inputs
    _installed = True
