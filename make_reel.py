"""Threads の投稿文から、インスタのリール用の縦長動画（1080x1920）を作る。

写真をいっぱいに出してゆっくりズーム、太い縁取りの文字、テンポよく切り替え。
写真は reel_assets/photos/<テーマ>/ から、文章の言葉に合うものを選ぶ。
最後はドライブプラスへの案内で締める（本人の写真・名前は使わない）。BGM は reel_assets/bgm/（OpenTracks の商用OK・クレジット不要の曲）。

使い方:
  python make_reel.py <posts.csv の id> [出力ファイル.mp4]
"""

import csv
import json
import os
import random
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "reel_assets")
W, H = 1080, 1920
FPS = 30
XFADE = 0.25  # 場面の切り替え（秒）
POP = 0.25    # 文字が出るときの動き（秒）

YELLOW = (255, 214, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# 文章の言葉 → 写真のテーマ
THEMES = [
    ("drink", r"飲酒|お酒|忘年会|飲ん|飲み会|呼気"),
    ("parking", r"駐車|バック|車庫|切り返"),
    ("mirror", r"ミラー|後方|死角|目視|車線変更|振り返"),
    ("rain", r"雨|雪|霧|ワイパー|凍結"),
    ("night", r"夜|ライト|暗い|夕方"),
    ("highway", r"高速|合流|インター|本線"),
    ("crosswalk", r"横断|歩行者|信号|交差点|右折|左折|一時停止"),
    ("meter", r"ブレーキ|アクセル|スピード|速度|km|キロ"),
    ("wheel", r"ハンドル|運転席|姿勢|シート|握"),
    ("keys", r"久しぶり|ペーパードライバー|何年も|はじめ|初めて|免許"),
    ("lesson", r"講習|指導員|教習|練習|プロ"),
    ("cone", r"コツ|ポイント|注意"),
    ("city", r"道|街|大阪|兵庫|狭い|住宅"),
]
DEFAULT_THEMES = ["city", "road", "wheel", "interior", "crosswalk"]


# ---------- 文字 ----------

FONT_PATH = os.path.join(ASSETS, "fonts", "NotoSansJP.ttf")
_font_cache = {}


def font(size, weight="Black"):
    key = (size, weight)
    if key not in _font_cache:
        f = ImageFont.truetype(FONT_PATH, size)
        f.set_variation_by_name(weight)
        _font_cache[key] = f
    return _font_cache[key]


EMOJI_MAP = {"✅": "", "⚠️": "", "⚠": "", "❌": "×", "⭕": "○", "👉": "", "▶": "", "→": "→"}
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")


def clean_line(line):
    for k, v in EMOJI_MAP.items():
        line = line.replace(k, v)
    return EMOJI_RE.sub("", line).strip()


def split_slides(text):
    """空行で区切って場面にする。リンクの行は消す。"""
    slides = []
    for b in re.split(r"\n\s*\n", text.strip()):
        lines = []
        for ln in b.splitlines():
            if "http" in ln or "山田" in ln or "代表指導員" in ln:
                continue
            c = clean_line(ln)
            if c in ("詳しくはこちら", "詳しくはnoteで", "→", ""):
                continue
            lines.append(c)
        if lines:
            slides.append(lines)
    merged = []
    for s in slides:
        if merged and len(merged[-1]) + len(s) <= 4 and len(merged) > 1:
            merged[-1] = merged[-1] + s
        else:
            merged.append(s)
    return merged


KINSOKU = "。、，．」』）！？ーっゃゅょァィゥェォッャュョ"


BREAK_AFTER = "はがをにでともの、。」）？！やへて・："


def wrap(draw, line, fnt, maxw):
    """言葉の切れ目（は・が・を・に など）で、なるべく同じ長さに分ける"""
    if draw.textlength(line, font=fnt) <= maxw:
        return [line]
    best = None
    for n in (2, 3, 4):
        # n 行に分ける：切れ目の候補から、各行が収まり長さがそろう分け方を探す
        cands = [
            i + 1 for i, ch in enumerate(line[:-1])
            if (ch in BREAK_AFTER or line[max(0, i - 1):i + 1] == "から") and line[i + 1] not in KINSOKU
        ]
        target = len(line) / n
        cuts, start = [], 0
        for k in range(1, n):
            ideal = target * k
            ok = [c for c in cands if c > start and draw.textlength(line[start:c], font=fnt) <= maxw]
            if not ok:
                break
            c = min(ok, key=lambda c: abs(c - ideal))
            cuts.append(c)
            start = c
        if len(cuts) == n - 1:
            parts = [line[a:b] for a, b in zip([0] + cuts, cuts + [len(line)])]
            if all(draw.textlength(p, font=fnt) <= maxw for p in parts):
                best = parts
                break
    if best:
        return best
    return wrap_chars(draw, line, fnt, maxw)


def wrap_chars(draw, line, fnt, maxw):
    out, cur = [], ""
    for ch in line:
        if ch in KINSOKU:
            cur += ch
        elif draw.textlength(cur + ch, font=fnt) > maxw and cur:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return out


def draw_rich(d, x, y, text, fnt, stroke, base=WHITE, accent=YELLOW):
    """「」の中を黄色にして1行描く。x は行の左端。"""
    color = base
    for ch in text:
        if ch in "「『":
            color = accent
        d.text((x, y), ch, font=fnt, fill=color, stroke_width=stroke, stroke_fill=BLACK, anchor="ls")
        x += d.textlength(ch, font=fnt)
        if ch in "」』":
            color = base


def text_block(lines, maxw, sizes, max_h, first_color=None):
    """文字だけの透明な画像を作る（太い黒フチ＋影）"""
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    for size in sizes:
        f = font(size)
        rows = []
        for i, ln in enumerate(lines):
            for w in wrap(probe, ln, f, maxw):
                rows.append((w, i))
        lh = int(size * 1.32)
        if len(rows) * lh <= max_h:
            break
    stroke = max(6, size // 9)
    img = Image.new("RGBA", (W, len(rows) * lh + stroke * 4 + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = stroke * 2 + int(size * 1.05)
    for txt, li in rows:
        tw = d.textlength(txt, font=f)
        x = (W - tw) / 2
        if first_color and li == 0:
            draw_rich(d, x, y, txt, f, stroke, base=first_color)
        else:
            draw_rich(d, x, y, txt, f, stroke)
        y += lh
    # 影
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.putalpha(img.getchannel("A").point(lambda a: int(a * 0.55)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (6, 10))
    out.alpha_composite(img)
    return out


def label(text, size=46, bg=YELLOW, fg=BLACK, pad=(28, 14)):
    f = font(size)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = int(probe.textlength(text, font=f))
    w, h = tw + pad[0] * 2, size + pad[1] * 2 + 8
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=bg)
    d.text((w / 2, h / 2 + 2), text, font=f, fill=fg, anchor="mm")
    return img


# ---------- 写真 ----------

def list_photos(theme):
    p = os.path.join(ASSETS, "photos", theme)
    if not os.path.isdir(p):
        return []
    return sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith((".jpg", ".jpeg", ".png")))


def pick_photos(slides, all_text, rnd):
    used, picks = set(), []
    post_themes = [t for t, rx in THEMES if re.search(rx, all_text)]
    for s in slides:
        txt = "".join(s)
        cands = [t for t, rx in THEMES if re.search(rx, txt)] + post_themes + DEFAULT_THEMES
        chosen = None
        for t in cands:
            pool = [p for p in list_photos(t) if p not in used]
            # 自分の写真（own_）を優先
            own = [p for p in pool if os.path.basename(p).startswith("own_")]
            if own and rnd.random() < 0.7:
                pool = own
            if pool:
                chosen = rnd.choice(pool)
                break
        if not chosen:
            chosen = rnd.choice([p for t in DEFAULT_THEMES for p in list_photos(t)])
        used.add(chosen)
        picks.append(chosen)
    return picks


def cover(img, w, h, focus_x=0.5):
    """写真を w×h いっぱいに切り抜く"""
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    nw, nh = int(img.width * s + 0.5), int(img.height * s + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    x = int(min(max(focus_x * nw - w / 2, 0), nw - w))
    y = (nh - h) // 2
    return img.crop((x, y, x + w, y + h))


def shade():
    """文字を読みやすくする上下の影"""
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(g)
    for y in range(H):
        a = 0
        if y < 420:
            a = int(140 * (1 - y / 420))
        if y > H * 0.42:
            a = max(a, int(175 * min(1, (y - H * 0.42) / (H * 0.35))))
        d.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    return g


# ---------- 場面 ----------

class Scene:
    def __init__(self, photo, overlays, dur, zoom_in=True, focus_x=0.5):
        base = Image.open(photo) if isinstance(photo, str) else photo
        self.bg = cover(base, int(W * 1.12), int(H * 1.12), focus_x)
        self.overlays = overlays  # [(画像, x, y, 出始め秒)]
        self.dur = dur
        self.zoom_in = zoom_in

    def frame(self, t):
        p = t / self.dur
        z = 1.0 + 0.10 * (p if self.zoom_in else 1 - p)
        bw, bh = self.bg.size
        cw, ch = bw / z, bh / z
        x0, y0 = (bw - cw) / 2, (bh - ch) / 2
        box = (max(0, x0), max(0, y0), min(bw, x0 + cw), min(bh, y0 + ch))
        img = self.bg.resize((W, H), Image.BILINEAR, box=box).convert("RGBA")
        img.alpha_composite(SHADE)
        for ov, x, y, t0 in self.overlays:
            dt = t - t0
            if dt < 0:
                continue
            if dt < POP:
                k = dt / POP
                s = 0.8 + 0.2 * (1 - (1 - k) ** 3) + 0.06 * (k * (1 - k) * 4)
                ow, oh = max(1, int(ov.width * s)), max(1, int(ov.height * s))
                o = ov.resize((ow, oh), Image.BILINEAR)
                a = o.getchannel("A").point(lambda v: int(v * min(1, k * 1.6)))
                o.putalpha(a)
                img.alpha_composite(o, (int(x + (ov.width - ow) / 2), int(y + (ov.height - oh) / 2)))
            else:
                img.alpha_composite(ov, (int(x), int(y)))
        return img.convert("RGB")


SHADE = None


def scene_seconds(lines):
    chars = sum(len(l) for l in lines)
    return max(2.6, min(5.5, 1.0 + chars * 0.085))


def build_scenes(text, rnd):
    slides = split_slides(text)
    hook = slides[0][0] if slides else ""
    # 表紙が1行だけなら、次の場面とまとめて表紙にする
    if len(slides) > 2 and len(slides[0]) == 1 and len(slides[1]) <= 3:
        slides = [slides[0] + slides[1]] + slides[2:]
    photos = pick_photos(slides, text, rnd)
    scenes = []
    brand = label("ドライブプラス｜運転のコツ", size=36, bg=(255, 255, 255), fg=BLACK, pad=(24, 10))
    for i, (s, ph) in enumerate(zip(slides, photos)):
        ovs = [(brand, 50, 150, 0)]
        if i == 0:
            # 表紙：大きくタイトル
            tb = text_block(s, 960, (108, 100, 92, 86, 80, 74), 1000, first_color=YELLOW)
            ovs.append((tb, 0, (H - tb.height) / 2 - 60, 0.1))
            dur = max(3.0, scene_seconds(s))
        else:
            top = label(hook[:16], size=44)
            ovs.append((top, (W - top.width) / 2, 300, 0))
            tb = text_block(s, 940, (88, 82, 76, 70, 64, 58), 800)
            ovs.append((tb, 0, min(H - 500 - tb.height, 900), 0.1))
            dur = scene_seconds(s)
        scenes.append(Scene(ph, ovs, dur, zoom_in=(i % 2 == 0)))

    # 締め：ドライブプラスへの案内（人物の写真・名前は使わない）
    end_pool = [p for p in list_photos("lesson") + list_photos("road") if p not in photos]
    end_photo = rnd.choice(end_pool)
    ovs = [(brand, 50, 150, 0)]
    t1 = text_block(["運転の不安、", "プロといっしょに", "解消しませんか？"], 900, (84, 76), 500)
    ovs.append((t1, 0, 330, 0.1))
    t2 = text_block(["「出張ペーパードライバー講習」"], 960, (64, 58, 52), 200)
    ovs.append((t2, 0, 1180, 0.5))
    t3 = text_block(["ドライブプラス"], 900, (110,), 200)
    ovs.append((t3, 0, 1290, 0.7))
    btn = label("プロフィールのリンクから ▶", size=46, bg=YELLOW)
    ovs.append((btn, (W - btn.width) / 2, 1480, 1.0))
    scenes.append(Scene(end_photo, ovs, 4.2, zoom_in=True))
    return scenes


# ---------- 音と書き出し ----------

def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def pick_bgm(rnd):
    with open(os.path.join(ASSETS, "bgm", "tracks.json"), encoding="utf-8") as f:
        tracks = json.load(f)
    tid = rnd.choice(sorted(tracks))
    return os.path.join(ASSETS, "bgm", f"{tid}.mp3"), tracks[tid]["start"]


def build(text, out_path, seed=None):
    global SHADE
    if SHADE is None:
        SHADE = shade()
    rnd = random.Random(seed if seed is not None else text)
    scenes = build_scenes(text, rnd)
    # 切り替えで重なる分を引いた長さ
    total = sum(s.dur for s in scenes) - XFADE * (len(scenes) - 1)
    bgm, start = pick_bgm(rnd)

    cmd = [
        ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-ss", str(start), "-i", bgm,
        "-filter_complex",
        f"[1:a]atrim=0:{total:.2f},afade=t=in:st=0:d=0.4,afade=t=out:st={total - 1.5:.2f}:d=1.5,volume=0.85[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-movflags", "+faststart", "-t", f"{total:.2f}", out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    # 各場面の始まり（前の場面と XFADE 秒重ねる）
    starts, t = [], 0.0
    for s in scenes:
        starts.append(t)
        t += s.dur - XFADE
    n = int(total * FPS)
    for fi in range(n):
        t = fi / FPS
        idx = max(i for i, st in enumerate(starts) if st <= t + 1e-9)
        cur = scenes[idx].frame(t - starts[idx])
        # 前の場面との重なり
        if idx > 0 and t - starts[idx] < XFADE:
            prev = scenes[idx - 1].frame(t - starts[idx - 1])
            k = (t - starts[idx]) / XFADE
            cur = Image.blend(prev, cur, k)
        proc.stdin.write(cur.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg でエラーが出ました")
    return total


def main():
    pid = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else f"reel_{pid}.mp4"
    with open(os.path.join(HERE, "posts.csv"), encoding="utf-8-sig", newline="") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}
    sec = build(rows[pid]["text"], out, seed=pid)
    print(f"作成しました: {out}（{sec:.1f}秒）")


if __name__ == "__main__":
    main()
