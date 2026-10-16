---
name: anyantong-video
description: Generate Anyantong pre-shift safety briefing videos from speech text and a background image using Edge TTS narration, staged info bars, safety chips and synced subtitles. Use for 安言通 班前安全讲话 digital-human videos. Do not use for general video editing.
---

# 安言通班前安全讲话视频

按已确认样式，把讲话原文生成为 1920x1080 数字人班前安全讲话视频：Edge TTS 配音、分阶段信息条、安全状态芯片、同步字幕。

## 何时使用

- 用户要求生成或批量生成「班前安全讲话」「异常工况紧急处置」视频
- 用户提供讲话文本（Excel、Word 或纯文本）与背景图，要求产出视频

## 硬约束

- 垂直顺序固定为 信息条 → 安全状态芯片 → 字幕，禁止调换；芯片必须在信息条下方、字幕上方
- 左侧数字人区域禁止任何深色填充或遮罩，否则出现黑块；只保留极淡描边
- 讲话文本必须照搬原文，不得改写、润色或删减
- 字幕必须完整展示，不得截断或遮挡

## 步骤

1. 抽取讲话文本：`python scripts/extract_speech.py <讲话文件> <speeches.json>`
2. 复制 assets/config.example.json 为本次 config，按班组填写 `key`、`stages`（各阶段信息条）、`chips`、`subtitle` 与路径
3. 在沙盒外运行：`python scripts/make_briefing_video.py <config.json>`
4. 先出单条给用户审阅，确认后再批量生成

## 参考

- references/pipeline.md 依赖、运行环境、完整流程与坑点清单
- references/layout-spec.md 坐标、字号、颜色、透明度与背景叠加常量
- scripts/make_briefing_video.py 配置驱动的生成器，唯一入口
- assets/config.example.json 可直接改用的配置样例