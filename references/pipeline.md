# 生成流程、环境与坑点

## 依赖

- Python 3.10+
- pip：pillow、numpy、imageio-ffmpeg、edge-tts>=7.3.1、openpyxl（解析 .docx 另需 python-docx）
- 字体：C:\Windows\Fonts\msyh.ttc（微软雅黑）优先，其次 simhei.ttf、simsun.ttc
- ffmpeg 由 imageio_ffmpeg 自带，无需单独安装

## 运行环境（重要）

视频生成必须**在沙盒外运行**。沙盒会拒绝新建输出目录与写入帧文件，报错形态为 "hit restricted" / "Not allow operate files"。渲染与编码耗时较长，注意给足超时。

## 流程

1. 准备讲话文本
   - 来源为 Excel 或 Word：`python scripts/extract_speech.py <讲话文件> <speeches.json>`
   - JSON 结构为 `{"班组名": ["完整讲话原文"]}`；也可跳过该步，直接在 config 里写 `speech_text`
   - 抽取出的 key 是表格/文档中的标题原文。config 的 `key` 必须与之完全一致，不一致时手动改名
   - Excel 为推荐输入（表 B 列名称、C 列正文）。docx 走启发式：章节以「中文数字、」开头，条目以「N. 标题」开头，正文段落若含句末标点（。！？；）则不算条目。对结构特殊的文档需人工核对抽取结果
2. 准备背景图。任意尺寸，脚本会 cover 裁剪到 1920x1080
3. 编写 config JSON（复制 assets/config.example.json 修改）
   - 用 `key` + `speech_json` 取文本，或直接给 `speech_text`
   - `stages` 形如 [[阶段名, [[标签, 内容], ...]], ...]，同时决定信息条分阶段切换与条数
   - 输出目录默认 `tts_cache_dir = out_dir/.tts_cache`，`frames_dir = out_dir/.frames`
   - 改 `output_name` 等于换一份产物；改讲话文本长度需清空 frames_dir
4. 生成：在沙盒外执行 `python scripts/make_briefing_video.py <config.json>`
5. 校验产物
   - 文件存在且大于 1MB
   - 视频时长约等于音频总时长
   - 抽帧确认：无左侧黑块、顺序为 信息条-芯片-字幕、字幕完整未被截断

## 坑点清单（均已实际踩过）

1. edge-tts 报 403 WSServerHandshakeError：旧版 Sec-MS-GEC token 被服务端拒绝，升级到 edge-tts 7.3.1 及以上。
2. 合并音频为 0 字节或报 "Failed to find two consecutive MPEG audio frames"：TTS 偶发返回空文件，并以空文件污染缓存。缓存必须按文件大小判定（小于 1000 字节视为无效并重合成）。
3. 音频合并禁用 `-c copy`：会静默失败产出空文件。必须重新编码（libmp3lame 128k）并校验产物大小。
4. ffmpeg 编码静默失败：encode_video 必须检查 returncode、打印 stderr，并在产物缺失时抛错。
5. 帧缓存复用只跳过已存在的帧。讲话文本或音频时长变化而旧帧仍在时，必须清空 frames_dir 重新渲染，否则会出现音画错位与缺帧。
6. 源文件编码：仓库脚本为 UTF-8；若写入中文时出现乱码（被写成 GBK），改用可保证 UTF-8 的写入方式（如 .NET WriteAllText + UTF8Encoding(false)）。
7. DNS：脚本内把 speech.platform.bing.com 硬编码到 150.171.27.10，用于绕过受限网络的解析失败；正常网络可删除该补丁。
8. 字幕必须完整展示（用户硬性要求）。字幕上限为最多 3 行、最大宽 1500、字号 38；不得在未验证的情况下压低这些上限。

## 批量生成

为每个班组各写一份 config，循环调用脚本；统一输出到同一 out_dir，文件名带班组名。建议先出单条给用户审阅，确认后再批量。