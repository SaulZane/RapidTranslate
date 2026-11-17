"""Runs inside BabelDOC's own Python: patches font mapping + progress output, then runs the BabelDOC CLI."""
import contextlib, logging, sys

import rich.logging

# Plain log lines (no rich tables) so the GUI can show them directly.
rich.logging.RichHandler = lambda **kw: logging.StreamHandler(sys.stdout)

import babeldoc.main as bd_main
from babeldoc_fonts import install

install()

STAGES = {
    "Parse PDF and Create Intermediate Representation": "解析 PDF", "DetectScannedFile": "检测扫描件",
    "Parse Page Layout": "分析页面版面", "Parse Paragraphs": "识别段落", "Parse Formulas and Styles": "识别公式与字体样式",
    "Translate Paragraphs": "翻译段落", "Typesetting": "中文排版", "Add Fonts": "嵌入字体",
    "Generate drawing instructions": "生成绘制指令", "Subset font": "字体子集化", "Save PDF": "保存 PDF",
}


def _progress(config, show_log=False):
    last = {}

    def handler(e):
        t = e["type"]
        if not t.startswith("progress"):
            return
        stage = STAGES.get(e["stage"], e["stage"])
        print(f"@@PROGRESS {e['overall_progress']:.1f}", flush=True)
        if t == "progress_start":
            print(f"▶ {stage}", flush=True)
        elif t == "progress_update":
            cur, tot = e["stage_current"], e["stage_total"]
            step = max(1, tot // 5)
            if cur - last.get(stage, -step) >= step:
                last[stage] = cur
                print(f"   {stage} {cur}/{tot}", flush=True)
        elif t == "progress_end":
            print(f"✔ {stage} 完成", flush=True)

    return contextlib.nullcontext(), handler


bd_main.create_progress_handler = _progress

if __name__ == "__main__":
    import multiprocessing as mp
    mp.freeze_support()
    mp.set_start_method("spawn", force=True)
    sys.argv[0] = "babeldoc"
    bd_main.cli()
