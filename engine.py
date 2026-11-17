"""Book translation dispatch: BabelDOC for PDF; original package editing otherwise."""
import http.server, json, os, shutil, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from translation_client import (UPSTREAM, UA, DEFAULT_WORKERS, MAX_WORKERS, Progress, TranslationClient,
                                TranslationCancelled, check_cancelled)
from prompt_templates import combine_requirements

MODELS = [("Index-Translate-2B", "2B · 快速"), ("Index-Translate-9B", "9B · 均衡"),
          ("Index-Translate-35B-A3B", "35B-A3B · 旗舰")]
RUNNER = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "bd_runner.py"
AZW3_RUNNER = RUNNER.with_name("azw3_runner.py")
OCR_RUNNER = RUNNER.with_name("ocr_runner.py")
RAPIDOCR_VERSION = '3.10.0'
SUPPORTED_EXTENSIONS = {".pdf", ".epub", ".docx", ".azw3", ".mobi"}
MARK = "## Here is the input:"
BABELDOC_FONT_VERSION = '0.7.1'


def babeldoc_font_runtime():
    app_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    if not (app_dir / 'tools').is_dir() and (app_dir.parent / 'tools').is_dir():
        app_dir = app_dir.parent
    return app_dir / 'tools' / 'BabelDoc-fonts'


BRIDGE_ATTEMPTS = 4
TRANSIENT = {429, 500, 502, 503}  # 504 means the batch is too slow; retrying it would only time out again.


def make_bridge(requirement="", on_log=print):
    """Local OpenAI-compatible endpoint that adds the required User-Agent / no-thinking and the user's requirement."""
    count = [0]

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            p = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            p.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
            if requirement:
                m = p["messages"][-1]
                extra = f"## Additional requirements from the user (follow them strictly)\n{requirement}\n\n"
                m["content"] = m["content"].replace(MARK, extra + MARK, 1) if MARK in m["content"] \
                    else m["content"] + "\n\n" + extra
            req = urllib.request.Request(UPSTREAM + "/chat/completions", json.dumps(p).encode(),
                                         {"Content-Type": "application/json", "User-Agent": UA})
            for attempt in range(BRIDGE_ATTEMPTS):
                try:
                    with urllib.request.urlopen(req, timeout=300) as resp:
                        body, code, reason = resp.read(), resp.code, ""
                except urllib.error.HTTPError as e:
                    with e:
                        body, code, reason = e.read(), e.code, "接口服务器返回错误"
                except Exception as e:
                    body, code = json.dumps({"error": str(e)}).encode(), 502
                    reason = f"网络连接失败（{type(e).__name__}: {e}），请检查网络/代理"
                if code not in TRANSIENT or attempt + 1 == BRIDGE_ATTEMPTS:
                    break
                on_log(f"   接口暂时不可用（HTTP {code}，{reason}），{2 ** attempt} 秒后重试")
                time.sleep(2 ** attempt)
            count[0] += 1
            on_log(f"   接口调用 #{count[0]} → HTTP {code}" + (f"：{reason}" if reason else ""))
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # BabelDOC may have been cancelled while the API was running.

    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def find_babeldoc_python():
    exe = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    local = babeldoc_font_runtime() / exe
    if local.is_file():
        return local
    roots = [Path(os.environ.get("APPDATA", "")) / "uv" / "tools", Path.home() / ".local/share/uv/tools"]
    if uv := shutil.which("uv"):
        try:
            roots.insert(0, Path(subprocess.check_output([uv, "tool", "dir"], text=True).strip()))
        except Exception:
            pass
    for r in roots:
        if (r / "babeldoc" / exe).exists():
            return r / "babeldoc" / exe
    return None


def install_babeldoc(on_line, holder=None, cancel_event=None):
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("未找到 BabelDOC，也未找到 uv。请先安装 uv (https://docs.astral.sh/uv/) 后重试。")
    runtime = babeldoc_font_runtime()
    python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        status = stream([uv, 'venv', '--python', '3.12', str(runtime)], on_line,
                        holder=holder, cancel_event=cancel_event)
        if status:
            return status
    return stream([uv, 'pip', 'install', '--python', str(python),
                   'BabelDOC==' + BABELDOC_FONT_VERSION], on_line,
                  holder=holder, cancel_event=cancel_event)


def supports_pdf_font_selection(python):
    try:
        version = subprocess.check_output(
            [str(python), '-c', "from importlib.metadata import version; print(version('BabelDOC'))"],
            text=True, timeout=15, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()
        return version == BABELDOC_FONT_VERSION
    except (OSError, subprocess.SubprocessError):
        return False


def stream(cmd, on_line, env=None, holder=None, cancel_event=None):
    check_cancelled(cancel_event)
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True,
                         encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if holder is not None:
        holder.append(p)
    finished = threading.Event()

    def watch():
        while not finished.wait(0.1):
            if cancel_event is not None and cancel_event.is_set():
                if p.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        p.kill()
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        for line in p.stdout:
            if line.strip():
                on_line(line.rstrip())
        code = p.wait()
        check_cancelled(cancel_event)
        return code
    finally:
        finished.set()
        if p.poll() is None:
            p.kill()
            p.wait()
        p.stdout.close()
        watcher.join(timeout=2)
        if holder is not None and p in holder:
            holder.remove(p)


def babeldoc_ready(on_line, holder=None, cancel_event=None):
    py = find_babeldoc_python()
    if py is None or not supports_pdf_font_selection(py):
        on_line(f"正在配置支持译文字体的独立 BabelDOC {BABELDOC_FONT_VERSION} 环境（首次需要几分钟）…")
        if install_babeldoc(on_line, holder, cancel_event) != 0 or (py := find_babeldoc_python()) is None:
            raise RuntimeError("BabelDOC 安装失败")
        if not supports_pdf_font_selection(py):
            raise RuntimeError(f'BabelDOC 字体环境未就绪，请安装 BabelDOC {BABELDOC_FONT_VERSION} 后重试。')
    return py


def ocr_pdf(pdf, destination, on_line, holder=None, cancel_event=None):
    """OCR scanned pages into paragraph blocks; returns {'pages': [{'index', 'blocks'}]}."""
    py = babeldoc_ready(on_line, holder, cancel_event)
    if subprocess.run([str(py), '-c', 'import rapidocr'], capture_output=True,
                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode:
        on_line('正在安装 OCR 组件 RapidOCR（首次需要几分钟）…')
        uv = shutil.which('uv')
        if not uv or stream([uv, 'pip', 'install', '--python', str(py), 'rapidocr==' + RAPIDOCR_VERSION],
                            on_line, holder=holder, cancel_event=cancel_event):
            raise RuntimeError('RapidOCR 安装失败，请检查网络或手工安装：uv pip install rapidocr')
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    if stream([str(py), str(OCR_RUNNER), str(pdf), str(destination)], on_line, env, holder, cancel_event):
        raise RuntimeError('OCR 识别失败，详见上方日志')
    return json.loads(Path(destination).read_text(encoding='utf-8'))


def worker_count(value):
    """The number of parallel API requests for a user's input: 1 to MAX_WORKERS, the default when unreadable."""
    try:
        return max(1, min(MAX_WORKERS, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_WORKERS


def progress_path(source, out_dir=None):
    """Where the finished paragraphs of an interrupted job are kept."""
    source = Path(source).expanduser().resolve()
    return Path(out_dir or source.parent).expanduser().resolve() / (source.name + '.翻译进度.jsonl')


def resume_settings(source, out_dir=None):
    """Settings of the interrupted job for this book (keyword arguments of translate), or None."""
    return Progress.read_meta(progress_path(source, out_dir))


def scaled_progress(on_line, start, end):
    """Map a sub-step's 0-100 progress into [start, end] of the whole job."""
    def forward(line):
        if line.startswith('@@PROGRESS'):
            line = f'@@PROGRESS {start + (end - start) * float(line.split()[1]) / 100:.1f}'
        on_line(line)
    return forward


def _translate_layout_pdf(source, original, model, requirement, template, work, on_line, holder,
                          primary_font, cancel_event, body_font, ocr, layout, progress, workers=1):
    """Paragraph typesetting: OCR for scanned pages, PDF text geometry for text pages (if layout)."""
    import pymupdf as fitz
    from font_preferences import pdf_font_path, windows_font
    from ocr_layout import typeset
    pages = {}
    if ocr:
        recognized = ocr_pdf(original, work / 'ocr.json', scaled_progress(on_line, 0, 10),
                             holder, cancel_event)
        pages = {page['index']: page for page in recognized['pages']}
    with fitz.open(source) as doc:
        total = len(doc)
    if layout and len(pages) < total:
        py = babeldoc_ready(on_line, holder, cancel_event)
        native = work / 'native.json'
        env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
        if stream([str(py), str(OCR_RUNNER), str(source), str(native), '--native', str(original)],
                  scaled_progress(on_line, 10 if ocr else 0, 15), env, holder, cancel_event):
            raise RuntimeError('PDF 段落整理失败')
        for page in json.loads(native.read_text(encoding='utf-8'))['pages']:
            pages.setdefault(page['index'], page)
    babeldoc = len(pages) < total
    client = TranslationClient(model, requirement, on_line, cancel_event, workers=workers)
    client.template, client.progress = template, progress
    font_path = pdf_font_path(body_font) if body_font is not None else windows_font('simsun.ttc')
    start = 15 if layout else 10
    end = 55 if babeldoc else 95
    typeset_pdf = typeset(source, [pages[i] for i in sorted(pages)], client, font_path,
                          work / 'paragraph_zh.pdf', scaled_progress(on_line, 0, 100), cancel_event,
                          progress=(start, end))
    if not babeldoc:
        return typeset_pdf
    on_line('▶ 其余页已有文字层，使用 BabelDOC 翻译')
    result = _translate_pdf(source, model, requirement, work, None, scaled_progress(on_line, end, 95),
                            holder, primary_font, cancel_event, body_font, workers)
    if result is None:
        return None
    with fitz.open(result) as merged, fitz.open(typeset_pdf) as typeset_doc:
        for index in sorted(pages):
            merged.delete_page(index)
            merged.insert_pdf(typeset_doc, from_page=index, to_page=index, start_at=index)
        merged.save(work / 'merged.pdf', garbage=3, deflate=True)
    return work / 'merged.pdf'


def _translate_pdf(pdf, model, requirement, out_dir, pages, on_line, holder=None,
                   primary_font="sans-serif", cancel_event=None, body_font=None, workers=4):
    """Returns path of the Chinese PDF, or None on failure."""
    py = babeldoc_ready(on_line, holder, cancel_event)
    check_cancelled(cancel_event)
    bridge = make_bridge(requirement, on_line)
    env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",  # bypass global proxy
           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    env.pop('INDEX_TRANSLATE_BODY_FONT', None)
    if body_font is not None:
        from font_preferences import font_choice
        env['INDEX_TRANSLATE_BODY_FONT'] = body_font
        primary_font = font_choice(body_font)[3]
    cmd = [str(py), str(RUNNER), "--openai", "--openai-model", model, "--openai-api-key", "index",
           "--openai-base-url", f"http://127.0.0.1:{bridge.server_port}/v1",
           "--lang-in", "en", "--lang-out", "zh", "--qps", str(workers), "--no-auto-extract-glossary",
           "--primary-font-family", primary_font, "--no-dual", "--watermark-output-mode", "no_watermark",
           "-o", str(out_dir), "--files", str(pdf)]
    if pages:
        cmd += ["-p", pages]
    if requirement:
        cmd.append("--ignore-cache")  # cache key ignores the requirement
    try:
        code = stream(cmd, on_line, env, holder, cancel_event)
    finally:
        bridge.shutdown()
        bridge.server_close()
    if code != 0:
        return None
    found = sorted(Path(out_dir).glob("*.mono.pdf"), key=lambda f: f.stat().st_mtime)
    return found[-1] if found else None


def find_calibre_debug():
    """Find installed or adjacent portable Calibre without changing system settings."""
    override = os.environ.get("CALIBRE_DEBUG")
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise RuntimeError(f"CALIBRE_DEBUG 指向的程序不存在：{path}")
        return path
    if found := shutil.which("calibre-debug"):
        return Path(found)
    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    candidates = []
    for root in {app_dir, app_dir.parent}:
        candidates.extend([root / "tools/Calibre Portable/Calibre/calibre-debug.exe",
                           root / "Calibre Portable/Calibre/calibre-debug.exe"])
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if value := os.environ.get(variable):
            candidates.extend([Path(value) / "Calibre2/calibre-debug.exe",
                               Path(value) / "calibre/calibre-debug.exe"])
    candidates.append(Path("/Applications/calibre.app/Contents/MacOS/calibre-debug"))
    return next((p for p in candidates if p.is_file()), None)


def _translate_azw3(source, destination, client, on_line, holder, cancel_event,
                    chapters=None, template='none', body_font=None):
    from book_formats import XMLPart, translate_parts
    from code_comments import css_code_classes

    calibre = find_calibre_debug()
    if calibre is None:
        raise RuntimeError("AZW3 / MOBI 需要 Calibre。请安装 Calibre，或将便携版放在 tools/Calibre Portable/；"
                           "也可将 CALIBRE_DEBUG 环境变量设为 calibre-debug.exe 的完整路径。")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    with tempfile.TemporaryDirectory(prefix="azw3-", dir=destination.parent) as tmp:
        bundle = Path(tmp) / "parts.zip"
        command = [str(calibre), "-e", str(AZW3_RUNNER), "--"]
        label = source.suffix[1:].upper()
        on_line(f"▶ 使用 Calibre 读取 {label} 原始内容")
        if stream(command + ["export", str(source), str(bundle)], on_line, env, holder, cancel_event):
            raise RuntimeError(f"Calibre 读取 {label} 失败，详见上方日志")
        metadata = json.loads(Path(str(bundle) + ".json").read_text(encoding="utf-8"))
        with ZipFile(bundle) as archive:
            files = {name: archive.read(name) for name in metadata}
        classes = css_code_classes([data.decode('utf-8', errors='replace') for name, data in files.items()
                                    if metadata[name]['kind'] == 'css'])
        changed, visible = {}, None
        if chapters is not None:
            from ebook_chapters import BookIndex, select_chapters
            opf = next(name for name, item in metadata.items() if item['kind'] == 'opf')
            changed, visible = select_chapters(BookIndex(opf, files), chapters)
        parts = [XMLPart(name, changed.get(name, files[name]), item['kind'], template, classes)
                 for name, item in metadata.items() if item['kind'] in {'html', 'ncx'}
                 and (visible is None or name in visible or name in changed)]
        if not parts:
            raise ValueError(f"{label} 中未找到可编辑的正文")
        translate_parts(parts, client, on_line, cancel_event)
        if body_font is not None:
            from document_fonts import apply_fonts
            for part in parts:
                apply_fonts(part, body_font)
        with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
            changed.update({part.name: part.render() for part in parts})
            for name, data in changed.items():
                if data != files[name]:
                    archive.writestr(name, data)
        on_line(f"▶ 使用 Calibre 保存 {label}，保留原有样式与资源")
        if stream(command + ["apply", str(source), str(bundle), str(destination)],
                  on_line, env, holder, cancel_event):
            raise RuntimeError(f"Calibre 保存 {label} 失败，详见上方日志")
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError(f"Calibre 未生成有效的 {label} 文件")


def save_result(result, source, out_dir, cancel_event=None, tag=''):
    """Publish only a completed result; never replace the source or an earlier translation."""
    check_cancelled(cancel_event)
    result, source, out_dir = Path(result), Path(source), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        suffix = tag + ("" if index == 1 else f"_{index}")
        destination = out_dir / f"{source.stem}_zh{suffix}{source.suffix.lower()}"
        try:
            target = destination.open("xb")
            break
        except FileExistsError:
            index += 1
    try:
        with target, result.open("rb") as src:
            shutil.copyfileobj(src, target)
        check_cancelled(cancel_event)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def translate(pdf, model, requirement, out_dir, pages=None, on_line=print, holder=None,
              primary_font="sans-serif", cancel_event=None, chapters=None, template='none', body_font=None, ocr=False, layout=False,
              workers=DEFAULT_WORKERS):
    """Translate a supported book into the same format. Keeps the original positional API."""
    source = Path(pdf).expanduser().resolve()
    extension = source.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("支持的文件格式为 PDF、EPUB、DOCX、AZW3、MOBI")
    if not source.is_file():
        raise FileNotFoundError(f"输入文件不存在：{source}")
    if pages and extension not in {'.pdf', '.docx'}:
        raise ValueError("页码范围仅适用于 PDF / DOCX；电子书请指定章节。")
    if chapters is not None and extension not in {'.epub', '.mobi', '.azw3'}:
        raise ValueError('章节选择仅适用于 EPUB / MOBI / AZW3')
    if (ocr or layout) and extension != '.pdf':
        raise ValueError('OCR 识别和段落优化仅适用于 PDF')
    meta = {'model': model, 'requirement': requirement, 'pages': pages, 'chapters': chapters, 'template': template,
            'body_font': body_font, 'ocr': ocr, 'layout': layout}
    requirement = combine_requirements(requirement, template)
    if body_font is not None:
        from font_preferences import font_choice, pdf_font_path
        on_line('译文字体：' + font_choice(body_font)[0] + '；代码使用等宽字体')
        if extension == '.pdf':
            pdf_font_path(body_font)  # Fail before API calls when a font is unavailable.
    check_cancelled(cancel_event)
    workers = worker_count(workers)
    on_line(f'接口并发：{workers} 路')
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = Progress(progress_path(source, out_dir), meta)
    if progress.done:
        on_line(f'▶ 发现未完成的翻译进度：已译 {len(progress.done)} 段，从中断处继续')
    with tempfile.TemporaryDirectory(prefix="index-translate-", dir=out_dir,
                                     ignore_cleanup_errors=True) as tmp:  # never mask the real error
        work = Path(tmp)
        original_source = source
        if pages:
            from page_selection import extract_pdf_pages, extract_docx_pages
            selected_source = work / ('selected' + extension)
            on_line('▶ 按原文实际页码提取所选内容')
            if extension == '.pdf':
                selected = extract_pdf_pages(source, selected_source, pages)
            else:
                selected = extract_docx_pages(source, selected_source, pages, cancel_event)
            on_line('   已选择原文页码：' + ','.join(map(str, selected)))
            source = selected_source
        if extension == ".pdf":
            from pdf_code import protect_code, restore_code
            code_client = TranslationClient(model, requirement, on_line, cancel_event, workers=workers)
            code_client.progress = progress
            protected_source, overlay, regions = protect_code(source, work, code_client, template, on_line,
                                                              cancel_event)
            if ocr or layout:
                result = _translate_layout_pdf(protected_source, source, model, requirement, template, work,
                                               on_line, holder, primary_font, cancel_event, body_font,
                                               ocr, layout, progress, workers)
            else:
                result = _translate_pdf(protected_source, model, requirement, work, None, on_line,
                                        holder, primary_font, cancel_event, body_font, workers)
            if result is not None:
                result = restore_code(result, overlay, regions, work)
            if result is None:
                return None
        else:
            try:
                from book_formats import translate_zip
            except ImportError as exc:
                raise RuntimeError("缺少文档处理依赖，请运行：python -m pip install -r requirements.txt") from exc
            client = TranslationClient(model, requirement, on_line, cancel_event, workers=workers)
            client.template, client.progress = template, progress
            result = work / ("translated" + extension)
            try:
                if extension in {".azw3", ".mobi"}:
                    _translate_azw3(source, result, client, on_line, holder, cancel_event, chapters, template, body_font)
                else:
                    translate_zip(source, result, client, extension[1:], on_line, cancel_event, chapters, template, body_font)
            except BadZipFile as exc:
                raise ValueError("文档包损坏或已加密，无法读取；请检查源文件。") from exc
        check_cancelled(cancel_event)
        if progress.failed and not progress.done:
            raise RuntimeError('翻译接口不可用，没有译出任何内容；请检查网络后重试')
        partial = out_dir / f'{original_source.stem}_zh_未完成{original_source.suffix.lower()}'
        try:
            partial.unlink(missing_ok=True)  # superseded by this run
        except OSError:
            pass  # still open in a viewer
        destination = save_result(result, original_source, out_dir, cancel_event, '_未完成' if progress.failed else '')
        if progress.failed:
            on_line(f'⚠ 接口中断，有 {progress.skipped} 段未翻译（保留原文），已输出部分译文。'
                    '进度已保存，排除故障后可继续翻译，只会补译缺少的段落。')
        else:
            if progress.skipped:
                on_line(f'⚠ 有 {progress.skipped} 段接口无法翻译，已跳过并保留原文（见上方日志）')
            progress.path.unlink(missing_ok=True)
    on_line("@@PROGRESS 100")
    return destination


def load_chapters(source, on_line=lambda _: None, cancel_event=None):
    """Load a public chapter tree without making any translation API calls."""
    from ebook_chapters import BookIndex, index_epub
    source = Path(source).expanduser().resolve()
    if source.suffix.lower() == '.epub':
        with ZipFile(source) as archive:
            return index_epub(archive).public()
    if source.suffix.lower() not in {'.azw3', '.mobi'}:
        raise ValueError('此格式不支持章节树')
    calibre = find_calibre_debug()
    if calibre is None:
        raise RuntimeError('读取 MOBI / AZW3 目录需要 Calibre，请安装或配置便携版。')
    with tempfile.TemporaryDirectory(prefix='index-chapters-') as tmp:
        bundle = Path(tmp) / 'parts.zip'
        command = [str(calibre), '-e', str(AZW3_RUNNER), '--', 'export', str(source), str(bundle)]
        env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
        if stream(command, on_line, env=env, cancel_event=cancel_event):
            raise RuntimeError('无法读取电子书目录，详见日志')
        metadata = json.loads(Path(str(bundle) + '.json').read_text(encoding='utf-8'))
        opf = next(name for name, item in metadata.items() if item['kind'] == 'opf')
        with ZipFile(bundle) as archive:
            files = {name: archive.read(name) for name in metadata}
        return BookIndex(opf, files).public()
