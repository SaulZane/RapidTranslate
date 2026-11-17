"""Locate natural-language comments with a lexer; never translate executable tokens."""
import re

from pygments.lexers import get_lexer_by_name
from pygments.token import Comment


def comment_ranges(text, language=''):
    hint = language.lower().replace('language-', '').replace('lang-', '').strip()
    if not hint:
        if re.search(r'^\s*#\s*(?:include|define)|\bstd::|\bint\s+main\s*\(', text, re.M):
            hint = 'cpp'
        elif re.search(r'^\s*(?:def |from \S+ import |import |print\(|return\b)|#', text, re.M):
            hint = 'python'
        elif re.search(r'//|/\*|;\s*$|\b(?:function|const|let|var)\b', text, re.M):
            hint = 'javascript'
        elif re.search(r'^\s*(?:\$|#!/.*sh)', text, re.M):
            hint = 'bash'
        else:
            return []  # Unknown code is protected instead of guessed as prose.
    try:
        lexer = get_lexer_by_name(hint, stripnl=False, ensurenl=False, tabsize=0)
    except Exception:
        return []
    ranges = []
    for start, kind, value in lexer.get_tokens_unprocessed(text):
        if kind not in Comment or kind in Comment.Preproc or kind in Comment.Hashbang:
            continue
        # Keep directives and tooling comments byte-for-byte.
        if re.match(r'\s*(?:#|//)\s*(?:noqa|type:|coding[:=]|pylint:|eslint|ts-ignore|pragma|region|endregion)', value):
            continue
        for match in re.finditer(r'[^\r\n]+', value):
            line = match.group()
            left = re.match(r'\s*(?:/\*+|//+|#+|\*|--|<!--)?\s*', line).end()
            right = re.search(r'\s*(?:\*/|-->)?\s*$', line).start()
            if right > left and re.search(r'[A-Za-z]', line[left:right]):
                ranges.append((start + match.start() + left, start + match.start() + right))
    return ranges


def mark_code_slots(slots, translate_comments, language=''):
    offsets, offset, pieces = [], 0, []
    for slot in slots:
        pieces.append(slot.prefix)
        offset += len(slot.prefix)
        offsets.append(offset)
        pieces.append(slot.source)
        offset += len(slot.source)
    ranges = comment_ranges(''.join(pieces), language) if translate_comments else []
    for slot, offset in zip(slots, offsets):
        slot.ranges = [(max(start, offset) - offset, min(end, offset + len(slot.source)) - offset)
                       for start, end in ranges if start < offset + len(slot.source) and end > offset]


def css_code_classes(styles):
    classes = set()
    for css in styles:
        for selector, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
            if re.search(r'font-family\s*:[^;]*(?:monospace|courier|consolas)|white-space\s*:\s*pre', declarations, re.I):
                classes.update(re.findall(r'\.([\w-]+)', selector))
    return classes
