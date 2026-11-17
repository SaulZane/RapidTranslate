"""Index-Translate 书籍翻译器 (tkinter GUI)."""
import queue, subprocess, threading, tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import engine
from prompt_templates import TEMPLATES
from font_preferences import BODY_FONTS

BG, CARD, FG, MUTED, ACCENT, BORDER = "#0f1115", "#181b22", "#e8eaed", "#8b93a1", "#4f8cff", "#2a2f3a"
PLACEHOLDER = "可选：例如“术语统一使用……”“语气正式”。留空则直接正常翻译。"


class ChapterDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title('选择要翻译的章节')
        app.update_idletasks()
        width, height = 700, 560
        x = max(0, min(app.winfo_rootx() + (app.winfo_width() - width) // 2,
                       app.winfo_screenwidth() - width - 20))
        y = max(0, min(app.winfo_rooty() + (app.winfo_height() - height) // 2,
                       app.winfo_screenheight() - height - 80))
        self.geometry(f'{width}x{height}+{x}+{y}')
        self.configure(bg=BG, padx=16, pady=16)
        self.transient(app)
        self.grab_set()
        self.rows = []
        self.checked = set()
        app._label(self, '读取原书目录；点击章节可勾选，选择父章节会包含下级章节。', 10, FG).pack(fill='x')
        self.info = app._label(self, '正在读取目录…')
        self.info.pack(fill='x', pady=8)
        frame = tk.Frame(self, bg=BG)
        frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(frame, show='tree', selectmode='extended')
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<ButtonRelease-1>', self.toggle)
        self.tree.bind('<space>', self.toggle_keyboard)
        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill='x', pady=(12, 0))
        app._button(buttons, '全选', self.select_all).pack(side='left')
        app._button(buttons, '清空', self.clear).pack(side='left', padx=6)
        app._button(buttons, '整本翻译', self.all_book).pack(side='right')
        self.use = app._button(buttons, '使用所选章节', self.confirm, primary=True)
        self.use.pack(side='right', padx=6)
        self.use.config(state='disabled')

    def populate(self, rows, selected):
        self.rows = rows
        self.checked = {row['id'] for row in rows} if selected is None else set(selected)
        for row in rows:
            parent = row['parent'] if self.tree.exists(row['parent']) else ''
            self.tree.insert(parent, 'end', iid=row['id'], text=row['title'], open=True)
        self.use.config(state='normal')
        self.refresh()

    def refresh(self):
        for row in self.rows:
            marker = '☑' if row['id'] in self.checked else '☐'
            self.tree.item(row['id'], text=f"{marker}  {row['id']}  {row['title']}")
        self.info.config(text=f'已选 {len(self.checked)} / {len(self.rows)} 个目录条目；输出所选章节的译文。')

    def set_key(self, key):
        children = {row['id'] for row in self.rows if row['id'] == key or row['id'].startswith(key + '.')}
        if key in self.checked:
            self.checked.difference_update(children)
            # A selected ancestor implies all descendants: clear it when a child is cleared.
            self.checked.difference_update(row['id'] for row in self.rows if key.startswith(row['id'] + '.'))
        else:
            self.checked.update(children)
        self.refresh()

    def toggle(self, event):
        if 'indicator' in self.tree.identify('element', event.x, event.y):
            return
        key = self.tree.identify_row(event.y)
        if key:
            self.set_key(key)

    def toggle_keyboard(self, event):
        if self.tree.focus():
            self.set_key(self.tree.focus())
        return 'break'

    def select_all(self):
        self.checked = {row['id'] for row in self.rows}
        self.refresh()

    def clear(self):
        self.checked.clear()
        self.refresh()

    def all_book(self):
        self.app.set_chapters(None)
        self.destroy()

    def confirm(self):
        if not self.checked:
            messagebox.showinfo('提示', '请至少选择一个章节，或点击“整本翻译”。', parent=self)
            return
        selected = [row['id'] for row in self.rows if row['id'] in self.checked]
        self.app.set_chapters(None if len(selected) == len(self.rows) else selected)
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Index-Translate · 书籍翻译")
        self.geometry("960x880")
        self.minsize(860, 760)
        self.configure(bg=BG, padx=24, pady=18)
        self.document = None
        self.out_file = None
        self.cancel_event = threading.Event()
        self.running = False
        self.closing = False
        self.chapter_rows = None
        self.selected_chapters = None
        self.chapter_dialog = None
        self.chapter_cancel = threading.Event()
        self.loading_chapters = False
        self.q = queue.Queue()
        self.proc = []
        self.model = tk.StringVar(value=engine.MODELS[-1][0])
        self.template = tk.StringVar(value='none')
        self.body_font = tk.StringVar(value=BODY_FONTS['song'][0])
        self.ocr = tk.BooleanVar(value=False)
        self.layout = tk.BooleanVar(value=False)
        self.workers = tk.StringVar(value=str(engine.DEFAULT_WORKERS))
        self.worker_count = engine.DEFAULT_WORKERS
        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self._drain)

    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("Horizontal.TProgressbar", troughcolor=CARD, background=ACCENT, bordercolor=BORDER,
                     lightcolor=ACCENT, darkcolor=ACCENT)

    def _label(self, parent, text, size=10, color=MUTED, bold=False):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color, anchor="w",
                        font=("Microsoft YaHei UI", size, "bold" if bold else "normal"))

    def _button(self, parent, text, cmd, primary=False):
        return tk.Button(parent, text=text, command=cmd, relief="flat", cursor="hand2", bd=0, padx=18, pady=8,
                         bg=ACCENT if primary else CARD, fg="white" if primary else FG,
                         activebackground="#6aa0ff" if primary else BORDER, activeforeground="white",
                         disabledforeground=MUTED, font=("Microsoft YaHei UI", 10, "bold" if primary else "normal"))

    def _build(self):
        self._label(self, "1  选择翻译模型", 10, FG, True).pack(fill="x")
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", pady=(6, 14))
        for mid, name in engine.MODELS:
            tk.Radiobutton(row, text=name, value=mid, variable=self.model, indicatoron=False, relief="flat", bd=0,
                           padx=16, pady=8, bg=CARD, fg=FG, selectcolor=ACCENT, activebackground=BORDER,
                           activeforeground="white", font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(0, 8))

        self._label(self, "2  打开书籍", 10, FG, True).pack(fill="x")
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", pady=(6, 14))
        self.open_btn = self._button(row, "📂 打开书籍", self.pick)
        self.open_btn.pack(side="left")
        self.file_lbl = self._label(row, "未选择文件")
        self.file_lbl.pack(side="left", padx=12)

        self._label(self, "3  选择章节或页数（可选）", 10, FG, True).pack(fill="x")
        scope = tk.Frame(self, bg=BG)
        scope.pack(fill='x', pady=(6, 4))
        self.chapter_btn = self._button(scope, '选择章节', self.show_chapters)
        self.chapter_btn.pack(side='left')
        self.chapter_btn.config(state='disabled')
        self._label(scope, "原文页码（PDF / DOCX）", 10).pack(side="left", padx=(24, 8))
        self.pages = tk.Entry(scope, width=16, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                              disabledbackground=CARD, disabledforeground=MUTED,
                              highlightthickness=1, highlightbackground=BORDER)
        self.pages.pack(side="left", ipady=4)
        self.pages.config(state="disabled")
        self.ocr_check = tk.Checkbutton(scope, text='OCR 识别文字（扫描版 PDF）', variable=self.ocr,
                                        bg=BG, fg=FG, selectcolor=CARD, activebackground=BG,
                                        activeforeground=FG, disabledforeground=MUTED,
                                        font=('Microsoft YaHei UI', 10), state='disabled')
        self.ocr_check.pack(side='left', padx=(16, 0))
        self.layout_check = tk.Checkbutton(scope, text='段落优化（分栏报刊）', variable=self.layout,
                                           bg=BG, fg=FG, selectcolor=CARD, activebackground=BG,
                                           activeforeground=FG, disabledforeground=MUTED,
                                           font=('Microsoft YaHei UI', 10), state='disabled')
        self.layout_check.pack(side='left', padx=(12, 0))
        self.scope_label = self._label(self, '页码留空翻译整本；组合示例：6-8,9,11')
        self.scope_label.pack(fill='x', pady=(0, 14))

        self._label(self, "4  翻译要求（可选）", 10, FG, True).pack(fill="x")
        self.req = tk.Text(self, height=4, bg=CARD, fg=MUTED, insertbackground=FG, relief="flat", wrap="word",
                           highlightthickness=1, highlightbackground=BORDER, padx=10, pady=8,
                           font=("Microsoft YaHei UI", 10))
        self.req.insert("1.0", PLACEHOLDER)
        self.req.bind("<FocusIn>", self._ph_in)
        self.req.bind("<FocusOut>", self._ph_out)
        self.req.pack(fill="x", pady=(6, 8))
        templates = tk.Frame(self, bg=BG)
        templates.pack(fill='x', pady=(0, 12))
        self._label(templates, '提示词模板', 10, FG).pack(side='left', padx=(0, 10))
        for key, (label, _) in TEMPLATES.items():
            tk.Radiobutton(templates, text=label, value=key, variable=self.template,
                           bg=BG, fg=FG, selectcolor=CARD, activebackground=BG, activeforeground=FG,
                           font=('Microsoft YaHei UI', 10)).pack(side='left', padx=8)
        fonts = tk.Frame(self, bg=BG)
        fonts.pack(fill='x', pady=(0, 14))
        self._label(fonts, '译文正文字体', 10, FG).pack(side='left', padx=(0, 12))
        self.font_picker = ttk.Combobox(fonts, textvariable=self.body_font, state='readonly', width=13,
                                       values=[font[0] for font in BODY_FONTS.values()])
        self.font_picker.pack(side='left')
        self._label(fonts, '代码：等宽字体（Consolas）', 10).pack(side='left', padx=18)
        self.workers_box = ttk.Spinbox(fonts, from_=1, to=engine.MAX_WORKERS, textvariable=self.workers, width=4)
        self.workers_box.pack(side='right')
        self._label(fonts, f'接口并发路数（1-{engine.MAX_WORKERS}）', 10, FG).pack(side='right', padx=(0, 8))
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x")
        self.start_btn = self._button(row, "开始翻译", self.start, primary=True)
        self.start_btn.pack(side="left")
        self.stop_btn = self._button(row, "停止", self.stop)
        self.stop_btn.pack(side="left", padx=8)
        self.stop_btn.config(state="disabled")
        self.resume_btn = self._button(row, "继续翻译", self.resume)
        self.resume_btn.pack(side="left")
        self.resume_btn.config(state="disabled")
        self.open_out = self._button(row, "打开输出位置", self.show_out)
        self.open_out.pack(side="right")
        self.open_out.config(state="disabled")

        self.status = self._label(self, "等待输入")
        self.status.pack(fill="x", pady=(14, 4))
        self.bar = ttk.Progressbar(self, maximum=100)
        self.bar.pack(fill="x")
        self.log = tk.Text(self, bg=CARD, fg="#c9d1d9", relief="flat", wrap="word", state="disabled",
                           highlightthickness=1, highlightbackground=BORDER, padx=10, pady=8, font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(6, 0))
        self.log.tag_config("ok", foreground="#56d364")
        self.log.tag_config("err", foreground="#ff7b72")
        self.log.tag_config("step", foreground=ACCENT)

    def _ph_in(self, _):
        if self.req.cget("fg") == MUTED:
            self.req.delete("1.0", "end"); self.req.config(fg=FG)

    def _ph_out(self, _):
        if not self.req.get("1.0", "end").strip():
            self.req.insert("1.0", PLACEHOLDER); self.req.config(fg=MUTED)

    def requirement(self):
        return "" if self.req.cget("fg") == MUTED else self.req.get("1.0", "end").strip()

    def set_chapters(self, selected):
        self.selected_chapters = selected
        self.scope_label.config(text='翻译整本电子书' if selected is None else f'已指定 {len(selected)} 个章节条目（包含所选父章节的下级内容）')

    def show_chapters(self):
        if self.chapter_dialog is not None and self.chapter_dialog.winfo_exists():
            self.chapter_dialog.lift()
            return
        self.chapter_dialog = ChapterDialog(self)
        if self.chapter_rows is not None:
            self.chapter_dialog.populate(self.chapter_rows, self.selected_chapters)

    def read_chapters(self, source, cancel_event):
        try:
            rows = engine.load_chapters(source, lambda line: self.q.put(('line', line)), cancel_event)
            self.q.put(('chapters', (source, rows, None)))
        except Exception as exc:
            self.q.put(('chapters', (source, None, str(exc))))

    def pick(self):
        f = filedialog.askopenfilename(title="选择待翻译书籍", filetypes=[
            ("支持的书籍", "*.pdf *.epub *.docx *.azw3 *.mobi"),
            ("PDF", "*.pdf"), ("EPUB", "*.epub"), ("Word 文档", "*.docx"), ("Kindle 书籍", "*.azw3 *.mobi")])
        if f:
            self.chapter_cancel.set()
            self.chapter_cancel = threading.Event()
            if self.chapter_dialog is not None and self.chapter_dialog.winfo_exists():
                self.chapter_dialog.destroy()
            self.document = Path(f)
            unfinished = engine.progress_path(self.document).is_file()
            self.resume_btn.config(state="normal" if unfinished else "disabled")
            if unfinished:
                self.write('发现这本书上次未完成的翻译进度，点击“继续翻译”可从中断处继续')
            self.chapter_rows, self.selected_chapters = None, None
            self.file_lbl.config(text=self.document.name, fg=FG)
            self.pages.config(state="normal")
            self.pages.delete(0, "end")
            self.ocr.set(False)
            self.layout.set(False)
            for check in (self.ocr_check, self.layout_check):
                check.config(state='normal' if self.document.suffix.lower() == '.pdf' else 'disabled')
            if self.document.suffix.lower() not in {'.pdf', '.docx'}:
                self.pages.config(state="disabled")
                self.chapter_btn.config(state='normal')
                self.scope_label.config(text='正在读取原书目录…')
                self.loading_chapters = True
                self.start_btn.config(state='disabled')
                self.show_chapters()
                threading.Thread(target=self.read_chapters, args=(self.document, self.chapter_cancel), daemon=True).start()
            else:
                self.loading_chapters = False
                self.chapter_btn.config(state='disabled')
                self.start_btn.config(state='normal')
                self.scope_label.config(text='页码留空翻译整本；组合示例：6-8,9,11。输出仅包含指定页面。')

    def write(self, line):
        tag = "err" if any(k in line for k in ("ERROR", "Error", "失败", "Traceback")) else \
              "ok" if line.startswith("✔") or line.startswith("完成") else "step" if line.startswith("▶") else None
        self.log.config(state="normal")
        self.log.insert("end", line + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def _drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "line":
                    if val.startswith("@@PROGRESS"):
                        self.bar["value"] = max(float(self.bar["value"]), min(99, float(val.split()[1])))
                    else:
                        self.write(val)
                        if val.startswith("▶"): self.status.config(text=val[2:] + "…")
                elif kind == "done":
                    self._finish(val)
                    if self.closing:
                        return
                elif kind == 'chapters':
                    source, rows, error = val
                    if source != self.document:
                        continue
                    self.loading_chapters = False
                    self.start_btn.config(state='normal')
                    if error:
                        self.write('ERROR 读取目录失败：' + error)
                        self.scope_label.config(text='目录读取失败；详见日志')
                        self.chapter_btn.config(state='disabled')
                        if self.chapter_dialog is not None and self.chapter_dialog.winfo_exists():
                            self.chapter_dialog.info.config(text=error, wraplength=650)
                    else:
                        self.chapter_rows = rows
                        self.set_chapters(None)
                        if self.chapter_dialog is not None and self.chapter_dialog.winfo_exists():
                            self.chapter_dialog.populate(rows, self.selected_chapters)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def start(self):
        if not self.document:
            return messagebox.showinfo("提示", "请先点击“打开书籍”选择 PDF、EPUB、DOCX、AZW3 或 MOBI 文件")
        if self.loading_chapters:
            return
        saved = engine.progress_path(self.document)
        if saved.is_file():
            answer = messagebox.askyesnocancel("发现未完成的翻译", "这本书上次没有翻译完，进度已保存。\n\n"
                                               "是：按上次的设置继续翻译\n否：丢弃进度，按当前设置重新开始")
            if answer is None:
                return
            if answer:
                return self.resume()
            saved.unlink(missing_ok=True)
        pdf = self.document.suffix.lower() == '.pdf'
        self.run({'model': self.model.get(), 'requirement': self.requirement(),
                  'pages': (self.pages.get().strip() or None) if self.document.suffix.lower() in {'.pdf', '.docx'} else None,
                  'chapters': self.selected_chapters, 'template': self.template.get(),
                  'body_font': next(key for key, font in BODY_FONTS.items() if font[0] == self.body_font.get()),
                  'ocr': self.ocr.get() and pdf, 'layout': self.layout.get() and pdf})

    def resume(self):
        """Continue the interrupted job of this book with the settings it was started with."""
        settings = engine.resume_settings(self.document) if self.document else None
        if settings is None:
            self.resume_btn.config(state="disabled")
            return messagebox.showinfo("提示", "没有找到可以继续的翻译进度")
        self.run(settings, resumed=True)

    def run(self, settings, resumed=False):
        self.out_file = None
        self.running = True
        self.cancel_event = threading.Event()
        self.bar["value"] = 0
        self.log.config(state="normal"); self.log.delete("1.0", "end"); self.log.config(state="disabled")
        self.start_btn.config(state="disabled"); self.stop_btn.config(state="normal"); self.open_out.config(state="disabled")
        self.resume_btn.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.chapter_btn.config(state='disabled')
        if resumed:
            self.write('继续上次未完成的翻译（沿用上次的设置）')
        self.write(f"文件：{self.document}\n模型：{settings['model']}\n翻译要求：{settings['requirement'] or '（无，正常翻译）'}\n")
        self.write(f"提示词模板：{TEMPLATES[settings['template']][0]}")
        if settings['pages']:
            self.write(f"页码：{settings['pages']}")
        self.status.config(text="准备中…", fg=MUTED)
        self.proc.clear()
        self.pages.config(state="disabled")
        self.ocr_check.config(state='disabled')
        self.layout_check.config(state='disabled')
        self.workers_box.config(state='disabled')
        self.worker_count = engine.worker_count(self.workers.get())
        self.workers.set(str(self.worker_count))
        if settings['layout']:
            self.write('段落优化：开启（按分栏和缩进重组段落后翻译）')
        if settings['ocr']:
            self.write('OCR 识别：开启（RapidOCR · PP-OCRv6）')
        threading.Thread(target=self.work, args=(self.document, settings), daemon=True).start()

    def work(self, source, settings):
        try:
            res = engine.translate(source, settings['model'], settings['requirement'], source.parent, settings['pages'],
                                   lambda l: self.q.put(("line", l)), self.proc, cancel_event=self.cancel_event, workers=self.worker_count,
                                   **{key: settings[key] for key in ('chapters', 'template', 'body_font', 'ocr', 'layout')})
            self.q.put(("done", res))
        except engine.TranslationCancelled:
            self.q.put(("line", "翻译已停止；已译内容已保存，点击“继续翻译”可从中断处继续"))
            self.q.put(("done", None))
        except Exception as e:
            self.q.put(("line", f"ERROR {e}"))
            self.q.put(("line", "已译内容已保存；排除故障后点击“继续翻译”可从中断处继续"))
            self.q.put(("done", None))

    def stop(self):
        self.cancel_event.set()
        self.stop_btn.config(state="disabled")
        self.status.config(text="正在停止…")
        self.write("已请求停止；正在等待当前接口返回或结束排版进程…")

    def close(self):
        self.chapter_cancel.set()
        if self.running:
            self.closing = True
            self.stop()
            self.withdraw()
        else:
            self.destroy()

    def _finish(self, dst):
        self.running = False
        if self.closing:
            self.destroy()
            return
        self.start_btn.config(state="normal"); self.stop_btn.config(state="disabled")
        self.open_btn.config(state="normal")
        self.workers_box.config(state='normal')
        if self.document and self.document.suffix.lower() in {'.pdf', '.docx'}:
            self.pages.config(state="normal")
            if self.document.suffix.lower() == '.pdf':
                self.ocr_check.config(state='normal')
                self.layout_check.config(state='normal')
        elif self.chapter_rows is not None:
            self.chapter_btn.config(state='normal')
        unfinished = bool(self.document) and engine.progress_path(self.document).is_file()
        self.resume_btn.config(state="normal" if unfinished else "disabled")
        if dst and unfinished:
            self.out_file = dst
            self.status.config(text="部分完成：排除故障后点击“继续翻译”", fg="#d29922")
            self.write(f"部分译文 → {dst}")
            self.open_out.config(state="normal")
        elif dst:
            self.out_file = dst
            self.bar["value"] = 100
            self.status.config(text="翻译完成", fg="#56d364")
            self.write(f"完成 → {dst}")
            self.open_out.config(state="normal")
        else:
            self.status.config(text="未完成（失败或已停止）", fg="#ff7b72")

    def show_out(self):
        subprocess.Popen(["explorer", "/select,", str(Path(self.out_file))])


if __name__ == "__main__":
    import multiprocessing; multiprocessing.freeze_support()
    App().mainloop()
