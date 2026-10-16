#!/usr/bin/env python3
"""Anyantong pre-shift safety briefing video generator (config driven).

Usage:
    python make_briefing_video.py <config.json>

The config JSON holds every user-visible string (brand, titles, staged info
bars, safety chips) plus all input/output paths. See assets/config.example.json
and references/pipeline.md.

Pipeline: split speech text into sentences -> per-sentence Edge TTS ->
measure durations -> concat audio -> render JPEG frames with Pillow ->
encode H.264 + AAC with ffmpeg.

Requirements: Python 3.10+, pillow, numpy, imageio-ffmpeg, edge-tts>=7.3.1,
openpyxl (only for the extract_speech.py helper). Video generation must run
outside the sandbox so that new directories and writes are permitted.
"""

import argparse
import asyncio
import hashlib
import json
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import imageio_ffmpeg
import edge_tts


# --------------------------------------------------------------------------
# DNS override for the Edge TTS websocket endpoint. Some locked-down networks
# resolve speech.platform.bing.com to an unroutable address. Removing this
# block is safe on normal networks.
# --------------------------------------------------------------------------
_orig_getaddrinfo = socket.getaddrinfo
_DNS_OVERRIDE = {"speech.platform.bing.com": "150.171.27.10"}


def _patched_getaddrinfo(host, *args, **kwargs):
    if host in _DNS_OVERRIDE:
        host = _DNS_OVERRIDE[host]
    return _orig_getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = _patched_getaddrinfo

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

W, H = 1920, 1080
FPS = 30

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]

_fonts = {}


def font(size):
    if size not in _fonts:
        for p in FONT_CANDIDATES:
            if Path(p).exists():
                _fonts[size] = ImageFont.truetype(p, size=size, index=0)
                break
        else:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


F = {
    "brand": font(34),
    "title": font(52),
    "subtitle": font(40),
    "big": font(48),
    "sub": font(28),
    "tag": font(24),
    "body": font(32),
    "small": font(20),
    "caption": font(38),
}


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
def process_text_for_tts(text):
    text = text.replace("\n", "")
    return re.sub(r"\s+", "", text)


def split_into_segments(text):
    """Split speech text on sentence enders, then regroup into 18-28 char cues."""
    sentences = re.split(r"(?<=[\u3002\uff01\uff1f\n])", text)
    segments = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if not current:
            current = sent
            continue
        if len(current) >= 18:
            segments.append(current)
            current = sent
        elif len(current) + len(sent) <= 28:
            current += sent
        else:
            segments.append(current)
            current = sent
    if current:
        segments.append(current)
    return segments


def text_w(draw, text, font_obj):
    bbox = draw.textbbox((0, 0), text, font=font_obj)
    return bbox[2] - bbox[0]


def wrap_text(draw, text, font_obj, max_width):
    if not text:
        return [""]
    lines = []
    current = ""
    for ch in list(text):
        test = current + ch
        if text_w(draw, test, font_obj) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------
# Background preparation
# --------------------------------------------------------------------------
def prepare_background(bg_path):
    """Cover-fit the photo to 1920x1080 and lay down the confirmed tint stack."""
    bg = Image.open(bg_path).convert("RGB")
    iw, ih = bg.size
    scale = max(W / iw, H / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    bg = bg.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - W) // 2
    top = (nh - H) // 2
    bg = bg.crop((left, top, left + W, top + H))
    bg = ImageEnhance.Brightness(bg).enhance(1.05)
    bg = ImageEnhance.Contrast(bg).enhance(1.03)

    overlay = Image.new("RGBA", (W, H), (5, 18, 43, 0))
    od = ImageDraw.Draw(overlay, "RGBA")
    od.rectangle((0, 0, W, H), fill=(2, 8, 26, 100))
    od.rectangle((0, 0, W, H), fill=(4, 14, 34, 25))
    od.ellipse((600, 50, 1800, 800), fill=(36, 126, 200, 28))
    od.ellipse((-200, 650, 700, 1250), fill=(255, 138, 43, 14))
    od.rectangle((1620, 0, W, H), fill=(2, 8, 26, 24))
    # NOTE: no darkening rectangle over the left digital-human area. Adding one
    # produces the black block the user rejected.
    return Image.alpha_composite(bg.convert("RGBA"), overlay)


# --------------------------------------------------------------------------
# Frame rendering. Vertical order is fixed and must not be reordered:
# top brand capsule + main title + subtitle -> stage title + info bars ->
# safety chips -> subtitle bar. Never place chips below the subtitle.
# --------------------------------------------------------------------------
def make_slide(base_bg, seg_text, cfg, frame_idx, total_frames):
    canvas = base_bg.copy()
    draw = ImageDraw.Draw(canvas, "RGBA")

    is_gongkuang = cfg.get("is_gongkuang", False)
    brand_text = cfg.get("brand", "AN")
    title_text = cfg.get("title_gongkuang" if is_gongkuang else "title", "")
    title_color = (220, 40, 50) if is_gongkuang else (255, 255, 255)

    # --- brand capsule ---
    bw = text_w(draw, brand_text, F["brand"])
    bx, by = 60, 28
    draw.rounded_rectangle((bx - 16, by - 6, bx + bw + 16, by + 44), radius=22,
                           fill=(30, 120, 200, 200))
    draw.ellipse((bx - 5, by + 12, bx + 7, by + 24), fill=(255, 255, 255, 220))
    draw.text((bx + 12, by), brand_text, font=F["brand"], fill=(255, 255, 255, 240))

    # --- main title ---
    if title_text:
        tw = text_w(draw, title_text, F["title"])
        tx, ty = (W - tw) // 2, 28
        draw.text((tx + 2, ty + 2), title_text, font=F["title"], fill=(0, 0, 0, 120))
        draw.text((tx, ty), title_text, font=F["title"], fill=title_color)

    # --- subtitle with flanking rules and diamond end caps ---
    subtitle = cfg.get("subtitle", "")
    if subtitle:
        sw = text_w(draw, subtitle, F["subtitle"])
        sx, sy = (W - sw) // 2, 90
        line_y = sy + 20
        line_left_end = sx - 30
        line_right_start = sx + sw + 30
        gold = (255, 200, 80, 200)
        gold_hi = (255, 200, 80, 220)
        draw.line((100, line_y, line_left_end, line_y), fill=gold, width=3)
        draw.polygon([(line_left_end - 8, line_y), (line_left_end, line_y - 6),
                      (line_left_end + 8, line_y), (line_left_end, line_y + 6)], fill=gold_hi)
        draw.line((line_right_start, line_y, W - 100, line_y), fill=gold, width=3)
        draw.polygon([(line_right_start - 8, line_y), (line_right_start, line_y - 6),
                      (line_right_start + 8, line_y), (line_right_start, line_y + 6)], fill=gold_hi)
        draw.text((sx + 2, sy + 2), subtitle, font=F["subtitle"], fill=(0, 0, 0, 120))
        draw.text((sx, sy), subtitle, font=F["subtitle"], fill=(255, 230, 140, 240))

    # --- staged info bars, switched evenly across the whole clip ---
    stages = cfg.get("stages", [])
    if stages:
        stage_count = len(stages)
        frames_per_stage = max(1, total_frames // stage_count)
        current_stage_idx = min(frame_idx // frames_per_stage, stage_count - 1)

        stage_frame = frame_idx % frames_per_stage
        fade_frames = 8
        alpha = 255
        if stage_frame < fade_frames:
            alpha = int(255 * stage_frame / fade_frames)
        elif stage_frame > frames_per_stage - fade_frames:
            alpha = int(255 * (frames_per_stage - stage_frame) / fade_frames)
        alpha = max(100, min(255, alpha))

        stage_name, stage_items = stages[current_stage_idx]

        stw = text_w(draw, stage_name, F["big"])
        stx, sty = (W - stw) // 2, 160
        draw.text((stx + 2, sty + 2), stage_name, font=F["big"], fill=(0, 0, 0, 80))
        draw.text((stx, sty), stage_name, font=F["big"], fill=(255, 255, 255, alpha))

        bar_y, bar_h, bar_gap = 290, 68, 12
        bar_x = 440
        bar_w = W - 440 - 60

        for item_idx, (item_label, item_content) in enumerate(stage_items):
            by_bar = bar_y + item_idx * (bar_h + bar_gap)
            draw.rounded_rectangle((bar_x, by_bar, bar_x + bar_w, by_bar + bar_h),
                                   radius=8, fill=(8, 20, 45, int(alpha * 0.85)))
            draw.rounded_rectangle((bar_x, by_bar, bar_x + 5, by_bar + bar_h),
                                   radius=3, fill=(30, 120, 200, alpha))
            lw = text_w(draw, item_label, F["body"])
            draw.text((bar_x + 18, by_bar + 8), item_label, font=F["body"],
                      fill=(120, 200, 255, alpha))
            lines = wrap_text(draw, item_content, F["sub"], bar_w - lw - 50)
            for line_idx, line in enumerate(lines[:2]):
                ly = by_bar + 8 + line_idx * 30
                if line_idx == 0:
                    draw.text((bar_x + 18 + lw + 12, ly), line, font=F["sub"],
                              fill=(220, 230, 245, alpha))
                else:
                    draw.text((bar_x + 18, ly), line, font=F["sub"],
                              fill=(220, 230, 245, alpha))

        dot_y = 155
        dot_start_x = (W - stage_count * 24) // 2
        for si in range(stage_count):
            dx = dot_start_x + si * 24
            color = (255, 200, 80, 255) if si == current_stage_idx else (100, 100, 120, 150)
            draw.ellipse((dx, dot_y, dx + 14, dot_y + 14), fill=color)

    # --- safety chips: sit just under the info bars, above the subtitle ---
    chips = cfg.get("chips", [])
    chip_colors = cfg.get("chip_colors", [])
    if chips:
        chip_y, chip_h = 620, 44
        total_chip_w = sum(text_w(draw, c, F["sub"]) + 52 for c in chips)
        cx = (W - total_chip_w) // 2
        for ci, chip in enumerate(chips):
            cw = text_w(draw, chip, F["sub"]) + 52
            color = tuple(chip_colors[ci]) if ci < len(chip_colors) else (50, 50, 80, 120)
            draw.rounded_rectangle((cx, chip_y, cx + cw, chip_y + chip_h), radius=22, fill=color)
            draw.text((cx + 26, chip_y + 8), chip, font=F["sub"], fill=(255, 255, 255, 230))
            cx += cw + 16

    # --- subtitle bar, pinned near the bottom, max 3 lines ---
    if seg_text:
        max_sub_w = 1500
        sub_lines = wrap_text(draw, seg_text, F["caption"], max_sub_w)[:3]
        line_h = 52
        sub_bg_h = len(sub_lines) * line_h + 32
        sub_bg_y = H - sub_bg_h - 40
        widest = max(text_w(draw, l, F["caption"]) for l in sub_lines)
        sub_bg_w = min(max_sub_w, widest) + 64
        sub_bg_x = (W - min(max_sub_w, widest)) // 2 - 32
        draw.rounded_rectangle((sub_bg_x, sub_bg_y, sub_bg_x + sub_bg_w, sub_bg_y + sub_bg_h),
                               radius=10, fill=(0, 0, 0, 220))
        draw.rounded_rectangle((sub_bg_x, sub_bg_y, sub_bg_x + 5, sub_bg_y + sub_bg_h),
                               radius=3, fill=(30, 120, 200, 255))
        for li, line in enumerate(sub_lines):
            lw = text_w(draw, line, F["caption"])
            lx = (W - lw) // 2
            ly = sub_bg_y + 16 + li * line_h
            draw.text((lx + 2, ly + 2), line, font=F["caption"], fill=(0, 0, 0, 150))
            draw.text((lx, ly), line, font=F["caption"], fill=(255, 255, 255, 245))

    # --- digital-human area: outline only, NO fill (fill caused the black block) ---
    dh_x, dh_y, dh_w = 20, 139, 400
    dh_h = H - dh_y - 100
    draw.rounded_rectangle((dh_x, dh_y, dh_x + dh_w, dh_y + dh_h), radius=10,
                           outline=(100, 120, 150, 40), width=1)

    return canvas


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------
def get_audio_duration(audio_path):
    r = subprocess.run([FFMPEG, "-i", str(audio_path), "-f", "null", "-"],
                       capture_output=True, text=True, timeout=60)
    for line in r.stderr.split("\n"):
        if "Duration" in line:
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", line)
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return 0.0


def merge_audio(audio_files, output_path):
    """Re-encode on concat. Do NOT use -c copy: it fails silently on mixed
    TTS output and produces an empty file."""
    list_file = Path(output_path).parent / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for af in audio_files:
            f.write(f"file '{af}'\n")
    cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
           "-c:a", "libmp3lame", "-b:a", "128k", str(output_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not Path(output_path).exists() or Path(output_path).stat().st_size < 1000:
        print("  audio merge failed:", flush=True)
        print(r.stderr[-1200:], flush=True)
        raise RuntimeError("merge audio failed")


async def synthesize(text_segments, cache_dir, voice):
    """Synthesize one mp3 per cue with a size-validated cache.

    A cached file smaller than 1000 bytes is treated as invalid and rebuilt.
    An empty TTS response also poisons the cache, so validate size after save.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    audio_files = []
    for i, seg in enumerate(text_segments):
        tts_text = process_text_for_tts(seg)
        h = hashlib.md5(tts_text.encode()).hexdigest()[:10]
        cache_file = cache_dir / f"{h}.mp3"
        if not cache_file.exists() or cache_file.stat().st_size < 1000:
            print(f"  TTS [{i + 1}/{len(text_segments)}]: {tts_text[:30]}...", flush=True)
            ok = False
            for attempt in range(3):
                try:
                    await edge_tts.Communicate(tts_text, voice).save(str(cache_file))
                    if cache_file.exists() and cache_file.stat().st_size >= 1000:
                        ok = True
                        break
                    print(f"    empty TTS output, retry {attempt + 1}", flush=True)
                except Exception as e:
                    print(f"    TTS error: {type(e).__name__}", flush=True)
                await asyncio.sleep(1)
            if not ok:
                raise RuntimeError(f"TTS failed for: {tts_text[:20]}")
        audio_files.append(str(cache_file))
    return audio_files


def encode_video(frames_dir, audio_path, output_path):
    if Path(output_path).exists():
        Path(output_path).unlink()
    cmd = [
        FFMPEG, "-y",
        "-framerate", str(FPS),
        "-i", str(Path(frames_dir) / "frame_%06d.jpg"),
        "-i", str(audio_path),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        str(output_path),
    ]
    print("  ffmpeg encoding...", flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    print(f"  ffmpeg rc={r.returncode} in {time.time() - t0:.0f}s", flush=True)
    if r.returncode != 0:
        print(r.stderr[-2000:], flush=True)
        raise RuntimeError("ffmpeg encode failed")
    if not Path(output_path).exists():
        raise RuntimeError("ffmpeg produced no file")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def load_speech(cfg):
    if cfg.get("speech_text"):
        return cfg["speech_text"]
    speech_json = cfg.get("speech_json")
    key = cfg.get("key")
    if not speech_json or not key:
        raise SystemExit("config must set either speech_text, or both speech_json and key")
    with open(speech_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    if key not in data:
        raise SystemExit(f"key {key!r} not found in {speech_json}; available: {list(data)}")
    entry = data[key]
    return entry[0] if isinstance(entry, list) else str(entry)


async def run(cfg_path):
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    for required in ("background", "out_dir", "output_name"):
        if not cfg.get(required):
            raise SystemExit(f"config missing required key: {required}")

    bg_path = Path(cfg["background"])
    out_dir = Path(cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(cfg.get("tts_cache_dir") or (out_dir / ".tts_cache"))
    frames_dir = Path(cfg.get("frames_dir") or (out_dir / ".frames"))
    frames_dir.mkdir(parents=True, exist_ok=True)

    speech_text = load_speech(cfg)
    voice = cfg.get("voice", "zh-CN-YunyangNeural")
    label = cfg.get("key") or "clip"
    print(f"clip: {label}  chars: {len(speech_text)}", flush=True)

    text_segments = split_into_segments(speech_text)
    print(f"cues: {len(text_segments)}", flush=True)

    for i, s in enumerate(text_segments):
        print(f"  [{i}] {s}")

    base_bg = prepare_background(bg_path)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        audio_files = await synthesize(text_segments, cache_dir, voice)

        durations = [get_audio_duration(af) for af in audio_files]
        total_dur = sum(durations)
        print(f"  total audio: {total_dur:.1f}s", flush=True)

        merged_audio = td / "merged.mp3"
        merge_audio(audio_files, merged_audio)

        total_frames = int(total_dur * FPS) + FPS
        done = len(list(frames_dir.glob("frame_*.jpg")))
        if done >= total_frames:
            print(f"  reusing {done} rendered frames", flush=True)
        else:
            print(f"  rendering frames 0 -> {total_frames}", flush=True)

        seg_boundaries = []
        cur = 0
        for i, dur in enumerate(durations):
            end = cur + int(dur * FPS)
            seg_boundaries.append((cur, end, i))
            cur = end

        for frame_idx in range(total_frames):
            if (frames_dir / f"frame_{frame_idx:06d}.jpg").exists():
                continue
            seg_idx, seg_text = 0, text_segments[0] if text_segments else ""
            for start_f, end_f, i in seg_boundaries:
                if start_f <= frame_idx < end_f:
                    seg_idx, seg_text = i, text_segments[i]
                    break
            else:
                if seg_boundaries and frame_idx >= seg_boundaries[-1][1]:
                    seg_idx = len(text_segments) - 1
                    seg_text = text_segments[-1] if text_segments else ""
            img = make_slide(base_bg, seg_text, cfg, frame_idx, total_frames)
            img.convert("RGB").save(frames_dir / f"frame_{frame_idx:06d}.jpg", quality=95)

        out_path = out_dir / cfg["output_name"]
        encode_video(frames_dir, merged_audio, out_path)

        print(f"  done: {out_path}  {out_path.stat().st_size / 1024 / 1024:.1f} MB", flush=True)
        return out_path


def main():
    ap = argparse.ArgumentParser(description="Anyantong briefing video generator")
    ap.add_argument("config", help="path to the video config JSON")
    args = ap.parse_args()
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
