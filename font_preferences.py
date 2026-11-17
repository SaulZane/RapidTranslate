"""Shared output font choices; font files stay in the user's Windows install."""
import os
from pathlib import Path

BODY_FONTS = {
    'song': ('宋体', 'SimSun', 'simsun.ttc', 'serif'),
    'kai': ('楷体', 'KaiTi', 'simkai.ttf', 'serif'),
    'fangsong': ('仿宋', 'FangSong', 'simfang.ttf', 'serif'),
    'hei': ('黑体', 'SimHei', 'simhei.ttf', 'sans-serif'),
    'yahei': ('微软雅黑', 'Microsoft YaHei', 'msyh.ttc', 'sans-serif'),
}
CODE_CSS = 'Consolas, "Courier New", "SimSun", "宋体", monospace'


def font_choice(key):
    try:
        return BODY_FONTS[key]
    except KeyError as exc:
        raise ValueError('未知正文字体，请选择宋体、楷体、仿宋、黑体或微软雅黑。') from exc


def body_css(key):
    label, family, _, generic = font_choice(key)
    return f'"{family}", "{label}", "SimSun", "宋体", {generic}'


def windows_font(filename):
    path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / filename
    return path if path.is_file() else None


def pdf_font_path(key):
    label, _, filename, _ = font_choice(key)
    path = windows_font(filename)
    if path is None:
        raise RuntimeError(f'PDF 排版需要本机安装“{label}”字体（{filename}），请选择已安装的字体。')
    return path
