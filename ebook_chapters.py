"""Read actual EPUB/Kindle navigation and cut selected anchored chapter ranges."""
from dataclasses import dataclass, field
import posixpath
from urllib.parse import unquote, urlsplit, quote

from lxml import etree

from book_formats import parse_xml, package_path, local_name


@dataclass
class Chapter:
    id: str
    title: str
    name: str
    fragment: str = ''
    parent: str = ''
    depth: int = 0
    element_index: int | None = None
    node: object = field(default=None, repr=False)

    def public(self):
        return {key: getattr(self, key) for key in ('id', 'title', 'name', 'fragment', 'parent', 'depth')}


class BookIndex:
    def __init__(self, opf_name, files):
        self.opf_name, self.files = opf_name, files
        self.opf = parse_xml(files[opf_name], opf_name)
        items = self.opf.xpath('//*[local-name()="manifest"]/*[local-name()="item"]')
        self.items = {item.get('id'): item for item in items}
        self.spine_nodes = self.opf.xpath('//*[local-name()="spine"]/*[local-name()="itemref"]')
        self.spine = []
        for node in self.spine_nodes:
            item = self.items.get(node.get('idref'))
            if item is not None:
                name = package_path(opf_name, item.get('href', ''))
                if name in files:
                    self.spine.append(name)
        if not self.spine:
            raise ValueError('电子书没有可读取的正文顺序')
        self.toc_name, self.toc_tree, self.toc_kind = None, None, None
        self.chapters = []
        self.ncx_names = []
        for item in items:
            name = package_path(opf_name, item.get('href', ''))
            if name not in files:
                continue
            if item.get('media-type') == 'application/x-dtbncx+xml':
                self.ncx_names.append(name)
            if 'nav' in item.get('properties', '').split():
                self._read_nav(name)
        if not self.chapters:
            for name in self.ncx_names:
                self._read_ncx(name)
                if self.chapters:
                    break
        if not self.chapters:
            self._read_headings()

    def _target(self, base, href):
        name = package_path(base, href)
        return name, unquote(urlsplit(href).fragment)

    def _read_nav(self, name):
        tree = parse_xml(self.files[name], name)
        navs = tree.xpath('//*[local-name()="nav"]')
        nav = next((n for n in navs if 'toc' in (n.get('{http://www.idpf.org/2007/ops}type') or '').split()
                    or n.get('role') == 'doc-toc'), None)
        if nav is None:
            return
        chapters = []
        def walk(ol, parent='', depth=0):
            number = 0
            for li in ol:
                if local_name(li) != 'li':
                    continue
                label = next((n for n in li if local_name(n) in {'a', 'span'}), None)
                if label is None:
                    continue
                number += 1
                key = f'{parent}.{number}' if parent else str(number)
                href = label.get('href') or ''
                target, fragment = self._target(name, href)
                chapter = Chapter(key, ''.join(label.itertext()).strip() or f'章节 {key}',
                                  target or '', fragment, parent, depth, node=li)
                chapters.append(chapter)
                for child in li:
                    if local_name(child) in {'ol', 'ul'}:
                        walk(child, key, depth + 1)
                if chapter.name not in self.spine:
                    descendant = next((c for c in chapters if c.parent == key and c.name in self.spine), None)
                    if descendant:
                        chapter.name, chapter.fragment = descendant.name, descendant.fragment
        for ol in nav:
            if local_name(ol) in {'ol', 'ul'}:
                walk(ol)
        if chapters:
            self.chapters = chapters
            self.toc_name, self.toc_tree, self.toc_kind = name, tree, 'nav'

    def _read_ncx(self, name):
        tree = parse_xml(self.files[name], name)
        maps = tree.xpath('//*[local-name()="navMap"]')
        if not maps:
            return
        chapters = []
        def walk(parent_node, parent='', depth=0):
            children = [node for node in parent_node if local_name(node) == 'navPoint']
            for number, node in enumerate(children, 1):
                key = f'{parent}.{number}' if parent else str(number)
                sources = node.xpath('./*[local-name()="content"]/@src')
                labels = node.xpath('./*[local-name()="navLabel"]/*[local-name()="text"]')
                target, fragment = self._target(name, sources[0] if sources else '')
                title = ''.join(labels[0].itertext()).strip() if labels else f'章节 {key}'
                chapters.append(Chapter(key, title, target or '', fragment, parent, depth, node=node))
                walk(node, key, depth + 1)
        walk(maps[0])
        if chapters:
            self.chapters = chapters
            self.toc_name, self.toc_tree, self.toc_kind = name, tree, 'ncx'

    def _read_headings(self):
        stack, counts = [], {}
        for name in self.spine:
            tree = parse_xml(self.files[name], name)
            nodes = list(tree.getroot().iter())
            headings = [(i, node) for i, node in enumerate(nodes)
                        if local_name(node) in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}]
            if not headings:
                titles = tree.xpath('//*[local-name()="title"]')
                title = ''.join(titles[0].itertext()).strip() if titles else posixpath.basename(name)
                key = str(counts.get('', 0) + 1)
                counts[''] = int(key)
                self.chapters.append(Chapter(key, title, name))
                stack = []
                continue
            for position, node in headings:
                level = int(local_name(node)[1:])
                while stack and stack[-1][0] >= level:
                    stack.pop()
                parent = stack[-1][1] if stack else ''
                counts[parent] = counts.get(parent, 0) + 1
                key = f'{parent}.{counts[parent]}' if parent else str(counts[parent])
                self.chapters.append(Chapter(key, ''.join(node.itertext()).strip(), name,
                                             node.get('id', ''), parent, len(stack), position))
                stack.append((level, key))

    def public(self):
        return [chapter.public() for chapter in self.chapters]


def index_epub(archive):
    container = parse_xml(archive.read('META-INF/container.xml'), 'container.xml')
    roots = container.xpath('//*[local-name()="rootfile"]/@full-path')
    if not roots:
        raise ValueError('EPUB 没有书籍清单')
    opf_name = package_path('', roots[0])
    opf = parse_xml(archive.read(opf_name), opf_name)
    files = {opf_name: archive.read(opf_name)}
    for item in opf.xpath('//*[local-name()="manifest"]/*[local-name()="item"]'):
        if item.get('media-type') in {'application/xhtml+xml', 'text/html', 'application/x-dtbncx+xml', 'image/svg+xml'}:
            name = package_path(opf_name, item.get('href', ''))
            if name is not None:
                files[name] = archive.read(name)
    return BookIndex(opf_name, files)


def serialized(tree):
    return etree.tostring(tree, encoding='utf-8', xml_declaration=True, pretty_print=False)


def select_chapters(book, selected):
    """Return changed parts and visible body names for a partial edition."""
    selected = set(selected)
    known = {chapter.id for chapter in book.chapters}
    if not selected or selected - known:
        raise ValueError('请选择有效的章节；章节编号以当前文件的目录树为准。')
    effective = {key for key in known if any(key == chosen or key.startswith(chosen + '.') for chosen in selected)}
    trees, positions, ends, bases = {}, {}, {}, {}
    cursor = 0
    for name in book.spine:
        tree = parse_xml(book.files[name], name)
        trees[name] = tree
        nodes = list(tree.getroot().iter())
        bases[name] = cursor
        positions[name] = {node: cursor + index for index, node in enumerate(nodes)}
        ends[name] = {node: max(positions[name][child] for child in node.iter()) for node in nodes}
        cursor += len(nodes) + 1
    points = {}
    for chapter in book.chapters:
        if chapter.name not in trees:
            if chapter.id in effective:
                raise ValueError(f'章节“{chapter.title}”没有有效的本地正文')
            continue
        tree = trees[chapter.name]
        if chapter.element_index is not None:
            node = list(tree.getroot().iter())[chapter.element_index]
        elif chapter.fragment:
            matches = tree.xpath('//*[@id=$anchor or @name=$anchor]', anchor=chapter.fragment)
            if not matches:
                if chapter.id in effective:
                    raise ValueError(f'章节“{chapter.title}”的锚点不存在：{chapter.fragment}')
                continue
            node = matches[0]
        else:
            bodies = tree.xpath('//*[local-name()="body"]')
            node = bodies[0] if bodies else tree.getroot()
        points[chapter.id] = positions[chapter.name][node]
    ranges = []
    for i, chapter in enumerate(book.chapters):
        if chapter.id not in selected:
            continue
        start = points[chapter.id]
        end = next((points[next_ch.id] for next_ch in book.chapters[i + 1:]
                    if next_ch.depth <= chapter.depth and next_ch.id in points and points[next_ch.id] > start), cursor)
        ranges.append((start, end))
    def inside(position):
        return any(start <= position < end for start, end in ranges)
    changed, visible = {}, []
    for name, tree in trees.items():
        positions_here, ends_here = positions[name], ends[name]
        bodies = tree.xpath('//*[local-name()="body"]')
        body = bodies[0] if bodies else tree.getroot()
        def prune(node):
            own = inside(positions_here[node])
            child_kept = False
            for child in list(node):
                if prune(child):
                    child_kept = True
                else:
                    node.remove(child)
            if not own:
                node.text = None
            if not inside(ends_here[node] + 0.5):
                node.tail = None
            return own or child_kept
        if prune(body):
            changed[name] = serialized(tree)
            visible.append(name)
    if not visible:
        raise ValueError('所选章节没有可输出的正文')
    # Keep resources and links in the package, but remove unselected reading-order entries.
    for itemref in list(book.spine_nodes):
        item = book.items.get(itemref.get('idref'))
        name = package_path(book.opf_name, item.get('href', '')) if item is not None else None
        if name not in visible:
            itemref.getparent().remove(itemref)
    changed[book.opf_name] = serialized(book.opf)
    keep_nav = set(effective)
    for key in effective:
        parts = key.split('.')
        keep_nav.update('.'.join(parts[:i]) for i in range(1, len(parts)))
    if book.toc_tree is not None:
        for chapter in reversed(book.chapters):
            node = chapter.node
            if node is None:
                continue
            if chapter.id not in keep_nav:
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            elif chapter.id not in effective:
                descendant = next(c for c in book.chapters if c.id in effective and c.id.startswith(chapter.id + '.'))
                target = quote(posixpath.relpath(descendant.name, posixpath.dirname(book.toc_name)), safe='/')
                if descendant.fragment:
                    target += '#' + quote(descendant.fragment)
                links = node.xpath('./*[local-name()="a" or local-name()="content"]')
                if links:
                    links[0].set('href' if book.toc_kind == 'nav' else 'src', target)
        changed[book.toc_name] = serialized(book.toc_tree)
    # EPUB 3 can also carry an NCX for older readers. Prune it by target ranges.
    for ncx_name in book.ncx_names:
        if ncx_name == book.toc_name:
            continue
        ncx = parse_xml(book.files[ncx_name], ncx_name)
        allowed_targets = {(c.name, c.fragment) for c in book.chapters if c.id in keep_nav}
        for node in reversed(ncx.xpath('//*[local-name()="navPoint"]')):
            refs = node.xpath('./*[local-name()="content"]/@src')
            target = book._target(ncx_name, refs[0]) if refs else None
            if target not in allowed_targets and not node.xpath('./*[local-name()="navPoint"]'):
                node.getparent().remove(node)
        changed[ncx_name] = serialized(ncx)
    return changed, visible
