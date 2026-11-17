import sys
import threading
import time
import io
import json
import urllib.error
import xml.etree.ElementTree as ET

import pytest

import engine
from book_translate import main
from translation_client import TranslationClient, TranslationCancelled


@pytest.mark.parametrize('response', [
    '<translation><p id="0"><s id="1">翻译</s></p></translation>',
    '<translation><p id="0"><s id="0">翻译</s><s id="1">多余</s></p></translation>',
    '<translation><p id="0"><s id="0"><b>加粗</b></s></p></translation>',
    '<translation><p id="0"><s id="0"></s></p></translation>',
    '<translation><p id="0"><s id="0">新增\n段落</s></p></translation>',
    '<!DOCTYPE translation><translation><p id="0"><s id="0">翻译</s></p></translation>',
    'Here is the translation: <translation><p id="0"><s id="0">翻译</s></p></translation>',
    '<translation><p id="0"><s id="0">截断',
])
def test_reject_broken_structure(response):
    with pytest.raises(ValueError):
        TranslationClient.decode(response, [['Text']])


def test_accept_split_word_and_entities():
    response = '<translation><p id="0"><s id="0">国际 &amp; 贸易</s><s id="1"/></p></translation>'
    assert TranslationClient.decode(response, [['inter', 'national']]) == [['国际 & 贸易', '']]


def test_real_http_contract(api_server):
    endpoint, requests = api_server
    client = TranslationClient('test-model', '术语统一为农场动物', lambda _: None, endpoint=endpoint)
    assert client.translate([['Hello', ' cow']]) == [['译文：Hello', '译文： cow']]
    headers, payload = requests[0]
    assert headers['User-Agent'] == engine.UA
    assert payload['model'] == 'test-model'
    assert payload['chat_template_kwargs']['enable_thinking'] is False
    assert '术语统一为农场动物' in payload['messages'][0]['content']


def test_gateway_timeout_splits_batch_without_losing_text(monkeypatch):
    prompts = []
    def reply(request, timeout):
        prompt = json.loads(request.data)['messages'][0]['content']
        prompts.append(prompt)
        root = ET.fromstring(prompt.split('## Here is the input:\n')[1])
        if len(root) > 1:
            raise urllib.error.HTTPError(request.full_url, 504, 'Timeout', {}, io.BytesIO())
        for span in root.iter('s'):
            span.text = '译文：' + span.text
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': ET.tostring(root, encoding='unicode')}}]}).encode())
    monkeypatch.setattr('urllib.request.urlopen', reply)
    client = TranslationClient('test', on_log=lambda _: None)
    assert client.translate([['First paragraph'], ['Second paragraph']]) == [
        ['译文：First paragraph'], ['译文：Second paragraph']]
    assert len(prompts) == 3


def test_split_spans_retains_original_paragraph_context(monkeypatch):
    prompts = []
    def reply(request, timeout):
        prompt = json.loads(request.data)['messages'][0]['content']
        prompts.append(prompt)
        root = ET.fromstring(prompt.split('## Here is the input:\n')[1])
        if len(root[0]) > 1:
            root[0].remove(root[0][-1])  # A real model may merge styled spans.
        for span in root.iter('s'):
            span.text = '译文：' + span.text
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': ET.tostring(root, encoding='unicode')}}]}).encode())
    monkeypatch.setattr('urllib.request.urlopen', reply)
    client = TranslationClient('test', on_log=lambda _: None)
    assert client.translate([['The little ', 'cow lives here.']]) == [
        ['译文：The little ', '译文：cow lives here.']]
    for prompt in prompts[1:]:
        context = prompt.split('## Here is the input:\n')[0]
        assert 'Full original paragraph' in context
        assert 'The little ' in context and 'cow lives here.' in context


@pytest.mark.parametrize('format', ['docx', 'epub'])
def test_end_to_end_dispatch_api_and_unique_output(format, request, api_server, monkeypatch, tmp_path):
    source = request.getfixturevalue(format)
    endpoint, requests = api_server
    monkeypatch.setattr(engine, 'TranslationClient', lambda model, req, log, cancel, **options: TranslationClient(model, req, log, cancel, endpoint, **options))
    outdir = tmp_path / 'output'
    output = engine.translate(source, 'mock', '术语要求', outdir, on_line=lambda _: None)
    assert output.name == f'book_zh.{format}'
    before = output.read_bytes()
    another = engine.translate(source, 'mock', '另一个要求', outdir, on_line=lambda _: None)
    assert another.name == f'book_zh_2.{format}'
    assert output.read_bytes() == before
    assert len(list(outdir.iterdir())) == 2
    assert '另一个要求' in requests[-1][1]['messages'][0]['content']


def test_no_partial_output_on_failure(epub, tmp_path, monkeypatch):
    class FailingClient:
        def __init__(self, *args, **options): pass
        def translate(self, groups): raise RuntimeError('failure')
    monkeypatch.setattr(engine, 'TranslationClient', FailingClient)
    outdir = tmp_path / 'output'
    with pytest.raises(RuntimeError, match='failure'):
        engine.translate(epub, 'mock', '', outdir, on_line=lambda _: None)
    assert list(outdir.iterdir()) == []


def test_pdf_keeps_babeldoc_route(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    source = tmp_path / 'book.pdf'
    writer = PdfWriter()
    for _ in range(3): writer.add_blank_page(width=595, height=842)
    writer.write(source)
    before = source.read_bytes()
    calls = []
    def fake_pdf(*args):
        calls.append(args)
        result = args[3] / 'book.mono.pdf'
        result.write_bytes(b'%PDF-translated')
        return result
    monkeypatch.setattr(engine, '_translate_pdf', fake_pdf)
    output = engine.translate(source, 'model', 'user requirements', tmp_path, '1-3', lambda _: None)
    assert output.name == 'book_zh.pdf'
    assert calls[0][1:3] == ('model', 'user requirements')
    assert calls[0][4] is None  # Only selected pages are sent to BabelDoc.
    assert source.read_bytes() == before


@pytest.mark.parametrize('body_font', [None, 'song'])
def test_pdf_upgrades_legacy_runtime_before_starting(tmp_path, monkeypatch, body_font):
    from types import SimpleNamespace
    legacy, upgraded = tmp_path / 'old-python', tmp_path / 'new-python'
    current = [legacy]
    installs, commands = [], []
    monkeypatch.setattr(engine, 'find_babeldoc_python', lambda: current[0])
    monkeypatch.setattr(engine, 'supports_pdf_font_selection', lambda py: py == upgraded)

    def install(*args):
        installs.append(True)
        current[0] = upgraded
        return 0

    def run(command, *args):
        commands.append(command)
        (tmp_path / 'book.mono.pdf').write_bytes(b'%PDF-translated')
        return 0

    monkeypatch.setattr(engine, 'install_babeldoc', install)
    monkeypatch.setattr(engine, 'stream', run)
    monkeypatch.setattr(engine, 'make_bridge', lambda *args: SimpleNamespace(
        server_port=12345, shutdown=lambda: None, server_close=lambda: None))
    result = engine._translate_pdf(tmp_path / 'book.pdf', 'model', '', tmp_path,
                                   None, lambda _: None, body_font=body_font)
    assert result == tmp_path / 'book.mono.pdf'
    assert len(installs) == 1
    assert commands[0][0] == str(upgraded)


def test_pages_and_cli_failure(epub, capsys):
    with pytest.raises(ValueError, match='页码'):
        engine.translate(epub, 'model', '', epub.parent, '1')
    with pytest.raises(SystemExit) as exc:
        main([str(epub), '-p', '1'])
    assert exc.value.code == 2
    assert main(['missing.docx']) == 1


def test_missing_calibre_fails_before_api(tmp_path, monkeypatch):
    source = tmp_path / 'book.azw3'
    source.write_bytes(b'book')
    monkeypatch.setattr(engine, 'find_calibre_debug', lambda: None)
    with pytest.raises(RuntimeError, match='Calibre'):
        engine.translate(source, 'model', '', tmp_path / 'output', on_line=lambda _: None)
    assert not list((tmp_path / 'output').iterdir())


def test_cancelled_job_creates_no_output(epub, tmp_path):
    cancel = threading.Event()
    cancel.set()
    output = tmp_path / 'output'
    with pytest.raises(TranslationCancelled):
        engine.translate(epub, 'model', '', output, cancel_event=cancel)
    assert not output.exists()


def test_cancel_stalled_child_process(tmp_path):
    cancel = threading.Event()
    holder = []
    timer = threading.Timer(0.6, cancel.set)
    timer.start()
    try:
        with pytest.raises(TranslationCancelled):
            engine.stream([sys.executable, '-c', 'import time; time.sleep(20)'],
                          lambda _: None, holder=holder, cancel_event=cancel)
    finally:
        timer.cancel()
    assert holder == []


def test_paragraph_without_span_is_accepted_when_unambiguous():
    groups = [["Hello"], ["World"]]
    content = '<translation><p id="0">你好</p><p id="1"><s id="1">世界</s></p></translation>'
    assert TranslationClient.decode(content, groups) == [["你好"], ["世界"]]
    with pytest.raises(ValueError):
        TranslationClient.decode('<translation><p id="0">你好世界</p></translation>', [["Hello ", "World"]])


def flaky_api(monkeypatch, broken):
    """An API that answers 504 for every request containing a word of `broken`; returns the list of prompts."""
    prompts = []

    def reply(request, timeout):
        prompt = json.loads(request.data)['messages'][0]['content'].split('## Here is the input:\n')[1]
        prompts.append(prompt)
        if any(word in prompt for word in broken):
            raise urllib.error.HTTPError(request.full_url, 504, 'Timeout', {}, io.BytesIO())
        root = ET.fromstring(prompt)
        for span in root.iter('s'):
            span.text = '译文：' + span.text
        content = ET.tostring(root, encoding='unicode')
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': content}}]}).encode())
    monkeypatch.setattr('urllib.request.urlopen', reply)
    monkeypatch.setattr(TranslationClient, 'RECOVERY_WAITS', ())
    return prompts


def test_failed_paragraph_is_skipped_and_only_it_is_retried_on_resume(monkeypatch, tmp_path):
    from translation_client import Progress
    broken = ['Second']
    prompts = flaky_api(monkeypatch, broken)
    groups = [['First paragraph'], ['Second paragraph'], ['Third paragraph']]
    logs = []
    client = TranslationClient('test', on_log=logs.append, attempts=1)
    client.progress = Progress(tmp_path / 'saved.jsonl', {'model': 'test'})
    assert client.translate(groups) == [['译文：First paragraph'], ['Second paragraph'], ['译文：Third paragraph']]
    assert client.progress.skipped == 1 and not client.progress.failed
    assert any('跳过并保留原文' in line for line in logs)
    assert Progress.read_meta(tmp_path / 'saved.jsonl') == {'model': 'test'}

    broken.clear()
    del prompts[:]
    client = TranslationClient('test', on_log=logs.append, attempts=1)
    client.progress = Progress(tmp_path / 'saved.jsonl')
    assert client.translate(groups) == [['译文：' + group[0]] for group in groups]
    assert len(prompts) == 1 and 'Second' in prompts[0] and 'First' not in prompts[0]
    assert client.progress.skipped == 0


def test_api_outage_stops_requests_and_keeps_the_source(monkeypatch):
    prompts = flaky_api(monkeypatch, ['paragraph'])
    client = TranslationClient('test', on_log=lambda _: None, attempts=1)
    monkeypatch.setattr(client, 'alive', lambda: False)
    groups = [[f'paragraph {i}'] for i in range(6)]
    assert client.translate(groups) == groups
    assert client.progress.failed and client.progress.skipped == 6
    calls = len(prompts)
    assert client.translate([['another paragraph']]) == [['another paragraph']]
    assert len(prompts) == calls  # the API is not called any more


def test_untranslatable_paragraphs_do_not_stop_the_job(monkeypatch):
    flaky_api(monkeypatch, ['table'])
    client = TranslationClient('test', on_log=lambda _: None, attempts=1)
    groups = [[f'table {i}'] for i in range(5)] + [['Last paragraph']]
    assert client.translate(groups) == groups[:5] + [['译文：Last paragraph']]
    assert not client.progress.failed and client.progress.skipped == 5


def test_partial_output_then_resume_completes_the_book(docx, monkeypatch, tmp_path):
    broken = ['Milk', 'Hello.']  # the API goes down at "Milk"
    flaky_api(monkeypatch, broken)
    monkeypatch.setattr(engine, 'TranslationClient',
                        lambda model, req, log, cancel, **options: TranslationClient(model, req, log, cancel, attempts=1, **options))
    outdir = tmp_path / 'output'
    lines = []
    partial = engine.translate(docx, 'mock', '', outdir, on_line=lines.append)
    assert partial.name == 'book_zh_未完成.docx'
    assert any('接口中断' in line for line in lines)
    assert engine.resume_settings(docx, outdir)['model'] == 'mock'

    broken.clear()
    lines.clear()
    finished = engine.translate(docx, 'mock', '', outdir, on_line=lines.append)
    assert finished.name == 'book_zh.docx'
    assert any('从中断处继续' in line for line in lines)
    assert not engine.progress_path(docx, outdir).exists()
    assert engine.resume_settings(docx, outdir) is None


def test_skipped_paragraph_still_finishes_the_book(docx, monkeypatch, tmp_path):
    flaky_api(monkeypatch, ['Milk'])
    monkeypatch.setattr(engine, 'TranslationClient',
                        lambda model, req, log, cancel, **options: TranslationClient(model, req, log, cancel, attempts=1, **options))
    lines = []
    output = engine.translate(docx, 'mock', '', tmp_path / 'output', on_line=lines.append)
    assert output.name == 'book_zh.docx'
    assert any('已跳过并保留原文' in line for line in lines)
    assert not engine.progress_path(docx, tmp_path / 'output').exists()


def test_total_outage_writes_no_output(docx, monkeypatch, tmp_path):
    flaky_api(monkeypatch, ['<s'])
    monkeypatch.setattr(engine, 'TranslationClient',
                        lambda model, req, log, cancel, **options: TranslationClient(model, req, log, cancel, attempts=1, **options))
    outdir = tmp_path / 'output'
    with pytest.raises(RuntimeError, match='接口不可用'):
        engine.translate(docx, 'mock', '', outdir, on_line=lambda _: None)
    assert list(outdir.iterdir()) == []


def sources(prompts):
    return [span.text for prompt in prompts for span in ET.fromstring(prompt).iter('s')]


def test_concurrent_batches_keep_their_order_and_translate_each_paragraph_once(monkeypatch, tmp_path):
    from translation_client import Progress, translate_all
    prompts = flaky_api(monkeypatch, [])
    client = TranslationClient('mock', workers=8, on_log=lambda line: None)
    client.progress = Progress(tmp_path / 'p.jsonl')
    batches = [[[f'Paragraph {n}.'], ['The same line.']] for n in range(40)]
    lines = []
    results = translate_all(client, batches, on_line=lines.append, progress=(0, 100))
    assert results == [[[f'译文：Paragraph {n}.'], ['译文：The same line.']] for n in range(40)]
    sent = sources(prompts)
    assert len(sent) == len(set(sent)) == 41  # the repeated paragraph went to the API once
    assert len((tmp_path / 'p.jsonl').read_text(encoding='utf-8').splitlines()) == 42
    assert '   并发 8 路调用翻译接口' in lines and lines[-1].startswith('   翻译进度 40/40 批')
    assert client.progress.pending == {}


def test_concurrent_failures_skip_only_the_broken_paragraphs(monkeypatch):
    from translation_client import translate_all
    prompts = flaky_api(monkeypatch, ['Broken'])
    client = TranslationClient('mock', workers=8, attempts=1, on_log=lambda line: None)
    batches = [[[f'Good {n}.'], [f'Broken {n}.' if n % 5 == 0 else f'Fine {n}.']] for n in range(20)]
    results = translate_all(client, batches)
    assert results == [[[f'译文：Good {n}.'], [f'Broken {n}.' if n % 5 == 0 else f'译文：Fine {n}.']] for n in range(20)]
    assert client.progress.skipped == 4 and not client.progress.failed
    assert sum(text == 'Hello.' for text in sources(prompts)) <= 8  # at most a probe per failed attempt


def test_concurrent_outage_stops_every_worker_and_keeps_what_is_done(monkeypatch):
    from translation_client import translate_all
    down = threading.Event()
    prompts = flaky_api(monkeypatch, [])
    answer = urllib.request.urlopen

    def reply(request, timeout):
        if down.is_set() or b'Paragraph 3.' in request.data:
            down.set()
            raise urllib.error.HTTPError(request.full_url, 504, 'Timeout', {}, io.BytesIO())
        return answer(request, timeout)
    monkeypatch.setattr('urllib.request.urlopen', reply)
    client = TranslationClient('mock', workers=4, attempts=1, on_log=lambda line: None)
    batches = [[[f'Paragraph {n}.']] for n in range(30)]
    results = translate_all(client, batches)
    assert client.progress.failed
    assert all(spans in ([f'Paragraph {n}.'], [f'译文：Paragraph {n}.']) for n, (spans,) in enumerate(results))
    assert results[0] == [['译文：Paragraph 0.']] and results[3] == [['Paragraph 3.']]
    assert len(prompts) < 30


def test_stop_ends_a_concurrent_translation(monkeypatch):
    from translation_client import translate_all
    cancel = threading.Event()
    flaky_api(monkeypatch, [])
    answer = urllib.request.urlopen

    def reply(request, timeout):
        if b'Paragraph 5.' in request.data:
            cancel.set()
        return answer(request, timeout)
    monkeypatch.setattr('urllib.request.urlopen', reply)
    client = TranslationClient('mock', workers=4, cancel_event=cancel, on_log=lambda line: None)
    with pytest.raises(engine.TranslationCancelled):
        translate_all(client, [[[f'Paragraph {n}.']] for n in range(200)], cancel)


def test_worker_count_is_kept_within_its_limits():
    assert [engine.worker_count(value) for value in (0, '7', 999, 'abc', None)] == [1, 7, 64, 32, 32]
    assert TranslationClient('mock', workers=999).workers == 64


def test_overload_pauses_every_worker(monkeypatch):
    from translation_client import translate_all
    flaky_api(monkeypatch, [])
    answer = urllib.request.urlopen
    starts, overloaded = [], []

    def reply(request, timeout):
        starts.append(time.monotonic())
        if not overloaded:
            overloaded.append(starts[-1])
            raise urllib.error.HTTPError(request.full_url, 429, 'Too many', {}, io.BytesIO())
        return answer(request, timeout)
    monkeypatch.setattr('urllib.request.urlopen', reply)
    client = TranslationClient('mock', workers=8, on_log=lambda line: None)
    assert translate_all(client, [[[f'Paragraph {n}.']] for n in range(16)])[15] == [['译文：Paragraph 15.']]
    late = [t for t in starts[1:] if t > overloaded[0] + 0.05]
    assert late and min(late) >= overloaded[0] + 0.95  # nobody sent during the 1 s pause


def test_a_failing_job_stops_its_workers(monkeypatch):
    from translation_client import translate_all

    class Client:
        workers = 4
        cancel_event = threading.Event()

        def translate(self, batch):
            if batch == [['bad']]:
                raise ValueError('bad')
            while not self.cancel_event.wait(0.05):
                pass
            raise TranslationCancelled('stopped')
    client = Client()
    with pytest.raises(ValueError):
        translate_all(client, [[['slow']], [['slow']], [['bad']], [['slow']]])
    assert client.cancel_event.is_set()
    assert not [t for t in threading.enumerate() if t.name.startswith('translate')]
