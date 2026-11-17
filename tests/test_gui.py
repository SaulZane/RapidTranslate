from pathlib import Path
import threading

import gui
import pytest


@pytest.fixture(scope='module')
def app():
    # One Tk interpreter per process: creating a second one intermittently fails on Windows.
    app = gui.App()
    app.withdraw()
    yield app
    app.destroy()
    # Release Tk variables on their owning thread before later network tests.
    app.model = app.template = app.body_font = app.ocr = app.layout = None




def test_file_picker_controls_pages_and_keeps_source_fixed(app, monkeypatch, tmp_path):
    monkeypatch.setattr(app, 'show_chapters', lambda: None)
    monkeypatch.setattr(gui.engine, 'load_chapters', lambda *args: [])
    monkeypatch.setattr(gui.filedialog, 'askopenfilename', lambda **kwargs: str(tmp_path / 'book.pdf'))
    app.pick()
    assert app.pages.cget('state') == 'normal'
    app.pages.insert(0, '1-3')
    monkeypatch.setattr(gui.filedialog, 'askopenfilename', lambda **kwargs: str(tmp_path / 'book.epub'))
    app.pick()
    assert app.pages.cget('state') == 'disabled'
    assert app.pages.get() == ''
    app.loading_chapters = False
    started = []
    class Thread:
        def __init__(self, target, args, daemon):
            started.append((target, args))
        def start(self): pass
    monkeypatch.setattr(gui.threading, 'Thread', Thread)
    app.template.set('magazine')
    app.start()
    assert app.open_btn.cget('state') == 'disabled'
    assert started[0][1][0] == tmp_path / 'book.epub'
    settings = started[0][1][1]
    assert settings['pages'] is None
    assert settings['template'] == 'magazine'
    assert settings['body_font'] == 'song'
    app.stop()
    assert app.cancel_event.is_set()
    app._finish(None)
    assert app.open_btn.cget('state') == 'normal'


def test_ocr_checkbox_only_for_pdf(app, monkeypatch, tmp_path):
    monkeypatch.setattr(app, 'show_chapters', lambda: None)
    monkeypatch.setattr(gui.filedialog, 'askopenfilename', lambda **kwargs: str(tmp_path / 'book.pdf'))
    app.pick()
    assert app.ocr_check.cget('state') == 'normal'
    app.ocr.set(True)
    app.layout.set(True)
    started = []

    class Thread:
        def __init__(self, target, args, daemon):
            started.append(args)
        def start(self): pass
    monkeypatch.setattr(gui.threading, 'Thread', Thread)
    app.start()
    assert (started[0][1]['ocr'], started[0][1]['layout']) == (True, True)
    assert app.ocr_check.cget('state') == app.layout_check.cget('state') == 'disabled'
    app._finish(None)
    assert app.ocr_check.cget('state') == app.layout_check.cget('state') == 'normal'
    monkeypatch.setattr(gui.filedialog, 'askopenfilename', lambda **kwargs: str(tmp_path / 'book.docx'))
    app.pick()
    assert app.ocr_check.cget('state') == app.layout_check.cget('state') == 'disabled'
    assert app.ocr.get() is False and app.layout.get() is False


def test_resume_button_continues_with_the_saved_settings(app, monkeypatch, tmp_path):
    from translation_client import Progress
    source = tmp_path / 'book.pdf'
    saved = {'model': 'Index-Translate-9B', 'requirement': '', 'pages': '1-30', 'chapters': None,
             'template': 'magazine', 'body_font': 'song', 'ocr': False, 'layout': True}
    monkeypatch.setattr(gui.filedialog, 'askopenfilename', lambda **kwargs: str(source))
    app.pick()
    assert app.resume_btn.cget('state') == 'disabled'
    Progress(gui.engine.progress_path(source), saved).store('key', ['译文'])
    app.pick()
    assert app.resume_btn.cget('state') == 'normal'
    started = []

    class Thread:
        def __init__(self, target, args, daemon):
            started.append(args)
        def start(self): pass
    monkeypatch.setattr(gui.threading, 'Thread', Thread)
    app.resume()
    assert started == [(source, saved)]
    assert app.resume_btn.cget('state') == 'disabled'
