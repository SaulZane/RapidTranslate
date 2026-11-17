# RapidTranslate

英文书籍与文档翻译为简体中文的 Windows 桌面工具，提供 GUI 与命令行。
支持 **PDF / DOCX / EPUB / AZW3 / MOBI**，使用 Index-Translate 在线接口。
本项目不是接口提供方的官方客户端。

## 下载 v1.0

[下载 Windows exe](https://github.com/SaulZane/RapidTranslate/releases/download/v1.0/RapidTranslate-v1.0-windows.exe) · [Release 页面](https://github.com/SaulZane/RapidTranslate/releases/tag/v1.0)

下载 `RapidTranslate-v1.0-windows.exe`，放在有写入权限的文件夹运行，无需为 exe 单独安装 Python。
GitHub 的 Source code 下载项是源码，不是可执行程序。

**exe 不包含外部工具与 OCR 模型，不是离线全功能包：**
- PDF：需要 BabelDOC。先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，确保 `uv` 在 PATH 中，重启程序后自动配置 Python 3.12 / BabelDOC 环境。
- OCR：首次使用还会下载 RapidOCR 和模型，需要联网。
- AZW3 / MOBI：安装 [Calibre](https://calibre-ebook.com/download_windows)。
- DOCX 按页翻译：需要 Microsoft Word；整本 DOCX 无需 Word。
- 使用 Windows 系统中文字体；其他操作系统尚未完整验证。
- exe 未做代码签名，Windows 可能提示未知发布者；请核对 Release 的 SHA-256。

构建配置和界面目前保留原名称 `IndexTranslate-Books` / Index-Translate。

## 功能与使用

1. 打开书籍，选择模型、页码或电子书章节（留空表示整本）。
2. 分栏报刊 PDF 可勾选「段落优化」；扫描件勾选「OCR 识别文字」。
3. 设置字体、提示词模板和并发数：默认 **32**，范围 **1–64**；接口不稳定时建议降至 8–16。
4. 点击「开始翻译」，输出默认在原文旁：`书名_zh.扩展名`，原书不会被覆盖。
5. 未完成时可「继续翻译」（支持范围见下文）。停止后已发送的请求可能需要等待返回或超时。

支持跨栏句子接续、代码保护、重复段落复用、错峰请求、自动重试与部分路径的断点续译。
机器翻译、OCR 和排版均可能出错；失败段落可能保留原文，请检查日志并校对。

## 隐私与使用边界

**待翻译文字会发送至 `https://index-translate.bilibili.com/v1/chat/completions`。**
不要提交机密、敏感个人信息或无权处理的文档。当前接口实现无需填写 API Key，
但接口可用性、限流与使用条款由服务提供方决定，本项目不保证长期可用。
进度 JSONL 包含译文，请按文档的保密级别保管。不支持 DRM 移除。

## 从源码启动

建议 Windows + Python 3.12：

```powershell
git clone https://github.com/SaulZane/RapidTranslate.git
cd RapidTranslate
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python gui.py
```

```powershell
python book_translate.py book.pdf -o output --layout --workers 32
python book_translate.py scan.pdf -o output --ocr --pages '1-5'
python book_translate.py book.epub -o output --workers 16
```

## 格式与排版

| 格式 | 处理方式 | 部分翻译 |
| --- | --- | --- |
| PDF | BabelDoc 0.7.1 解析与排版，适配中文字体和粗斜体 | 按 PDF 的物理页序提取所选页面，输出仅含选中页 |
| DOCX | lxml 回写原 OOXML 文字节点 | 通过本机 Microsoft Word 计算原文真实分页，再提取所选页内容 |
| EPUB | lxml 回写原 ZIP 内的 XHTML / SVG / NCX 文字 | EPUB3 导航 / NCX 目录树，支持同一 HTML 内的章节锚点 |
| AZW3 | Calibre Edit Book 容器，回写原内容后保存 AZW3 | 原书目录树选择 |
| MOBI | Calibre 原生 MOBI 读取与写入，保留 old / both / new 子类型 | 原书目录树选择，包括 MOBI6 二进制目录 |

保留的是文档结构、样式和排版设置：DOCX 的段落、字符样式、粗斜体、表格、页眉页脚、图片和关系；电子书的 CSS、标签、图片、字体、封面与链接资源。EPUB / DOCX 中未修改的资源内容按字节复制。AZW3 / MOBI 保存时会重建二进制容器；MOBI6 本身的样式能力有限，不能保证复杂样式与所有阅读器中的原显示完全一致。

**译文长度和中文字体会影响自动换行、表格高度和分页，因此不能承诺逐页逐像素不变。** 电子书本身还会随阅读器、字号和屏幕尺寸重排。PDF 继续由 BabelDoc 处理复杂版面。

## 译文字体

界面和命令行默认使用宋体，可选择宋体、楷体、仿宋、黑体、微软雅黑。仅为译文和识别出的代码设置字体，保留原有字号、段落、表格、图片及其他排版设置。

- DOCX / EPUB / AZW3 / MOBI：正文使用所选字体；代码使用 Consolas，中文注释使用宋体，电子书还提供 Courier New / monospace 等回退字体。字体文件不嵌入这些格式，阅读器需安装相应字体；阅读器设置及 MOBI6 的格式限制可能影响最终字体。
- PDF：从本机 Windows 字体目录读取实际字体，交给 BabelDoc 测量、排版并嵌入输出。所选字体缺失时会在翻译前提示。粗体优先使用微软雅黑粗体，斜体优先使用楷体，以保留强调层次。
- PDF 中识别出的代码保留原等宽字形、位置和缩进；中文注释使用宋体。

## 页码与章节规则

- 页码从 1 开始，表示文件中的物理页序，不是正文印刷页码或罗马数字页码。接受 `1-3,5,8-10`，自动去重并按原文顺序输出；倒序、0、越界和无效输入会报错。
- DOCX 整本翻译不需要 Word；**DOCX 按页翻译需要 Windows 上能正常启动的 Microsoft Word**。页边界以本机 Word、已安装字体和排版设置为准，提取后译文可重新分页。复杂跨页表格和分节内容建议核对输出。
- 电子书选择目录项后，输出阅读顺序与目录只包含所选内容。为保留链接与图片等依赖，包内可能保留未选中的资源文件；这些内容不提交翻译，也不进入正常阅读顺序。此功能不等同于敏感信息彻底移除工具。
- 没有导航目录时，尝试从正文标题生成目录；只有正文文件时按其阅读顺序提供选择。原书为扁平目录时不会凭空生成层级。
- 关闭目录窗口会保留当前选择；首次打开的默认范围为整本。

## 提示词模板

| 选项 | 翻译要求 |
| --- | --- |
| 科技类 | 信、达、雅；准确保留事实、公式、数值与单位；使用可靠中文术语，无准确中文解释的术语保留原文 |
| 小说类 | 忠实情节与语气，语言流畅优美；人名、地名、称谓保持一致；建议手工提供人物与术语表 |
| 教程类 | 平实易懂；程序代码、字符串、变量、路径保持原样，仅翻译自然语言注释，保留注释符号、缩进与换行 |
| 杂志类 | 根据文章内容与发表时的时事背景翻译，语言平实流畅、信达雅；保留原格式与层级，不擅自补充或更新事实 |

手工要求与模板一起传给接口，明确声明手工要求优先。可填写术语对照，如 `Mira Stone 统一译为“米拉·斯通”，Silver Bay 统一译为“银湾”`。模型仍可能出现译名或语义错误，长篇小说建议提供人物表并校对。

杂志类模板要求结合文章发表时的背景理解内容，不会联网检索新闻，也不会把原文事实更新为当前信息。

代码保护使用词法分析器识别注释。EPUB / Kindle 识别代码标签、语言提示和常见 CSS 等宽字体；DOCX 识别代码样式和常见等宽字体；PDF 对可识别的等宽代码区域保留原字形与坐标，只替换注释。图片中的代码、未标注且无法识别的代码、特殊语言或异常排版不能保证自动识别。无模板时，已识别的代码块整体保留。

## 依赖

源码运行建议使用 Python 3.12：

```powershell
python -m pip install -r requirements.txt
python gui.py
```

PDF 使用 BabelDoc 0.7.1。程序优先使用 `tools/BabelDoc-fonts` 独立环境；缺失或版本不兼容时，会通过已安装的 uv 配置该环境。已适配新版原生排版流程：先嵌入所选 Windows 字体，再由 BabelDoc 使用相同字形测量、排版并输出，同时保留 PDF 标准字体的粗斜体。可手工配置：

```powershell
uv venv --python 3.12 tools/BabelDoc-fonts
uv pip install --python tools/BabelDoc-fonts/Scripts/python.exe 'BabelDOC==0.7.1'
```

AZW3 / MOBI 需要 Calibre。程序会查找 PATH、默认安装位置和程序相邻或上一级的 `tools/Calibre Portable/Calibre/calibre-debug.exe`。也可指定：

```powershell
$env:CALIBRE_DEBUG = 'C:\Program Files\Calibre2\calibre-debug.exe'
```

OCR 使用 RapidOCR 3.10.0（PP-OCRv6 中型检测 + 英文识别模型，onnxruntime CPU），安装在 BabelDoc 环境中；首次勾选 OCR 时自动安装并下载模型（需联网）。可手工安装：

```powershell
uv pip install --python tools/BabelDoc-fonts/Scripts/python.exe 'rapidocr==3.10.0'
```

识别以 200 DPI 进行，四核 CPU 上密排报纸约 45 秒/页、杂志约 10–40 秒/页；页码范围只识别所选页面。表格按单元格文字识别，表格线和公式保留为原图。

不支持受 DRM 保护或无法解析的加密文件；不进行 DRM 移除。未勾选 OCR 时，图片文字、嵌入对象和书籍元数据不做 OCR 或翻译。XML 损坏时停止，不通过自动修复改变原结构。

## 命令行

```powershell
python book_translate.py book.pdf -o output --pages '6-8,9,11' --template science
python book_translate.py book.docx -o output --pages '1,3-5' --template tutorial
python book_translate.py book.epub --list-chapters
python book_translate.py book.epub -o output --chapters '1.2,3' --template fiction -r '人名与地名采用指定译名'
python book_translate.py book.mobi --list-chapters
python book_translate.py book.mobi -o output --chapters '2,5' --template tutorial
python book_translate.py book.azw3 -o output --template science
python book_translate.py scan.pdf -o output --ocr --template magazine
python book_translate.py magazine.epub -o output --template magazine --font kai
```

章节编号以该文件的 `--list-chapters` 为准；父编号包含其后代。`--pages` 仅支持 PDF / DOCX，`--chapters` 仅支持电子书，`--ocr` 仅支持 PDF。原 `pdf_translate.py` 入口仍兼容，支持全部五种格式。

`--font` 可选 `song`（宋体，默认）、`kai`（楷体）、`fangsong`（仿宋）、`hei`（黑体）、`yahei`（微软雅黑）；代码字体单独保持等宽。

## 测试与构建

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m PyInstaller --noconfirm IndexTranslate-Books.spec
```

`tests/` 使用模拟接口和合成文档，部分集成测试需要 Calibre / Word。
发布前 Windows 完整测试结果：102 passed。
`scripts/create_samples.py` 生成自编样例；smoke 脚本的 `--live` 会调用真实接口。
本地书籍、译文、样例与运行日志不随仓库公开。

勾选「段落优化（分栏报刊）」（命令行 `--layout`）后，有文字层的 PDF 也会按栏位、行距和首行缩进先重组段落，再整段翻译排版，无需 OCR；不勾选时仍由 BabelDOC 处理。两项可同时勾选。进度按整份文档累计，进度条上方显示当前阶段、页码和翻译批次，保存完成后才到 100%。

跨栏接续：一栏末尾没有结束的句子（含行末连字符断词）会与右侧下一栏开头不缩进/小写起始的段落接成一段整体翻译，译文再按各栏高度比例分回原位置；大标题和“Continued on Page”转版行作为文章边界，不会跨越。

## 异常处理与断点续译

- 每译完一段立即写入进度文件 `<书名>.翻译进度.jsonl`（与输出同目录），可恢复已经成功写入的译文。
- 某一批失败时自动改为逐段翻译；接口正常但个别段落始终失败时，跳过该段并保留原文，继续往下翻译。
- 接口整体不可用时，依次等待 30、60、120 秒重试；仍不可用则停止请求，把已译内容排版输出为 `<书名>_zh_未完成.<后缀>`（未译部分保留原文）。
- 排除故障后，在界面点击「继续翻译」，或在命令行再次运行同一命令，会沿用上次的设置，只补译缺少的段落。命令行加 `--restart` 可丢弃进度从头翻译。
- 全部译完后进度文件自动删除。断点续译适用于「段落优化」/ OCR 的 PDF 以及 EPUB、DOCX、AZW3、MOBI；未勾选这两项的 PDF 由 BabelDOC 翻译，不支持续译。

## 接口并发

- 翻译请求默认 32 路并发，可在界面「接口并发路数」或命令行 `--workers` 调整，范围 1-64；填 1 即逐批串行。
- 同一段文字只会由一路翻译：各路先登记自己要译的段落，重复出现的段落等先登记的一路译完后直接取用。
- 请求按固定间隔依次发出，不会瞬间齐发；接口返回 429 / 502 / 503 时所有路一起暂缓。
- 异常处理与串行时相同：失败的批次逐段重试，个别段落跳过并保留原文；接口整体不可用时只由一路探测和等待，其余各路停下，随后保存进度并输出已译内容。
- 「段落优化」/ OCR 的 PDF 把所有页的段落放进同一个队列并发翻译，译完后逐页排版；未勾选这两项的 PDF 由 BabelDOC 按同一路数并发。
- 日志约每 2 秒输出一行「翻译进度 已完成/总批数 · 已用时间 · 预计剩余」。
