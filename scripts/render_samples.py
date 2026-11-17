"""Render QA fixtures with native Word and the bundled Poppler executable."""
from pathlib import Path
import subprocess
import sys

import pythoncom
import win32com.client

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'samples' / 'qa'
POPLER = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe'


def render(path):
    folder = QA / (path.parent.name + '_' + path.stem + '_' + path.suffix[1:])
    folder.mkdir(parents=True, exist_ok=True)
    pdf = path
    if path.suffix == '.docx':
        pythoncom.CoInitialize()
        word = document = None
        try:
            word = win32com.client.DispatchEx('Word.Application')
            word.Visible = False
            word.DisplayAlerts = 0
            word.AutomationSecurity = 3
            document = word.Documents.Open(str(path.resolve()), ReadOnly=True, AddToRecentFiles=False,
                                           ConfirmConversions=False, Visible=False)
            document.Repaginate()
            print(path.name, 'pages:', document.ComputeStatistics(2), flush=True)
            pdf = folder / (path.stem + '.pdf')
            document.ExportAsFixedFormat(str(pdf.resolve()), 17)
        finally:
            try:
                if word is not None:
                    word.Quit(SaveChanges=0)
            finally:
                document = word = None
                pythoncom.CoUninitialize()
    subprocess.run([str(POPLER), '-r', '110', '-png', str(pdf), str(folder / 'page')],
                   check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    print('RENDERED', folder, flush=True)


if __name__ == '__main__':
    for filename in sys.argv[1:]:
        render(Path(filename))
