"""Threads の投稿文から、インスタのリール用の縦長動画（1080x1920）を作る。

使い方:
  python make_reel.py <posts.csv の id> [出力ファイル.mp4]
文章を空行で区切ってスライドにし、最後にドライブプラスの案内スライドを付ける。
BGM はプログラムで合成した音なので、著作権の心配はない。
"""

import csv
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import wave

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920
FPS = 30
FADE = 0.3

# 色
BG_TOP = (232, 244, 253)
BG_BOTTOM = (255, 255, 255)
NAVY = (20, 50, 100)
ACCENT = (0, 120, 200)
TEXT = (30, 30, 40)

FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\meiryob.ttc",
    r"C:\Windows\Fonts\YuGothB.ttc",
    # Ubuntu（GitHub Actions で fonts-noto-cjk を入れる）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise RuntimeError("日本語フォントが見つかりません")


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# 絵文字はフォントで出ないので、記号に置きかえるか消す
EMOJI_MAP = {"✅": "✔", "⚠️": "！", "⚠": "！", "❌": "×", "⭕": "○", "👉": "→", "▶": "→"}
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]"
)


def clean_line(line):
    for k, v in EMOJI_MAP.items():
        line = line.replace(k, v)
    return EMOJI_RE.sub("", line).strip()


def split_slides(text):
    """空行で区切ってスライドにする。リンクの行は消す（インスタの本文ではリンクを押せないため）。"""
    blocks = re.split(r"\n\s*\n", text.strip())
    slides = []
    for b in blocks:
        lines = []
        for ln in b.splitlines():
            if "http" in ln:
                continue
            c = clean_line(ln)
            if c in ("詳しくはこちら", "詳しくはnoteで", "→"):
                continue
            if c:
                lines.append(c)
        if lines:
            slides.append(lines)
    # 短いブロックは次とまとめる（1枚に最大7行）
    merged = []
    for s in slides:
        if merged and len(merged[-1]) + len(s) <= 5:
            merged[-1] = merged[-1] + [""] + s
        else:
            merged.append(s)
    return merged


def wrap(draw, line, fnt, maxw):
    out, cur = [], ""
    for ch in line:
        # 「。」「、」などは行の頭に置かない（前の行にぶら下げる）
        if ch in "。、，．」』）！？ーっゃゅょ":
            cur += ch
        elif draw.textlength(cur + ch, font=fnt) > maxw and cur:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return out


def background():
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = tuple(int(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)
    # 上の帯
    d.rectangle([0, 0, W, 230], fill=NAVY)
    f = font(52)
    title = "運転のコツ ／ ドライブプラス"
    d.text(((W - d.textlength(title, font=f)) / 2, 120), title, font=f, fill="white", anchor="lm")
    # 下の帯
    d.rectangle([0, H - 200, W, H], fill=NAVY)
    f2 = font(40)
    foot = "ドライブプラス 代表指導員 山田"
    d.text(((W - d.textlength(foot, font=f2)) / 2, H - 100), foot, font=f2, fill="white", anchor="lm")
    return img


def render_slide(lines, path, page, total, first=False):
    img = background()
    d = ImageDraw.Draw(img)
    maxw = W - 160
    area_top, area_bottom = 300, H - 300
    sizes = (78, 72, 66, 60, 54, 50, 46, 42, 40)

    def layout(size):
        f = font(size)
        wrapped = []
        for i, ln in enumerate(lines):
            wrapped += [(w, i == 0 and first) for w in wrap(d, ln, f, maxw)] if ln else [("", False)]
        return f, wrapped, int(size * 1.55)

    # まずは「どの行も折り返さない」大きさを探す。無理なら折り返して入る大きさ。
    chosen = None
    for size in sizes:
        f, wrapped, line_h = layout(size)
        fits = len(wrapped) * line_h <= area_bottom - area_top
        no_wrap = len(wrapped) == len(lines)
        if fits and (no_wrap or size <= 50):
            chosen = (f, wrapped, line_h)
            break
    if chosen is None:
        for size in sizes:
            f, wrapped, line_h = layout(size)
            if len(wrapped) * line_h <= area_bottom - area_top:
                break
        chosen = (f, wrapped, line_h)
    f, wrapped, line_h = chosen
    y = area_top + ((area_bottom - area_top) - len(wrapped) * line_h) / 2
    for txt, is_head in wrapped:
        if txt:
            color = ACCENT if is_head else TEXT
            x = (W - d.textlength(txt, font=f)) / 2
            d.text((x, y + line_h / 2), txt, font=f, fill=color, anchor="lm")
        y += line_h
    # ページ表示
    fp = font(34)
    pg = f"{page} / {total}"
    d.text(((W - d.textlength(pg, font=fp)) / 2, H - 250), pg, font=fp, fill=(120, 130, 150), anchor="lm")
    img.save(path)


def render_cta(path):
    img = background()
    d = ImageDraw.Draw(img)
    rows = [
        ("出張ペーパードライバー講習", 70, NAVY),
        ("", 40, TEXT),
        ("運転の不安、", 62, TEXT),
        ("いっしょに解消しましょう", 62, TEXT),
        ("", 40, TEXT),
        ("ドライブプラス", 96, ACCENT),
        ("", 40, TEXT),
        ("くわしくはプロフィールのリンクから", 50, TEXT),
    ]
    total_h = sum(int(s * 1.6) for _, s, _ in rows)
    y = (H - total_h) / 2
    for txt, s, c in rows:
        f = font(s)
        if txt:
            d.text(((W - d.textlength(txt, font=f)) / 2, y + s * 0.8), txt, font=f, fill=c, anchor="lm")
        y += int(s * 1.6)
    img.save(path)


def make_bgm(path, seconds):
    """やさしいコード進行の音（自前で合成）"""
    sr = 44100
    chords = [  # C - G - Am - F
        [261.63, 329.63, 392.00],
        [246.94, 293.66, 392.00],
        [220.00, 261.63, 329.63],
        [174.61, 220.00, 261.63],
    ]
    beat = 2.0  # 1コード2秒
    n = int(sr * seconds)
    frames = bytearray()
    for i in range(n):
        t = i / sr
        ci = int(t / beat) % len(chords)
        tt = t % beat
        env = min(1.0, tt / 0.4) * (0.6 + 0.4 * math.exp(-tt * 1.5))
        v = 0.0
        for k, fr in enumerate(chords[ci]):
            v += math.sin(2 * math.pi * fr * t) + 0.3 * math.sin(2 * math.pi * fr * 2 * t)
        # アルペジオ（0.5秒ごとに1音）
        note = chords[ci][int(tt / 0.5) % 3] * 2
        nt = tt % 0.5
        v += 1.2 * math.sin(2 * math.pi * note * t) * math.exp(-nt * 6)
        master = min(1.0, t / 1.0, (seconds - t) / 1.5)
        s = int(max(-1, min(1, v * env * 0.08 * master)) * 32767)
        frames += struct.pack("<h", s)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


def slide_seconds(lines):
    chars = sum(len(l) for l in lines)
    return max(3.0, min(7.0, 1.2 + chars * 0.11))


def build(text, out_path):
    slides = split_slides(text)
    tmp = tempfile.mkdtemp()
    try:
        imgs, durs = [], []
        total = len(slides)
        for i, s in enumerate(slides):
            p = os.path.join(tmp, f"s{i:02d}.png")
            render_slide(s, p, i + 1, total, first=(i == 0))
            imgs.append(p)
            durs.append(slide_seconds(s))
        p = os.path.join(tmp, "cta.png")
        render_cta(p)
        imgs.append(p)
        durs.append(4.0)
        length = sum(durs)
        bgm = os.path.join(tmp, "bgm.wav")
        make_bgm(bgm, length)

        cmd = [ffmpeg_exe(), "-y", "-loglevel", "error"]
        for img, dur in zip(imgs, durs):
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.2f}", "-i", img]
        cmd += ["-i", bgm]
        parts = []
        for i, dur in enumerate(durs):
            parts.append(
                f"[{i}:v]format=yuv420p,fade=t=in:st=0:d={FADE},"
                f"fade=t=out:st={dur - FADE:.2f}:d={FADE}[v{i}]"
            )
        parts.append("".join(f"[v{i}]" for i in range(len(durs))) + f"concat=n={len(durs)}:v=1:a=0[v]")
        cmd += [
            "-filter_complex", ";".join(parts),
            "-map", "[v]", "-map", f"{len(durs)}:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", "-shortest", out_path,
        ]
        subprocess.run(cmd, check=True)
        return length
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    pid = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else f"reel_{pid}.mp4"
    with open(os.path.join(HERE, "posts.csv"), encoding="utf-8-sig", newline="") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}
    sec = build(rows[pid]["text"], out)
    print(f"作成しました: {out}（{sec:.1f}秒）")


if __name__ == "__main__":
    main()
