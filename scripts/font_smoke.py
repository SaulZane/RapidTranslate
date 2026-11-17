"""Small opt-in API verification for output fonts on self-authored samples."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine
from scripts.live_smoke import visible_text


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--formats', nargs='+', choices=['pdf', 'docx', 'epub', 'azw3', 'mobi'])
    args = parser.parse_args()
    if not args.live:
        parser.error('Use --live to enable sample API calls')
    destination = ROOT / 'samples/font_verified'
    destination.mkdir(parents=True, exist_ok=True)
    cases = [('pdf', 'song', 'tutorial', {'pages': '3'}),
             ('docx', 'fangsong', 'tutorial', {'pages': '3'}),
             ('epub', 'kai', 'magazine', {'chapters': ['1.1']}),
             ('azw3', 'hei', 'tutorial', {'chapters': ['3']}),
             ('mobi', 'song', 'tutorial', {'chapters': ['5']})]
    report_path = destination / 'results.json'
    report = (json.loads(report_path.read_text(encoding='utf-8'))
              if args.formats and report_path.exists() else [])
    for extension, font, template, selection in cases:
        if args.formats and extension not in args.formats:
            continue
        entry = dict(format=extension, font=font, template=template, selection=selection)
        lines = []
        def log(line):
            lines.append(line)
            if not line.startswith('@@'):
                print(line, flush=True)
        try:
            source = ROOT / 'samples/input' / ('sample_book.' + extension)
            original = source.read_bytes()
            result = engine.translate(source, 'Index-Translate-9B', '', destination,
                                      template=template, body_font=font, on_line=log, **selection)
            assert result and result.exists()
            text, _ = visible_text(result)
            assert re.search(r'[\u4e00-\u9fff]', text)
            assert source.read_bytes() == original
            if template == 'tutorial':
                compact = re.sub(r'\s+', '', text)
                assert 'defgreet(name):' in compact and 'return"Hello,"+name' in compact
                assert 'Print a friendly greeting' not in text
                assert 'Keep the original value' not in text
            if extension == 'pdf':
                import pymupdf
                with pymupdf.open(result) as document:
                    spans = [span for page in document for block in page.get_text('dict')['blocks']
                             for line in block.get('lines', []) for span in line['spans']]
                    # A few Song glyphs in comments are insufficient: the prose
                    # itself must use the selected font as well.
                    assert sum(len(re.findall(r'[\u4e00-\u9fff]', span['text']))
                               for span in spans if 'SimSun' in span['font']) > 30
            entry.update(status='passed', output=str(result))
        except Exception as exc:
            entry.update(status='failed', error=str(exc))
        report = [old for old in report if old['format'] != extension]
        report.append(entry)
        (destination / (extension + '.log')).write_text('\n'.join(lines), encoding='utf-8')
        (destination / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(entry, flush=True)
    raise SystemExit(0 if all(item['status'] == 'passed' for item in report) else 1)
