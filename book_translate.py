#!/usr/bin/env python3
"""CLI entry point for book translation in the original file format."""
import argparse
from pathlib import Path
import threading

import engine
from prompt_templates import TEMPLATES
from font_preferences import BODY_FONTS


def main(argv=None):
    parser = argparse.ArgumentParser(description="翻译 PDF / EPUB / DOCX / AZW3 / MOBI，按后缀选择处理方式")
    parser.add_argument("file", help="输入书籍路径")
    parser.add_argument("-o", "--output", default=".", help="输出目录（默认当前目录）")
    parser.add_argument("-p", "--pages", help="PDF / DOCX 的原文页码：例如 6-8,9,11")
    parser.add_argument('--chapters', help='电子书目录编号，例如 1.2,3；父章节包含下级章节')
    parser.add_argument('--list-chapters', action='store_true', help='显示电子书目录和编号后退出')
    parser.add_argument('--template', choices=list(TEMPLATES), default='none', help='none / science / fiction / tutorial / magazine')
    parser.add_argument('--font', choices=list(BODY_FONTS), default='song', help='译文正文字体：song / kai / fangsong / hei / yahei；代码单独使用等宽字体')
    parser.add_argument('--ocr', action='store_true', help='PDF：先用 OCR 识别扫描页文字，再翻译排版')
    parser.add_argument('--layout', action='store_true', help='PDF：按分栏、行距和缩进先重组段落再翻译排版（报纸、杂志）')
    parser.add_argument('--restart', action='store_true', help='丢弃上次未完成的翻译进度，从头翻译（默认自动续译）')
    parser.add_argument('--workers', type=int, default=engine.DEFAULT_WORKERS,
                        help=f'同时调用翻译接口的并发路数，1-{engine.MAX_WORKERS}（默认 {engine.DEFAULT_WORKERS}）')
    parser.add_argument("-m", "--model", default=engine.MODELS[-1][0])
    parser.add_argument("-r", "--requirement", default="", help="翻译要求")
    args = parser.parse_args(argv)
    if args.pages and Path(args.file).suffix.lower() not in {'.pdf', '.docx'}:
        parser.error("--pages 仅支持 PDF / DOCX；电子书请使用 --chapters")
    if not 1 <= args.workers <= engine.MAX_WORKERS:
        parser.error(f'--workers 的范围是 1-{engine.MAX_WORKERS}')
    if args.ocr and Path(args.file).suffix.lower() != '.pdf':
        parser.error('--ocr 仅支持 PDF')
    if args.layout and Path(args.file).suffix.lower() != '.pdf':
        parser.error('--layout 仅支持 PDF')
    cancelled = threading.Event()
    try:
        if args.list_chapters:
            for chapter in engine.load_chapters(args.file):
                print('  ' * chapter['depth'] + chapter['id'] + '  ' + chapter['title'])
            return 0
        saved = engine.progress_path(args.file, args.output)
        if args.restart:
            saved.unlink(missing_ok=True)
        chapters = [key.strip() for key in args.chapters.split(',') if key.strip()] if args.chapters is not None else None
        result = engine.translate(args.file, args.model, args.requirement, args.output, args.pages,
                                  lambda line: None if line.startswith("@@") else print(line, flush=True),
                                  cancel_event=cancelled, chapters=chapters, template=args.template, body_font=args.font, ocr=args.ocr, layout=args.layout,
                                  workers=args.workers)
        if result is None:
            print("ERROR 翻译失败，未生成输出文件", flush=True)
            return 1
        if saved.is_file():
            print(f"未完成 → {result}\n再次运行同一命令即可从中断处继续", flush=True)
            return 2
        print(f"saved {result}", flush=True)
        return 0
    except (KeyboardInterrupt, engine.TranslationCancelled):
        cancelled.set()
        print("翻译已停止；已译内容已保存，再次运行同一命令即可继续", flush=True)
        return 130
    except Exception as exc:
        print(f"ERROR {exc}\n已译内容已保存，排除故障后再次运行同一命令即可继续", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
