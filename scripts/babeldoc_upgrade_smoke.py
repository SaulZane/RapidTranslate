"""Verify the upgraded PDF pipeline on original samples (opt-in API calls)."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine
from font_preferences import BODY_FONTS
import pymupdf


def verify(source, output, key):
    expected = {'song': 'SimSun', 'kai': 'KaiTi', 'fangsong': 'FangSong',
                'hei': 'SimHei', 'yahei': 'Microsoft YaHei'}[key]
    with pymupdf.open(source) as original, pymupdf.open(output) as document:
        assert len(document) == 2
        for page, original_index in zip(document, (0, 2)):
            assert tuple(page.mediabox) == tuple(original[original_index].mediabox)
        spans = [s for p in document for b in p.get_text('dict')['blocks']
                 for line in b.get('lines', []) for s in line['spans']]
        selected_characters = sum(len(re.findall(r'[\u4e00-\u9fff]', s['text']))
                                  for s in spans if expected in s['font'])
        assert selected_characters > 60, (key, selected_characters)
        assert any('YaHei' in s['font'] and 'Bold' in s['font'] for s in spans)
        assert any('KaiTi' in s['font'] for s in spans)
        compact = re.sub(r'\s+', '', ''.join(s['text'] for s in spans))
        assert 'defgreet(name):' in compact and 'return"Hello,"+name' in compact
        assert any('Courier' in s['font'] and 'def greet' in s['text'] for s in spans)
        assert 'Printafriendlygreeting' not in compact and 'Keeptheoriginalvalue' not in compact
        assert document[0].get_images(), 'Diagram was removed'
        return {'pages': len(document), 'selected_font_cjk_characters': selected_characters,
                'fonts': sorted({s['font'] for s in spans})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--fonts', nargs='+', choices=BODY_FONTS, default=list(BODY_FONTS))
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live enables API calls on self-authored samples')
    folder = ROOT / 'samples/babeldoc_071_verified'
    folder.mkdir(parents=True, exist_ok=True)
    # Reuse original pages as nested Form XObjects to exercise font resources
    # within forms as well as page resources, without adding new sample prose.
    source = folder / 'nested_sample.pdf'
    if not source.exists():
        with pymupdf.open(ROOT / 'samples/input/sample_book.pdf') as original, pymupdf.open() as nested:
            for i, page in enumerate(original):
                nested.new_page(width=page.rect.width, height=page.rect.height).show_pdf_page(
                    page.rect, original, i)
            nested.save(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    report_path = folder / 'results.json'
    reports = json.loads(report_path.read_text(encoding='utf-8')) if args.retry_failed and report_path.exists() else []
    for key in args.fonts:
        if args.retry_failed and any(r['font'] == key and r['status'] == 'passed' for r in reports):
            continue
        logs = []
        report = {'font': key, 'babeldoc': engine.BABELDOC_FONT_VERSION}
        try:
            result = engine.translate(source, 'Index-Translate-9B', '', folder / key,
                                      pages='1,3', template='tutorial', body_font=key,
                                      on_line=logs.append)
            assert result is not None
            report.update(verify(source, result, key))
            assert hashlib.sha256(source.read_bytes()).hexdigest() == before
            report.update(status='passed', output=str(result.relative_to(ROOT)))
        except Exception as exc:
            report.update(status='failed', error=repr(exc))
        (folder / (key + '.log')).write_text('\n'.join(logs), encoding='utf-8')
        reports = [r for r in reports if r['font'] != key]
        reports.append(report)
        (folder / 'results.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if all(r['status'] == 'passed' for r in reports) else 1


if __name__ == '__main__':
    raise SystemExit(main())
