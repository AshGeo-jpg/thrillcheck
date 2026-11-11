"""
make_short.py  -  spoiler-free "Worth the watch?" Shorts from Thrillcheck's ratings

Makes a vertical 1080x1920 video (YouTube Shorts, Instagram Reels, TikTok) for one league's
latest week, matchweek or game night:

    hook  ->  the game to skip  ->  #3  ->  #2  ->  #1 Must watch  ->  thrillcheck.com

It never reads the final score. The only numbers shown are Thrill Scores, so every video is
as spoiler-free as the site.

Usage:
    python make_short.py --league nfl                      # newest NFL week in site/data/games.json
    python make_short.py --league nba --games https://thrillcheck.com/data/games.json
    python make_short.py --league epl --group 11           # a specific matchweek

Writes shorts/<league>-<group>.mp4, a cover image (.png) and a ready-to-paste caption (.txt).
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import date

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")
W, H, FPS = 1080, 1920, 30
SS = 2                                        # gauge is drawn at 2x then shrunk, for smooth edges

BG, PANEL, LINE = (9, 15, 27), (17, 26, 43), (31, 43, 66)
INK, MUTED, GOLD, TRACK = (237, 241, 247), (142, 155, 176), (245, 189, 58), (36, 48, 72)
LABEL_COLOR = {"Must watch": (255, 106, 61), "Worth watching": (53, 196, 134),
               "Fans only": (111, 155, 255), "Skip it": (137, 148, 168)}
LEAGUE_NAME = {"nfl": "NFL", "nba": "NBA", "epl": "PREMIER LEAGUE", "laliga": "LA LIGA"}
HASHTAGS = {"nfl": "#NFL #NFLFootball #FootballSunday", "nba": "#NBA #Basketball #NBATwitter",
            "epl": "#PremierLeague #EPL #Football", "laliga": "#LaLiga #Football #Soccer"}

INTRO_S, CARD_S, OUTRO_S = 2.8, 5.0, 3.6


# ------------------------------------------------------------------ helpers
_fonts = {}


def font(name, size):
    key = (name, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(os.path.join(FONTS, name), size)
    return _fonts[key]


DISPLAY, DISPLAY_X = "big-shoulders-display-800.ttf", "big-shoulders-display-900.ttf"
BODY, BODY_B, BODY_X = "figtree-500.ttf", "figtree-700.ttf", "figtree-800.ttf"


def hexrgb(h):
    h = (h or "#556070").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def visible(c):
    """Lift team colours that would vanish on the dark background."""
    lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    if lum >= 70:
        return c
    t = min(1.0, (70 - lum) / 70) * 0.55
    return tuple(int(v + (255 - v) * t) for v in c)


def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_back(t):
    t = max(0.0, min(1.0, t))
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def fit(d, text, name, size, max_w, floor=40):
    while size > floor and d.textlength(text, font=font(name, size)) > max_w:
        size -= 4
    return font(name, size)


def center_text(d, y, text, f, fill, cx=W // 2):
    d.text((cx - d.textlength(text, font=f) / 2, y), text, font=f, fill=fill)


def wordmark(d, x, y, size):
    f = font(DISPLAY_X, size)
    d.text((x, y), "THRILL", font=f, fill=INK)
    d.text((x + d.textlength("THRILL", font=f), y), "CHECK", font=f, fill=GOLD)


def wordmark_centered(d, y, size):
    f = font(DISPLAY_X, size)
    w = d.textlength("THRILLCHECK", font=f)
    wordmark(d, W / 2 - w / 2, y, size)


def background():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for i in range(0, H, 6):                    # faint top glow so the frame isn't flat black
        k = max(0.0, 1 - i / 900)
        if k:
            d.line([(0, i), (W, i)], fill=tuple(int(b + (p - b) * k * 0.55) for b, p in zip(BG, PANEL)), width=6)
    return img


def gauge_layer(score, color, r=300, w=58):
    """Thrill gauge (270 degree arc) drawn at 2x and shrunk for clean edges."""
    size = (2 * r + 40) * SS
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    c = size / 2
    R, Wd = r * SS, w * SS
    box = [c - R, c - R, c + R, c + R]
    d.arc(box, 135, 405, fill=TRACK + (255,), width=Wd)
    if score > 0.3:
        end = 135 + 270 * score / 100
        d.arc(box, 135, end, fill=color + (255,), width=Wd)
        for ang in (135, end):                  # round caps
            x = c + (R - Wd / 2) * math.cos(math.radians(ang))
            y = c + (R - Wd / 2) * math.sin(math.radians(ang))
            d.ellipse([x - Wd / 2, y - Wd / 2, x + Wd / 2, y + Wd / 2], fill=color + (255,))
    return layer.resize((size // SS, size // SS), Image.LANCZOS)


# ------------------------------------------------------------------ choosing the games
def load_games(src):
    if src.startswith("http"):
        req = urllib.request.Request(src, headers={"User-Agent": "Thrillcheck shorts"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    else:
        data = json.load(open(src))
    games = data["games"] if isinstance(data, dict) else data
    for g in games:                             # never carry a result into the video code
        g.pop("final", None)
    return games


def pick(games, league, group=None, today=None):
    today = (today or date.today()).isoformat()
    lg = [g for g in games if g["league"] == league and g["date"] <= today and g.get("score") is not None]
    if not lg:
        raise SystemExit(f"No rated {league} games found")
    groups = {}
    for g in lg:
        groups.setdefault((g["season"], g["groupOrder"]), []).append(g)
    if group:
        keys = [k for k in groups if str(k[1]) == str(group) or groups[k][0]["group"] == str(group)]
        if not keys:
            raise SystemExit(f"No {league} group {group}")
        key = max(keys)
    else:                                       # newest group with at least 4 rated games
        keys = sorted((k for k in groups if len(groups[k]) >= 4), reverse=True)
        key = keys[0] if keys else max(groups)
    pool = sorted(groups[key], key=lambda g: (-g["score"], g["date"]))
    top = pool[:3]
    rest = [g for g in pool[3:] if g["label"] in ("Skip it", "Fans only")]
    skip = rest[-1] if rest else None
    order = ([skip] if skip else []) + top[::-1]          # skip first, then #3, #2, #1
    return pool[0], order, len(pool), skip is not None


# ------------------------------------------------------------------ scenes
def max_size(text, name, start, max_w):
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    size = start
    while size > 20 and d.textlength(text, font=font(name, size)) > max_w:
        size -= 2
    return size


def intro_frames(league_name, group_label, n_games):
    base = background()
    cap1, cap2 = max_size("WORTH", DISPLAY_X, 230, W - 140), max_size("THE WATCH?", DISPLAY_X, 230, W - 140)
    for i in range(int(INTRO_S * FPS)):
        t = i / FPS
        img = base.copy()
        d = ImageDraw.Draw(img)
        a = ease_out(t / 0.5)
        wordmark_centered(d, 250 - 40 * (1 - a), 64)
        k = ease_back((t - 0.15) / 0.55)
        if t > 0.15:
            f1 = font(DISPLAY_X, max(10, int(cap1 * 0.8 * min(k, 1.04))))
            f2 = font(DISPLAY_X, max(10, int(cap2 * min(k, 1.04))))
            center_text(d, 560, "WORTH", f1, INK)
            center_text(d, 740, "THE WATCH?", f2, GOLD)
        if t > 0.6:
            b = ease_out((t - 0.6) / 0.4)
            f = fit(d, f"{league_name} · {group_label.upper()}", DISPLAY, 92, 900)
            center_text(d, 1010 + 30 * (1 - b), f"{league_name} · {group_label.upper()}", f, INK)
        if t > 0.9:
            f = font(BODY_B, 46)
            center_text(d, 1150, f"{n_games} games rated. No scores. No spoilers.", f, MUTED)
        if t > 1.3:
            f = font(BODY_X, 44)
            pill = "Here's what's worth your time"
            tw = d.textlength(pill, font=f)
            d.rounded_rectangle([W / 2 - tw / 2 - 40, 1290, W / 2 + tw / 2 + 40, 1386], radius=48, outline=GOLD, width=4)
            center_text(d, 1311, pill, f, GOLD)
        yield img


def card_frames(g, rank_text, league_name, group_label):
    color = LABEL_COLOR.get(g["label"], GOLD)
    ac, hc = visible(hexrgb(g.get("awayColor"))), visible(hexrgb(g.get("homeColor")))
    static = background()
    d = ImageDraw.Draw(static)
    wordmark(d, 64, 96, 52)
    lab = f"{league_name} · {group_label.upper()}"
    fl = font(DISPLAY, 50)
    d.text((W - 64 - d.textlength(lab, font=fl), 100), lab, font=fl, fill=MUTED)
    d.line([(64, 180), (W - 64, 180)], fill=LINE, width=2)

    # teams: colour bar + name, "at", colour bar + name
    def team(y, name, col):
        d.rounded_rectangle([64, y + 8, 84, y + 118], radius=8, fill=col)
        f = fit(d, name.upper(), DISPLAY_X, 120, W - 64 - 120)
        d.text((116, y), name.upper(), font=f, fill=INK)
    away = g.get("awayName") or g["away"]
    home = g.get("homeName") or g["home"]
    team(390, away, ac)
    d.text((116, 530), "at", font=font(BODY_B, 46), fill=MUTED)
    team(600, home, hc)
    when = g.get("kickoff", "").replace("·", "  ·  ")
    d.text((64, 1640), when, font=font(BODY_B, 40), fill=MUTED)
    d.text((64, 1700), "Full ratings at thrillcheck.com", font=font(BODY_B, 40), fill=GOLD)

    frames = int(CARD_S * FPS)
    slide = 0.35
    for i in range(frames):
        t = i / FPS
        img = static.copy()
        dd = ImageDraw.Draw(img)
        # rank tag pops in
        k = ease_back(t / 0.45)
        ft = font(DISPLAY_X, max(10, int(110 * k)))
        rt_color = GOLD if rank_text.startswith("#") else MUTED
        dd.text((64, 230 + (1 - min(1, k)) * 30), rank_text, font=ft, fill=rt_color)
        # gauge fills
        fill_t = ease_out((t - slide) / 1.9)
        val = g["score"] * fill_t
        gl = gauge_layer(val, color if t > slide else TRACK)
        cx, cy = W // 2, 1080
        img.paste(gl, (cx - gl.width // 2, cy - gl.height // 2), gl)
        num = str(int(round(val)))
        fn = font(DISPLAY_X, 250)
        dd.text((cx - dd.textlength(num, font=fn) / 2, cy - 165), num, font=fn, fill=INK)
        fs = font(BODY_X, 40)
        cap = "THRILL SCORE"
        dd.text((cx - dd.textlength(cap, font=fs) / 2, cy + 105), cap, font=fs, fill=MUTED)
        # verdict pill
        if t > 2.35:
            p = ease_back((t - 2.35) / 0.4)
            fv = font(DISPLAY_X, max(10, int(96 * p)))
            label = g["label"].upper()
            tw = dd.textlength(label, font=fv)
            ph = 140 * p
            y0 = 1450 - ph / 2
            dd.rounded_rectangle([cx - tw / 2 - 60 * p, y0, cx + tw / 2 + 60 * p, y0 + ph], radius=int(ph / 2), fill=color)
            dd.text((cx - tw / 2, y0 + ph * 0.12), label, font=fv, fill=(255, 255, 255) if g["label"] != "Skip it" else BG)
        yield img


def outro_frames():
    base = background()
    cap = max_size("THRILLCHECK.COM", DISPLAY_X, 170, W - 140)
    for i in range(int(OUTRO_S * FPS)):
        t = i / FPS
        img = base.copy()
        d = ImageDraw.Draw(img)
        a = ease_out(t / 0.5)
        center_text(d, 520 + 30 * (1 - a), "EVERY GAME RATED.", font(DISPLAY_X, 120), INK)
        center_text(d, 660 + 30 * (1 - a), "NO SPOILERS.", font(DISPLAY_X, 120), INK)
        if t > 0.5:
            k = ease_back((t - 0.5) / 0.5)
            f = font(DISPLAY_X, max(10, int(cap * min(k, 1.04))))
            center_text(d, 920, "THRILLCHECK.COM", f, GOLD)
        if t > 1.1:
            center_text(d, 1130, "NFL · NBA · Premier League · La Liga", font(BODY_B, 46), MUTED)
        if t > 1.6:
            center_text(d, 1290, "Thrillcheck the game.", font(BODY_X, 58), INK)
        yield img


# ------------------------------------------------------------------ encoding
def encode(frames, path):
    tmp = path[:-4] + ".part.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-r", str(FPS), tmp]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    last = None
    for img in frames:
        p.stdin.write(img.tobytes())
        last = img
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit(f"ffmpeg failed on {path}")
    os.replace(tmp, path)
    return last


def caption(league, group_label, order, top):
    name = {"nfl": "NFL", "nba": "NBA", "epl": "Premier League", "laliga": "La Liga"}[league]
    when = group_label if league != "nba" else f"games from {group_label}"
    lines = [
        f"TITLE: Which {name} {when} games are worth watching? (No spoilers)",
        "",
        "DESCRIPTION:",
        f"Missed the {name} this week? Here's what's worth the replay, rated by how close, tense and "
        "dramatic each game was. No scores, no spoilers.",
        "",
    ]
    for g in reversed(order):
        lines.append(f"{g['score']}  {g.get('awayShort') or g['away']} at {g.get('homeShort') or g['home']}  ({g['label']})")
    lines += ["", "Every game rated at https://thrillcheck.com", "",
              f"{HASHTAGS[league]} #Shorts #SportsReplay #NoSpoilers #Thrillcheck"]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="nfl", choices=list(LEAGUE_NAME))
    ap.add_argument("--games", default=os.path.join(HERE, "site", "data", "games.json"))
    ap.add_argument("--group", help="week / matchweek number, or NBA date like 2026-11-08")
    ap.add_argument("--out", default=os.path.join(HERE, "shorts"))
    a = ap.parse_args()

    games = load_games(a.games)
    top, order, n, has_skip = pick(games, a.league, a.group)
    league_name = LEAGUE_NAME[a.league]
    group_label = top["groupLabel"]
    os.makedirs(a.out, exist_ok=True)
    slug = f"{a.league}-{str(top['group']).replace(' ', '')}"
    work = tempfile.mkdtemp()
    parts = [os.path.join(work, "00-intro.mp4")]
    encode(intro_frames(league_name, group_label, n), parts[0])
    cover = None
    for i, g in enumerate(order):
        from_end = len(order) - 1 - i                     # 0 = the best game, shown last
        rank = "SKIP THIS ONE" if (has_skip and i == 0) else {0: "#1  WATCH THIS", 1: "#2", 2: "#3"}[from_end]
        path = os.path.join(work, f"{i + 1:02d}-card.mp4")
        last = encode(card_frames(g, rank, league_name, group_label), path)
        parts.append(path)
        if i == len(order) - 1:
            cover = last
    parts.append(os.path.join(work, "99-outro.mp4"))
    encode(outro_frames(), parts[-1])

    listing = os.path.join(work, "list.txt")
    open(listing, "w").write("".join(f"file '{p}'\n" for p in parts))
    joined = os.path.join(work, "joined.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing,
                    "-c", "copy", joined], check=True)
    out = os.path.join(a.out, f"{slug}.mp4")
    tmp = out[:-4] + ".part.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", joined, "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=stereo", "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", tmp], check=True)
    os.replace(tmp, out)
    cover.save(os.path.join(a.out, f"{slug}-cover.png"))
    open(os.path.join(a.out, f"{slug}.txt"), "w").write(caption(a.league, group_label, order, top))
    shutil.rmtree(work, ignore_errors=True)
    print(f"Made {out}  ({len(order)} games, {group_label})")


if __name__ == "__main__":
    main()
