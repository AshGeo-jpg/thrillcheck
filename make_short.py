"""
make_short.py  -  spoiler-free Shorts from Thrillcheck's ratings (YouTube Shorts, Reels, TikTok)

Formats (words, schedule and switches live in shorts_config.json):
    worth   "Worth the watch?"      the game to skip, then #3, #2, #1
    guess   "Guess the Thrill Score" matchup, pause for comments, then the reveal
    teams   "Most entertaining team" teams ranked by average Thrill Score this season
    snooze  "Snoozefest of the week" the least exciting game, roasted by the mascot

Every video: 1080x1920, 30 fps, synthesized sound effects (no music, so no copyright trouble),
the Thrill mascot, and an ending that says the site is free and people can pay what they want.
It never reads final scores: results are dropped before anything is drawn.

Usage:
    python make_short.py --format worth --league nfl
    python make_short.py --format teams --league nba --games https://thrillcheck.com/data/games.json
    python make_short.py --daily          # today's formats from the config, for every league with fresh games
Each video comes with a cover image (.png) and a ready-to-paste caption (.txt).
"""
import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import urllib.request
import wave
from datetime import date, datetime, timedelta, timezone

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")
CONFIG = os.path.join(HERE, "shorts_config.json")
W, H, FPS, SR = 1080, 1920, 30, 44100
SS = 2

BG, PANEL, LINE = (9, 15, 27), (17, 26, 43), (31, 43, 66)
INK, MUTED, GOLD, TRACK = (237, 241, 247), (142, 155, 176), (245, 189, 58), (36, 48, 72)
LABEL_COLOR = {"Must watch": (255, 106, 61), "Worth watching": (53, 196, 134),
               "Fans only": (111, 155, 255), "Skip it": (137, 148, 168)}
MOOD = {"Must watch": "hype", "Worth watching": "happy", "Fans only": "meh", "Skip it": "sleep"}
LEAGUE_NAME = {"nfl": "NFL", "nba": "NBA", "epl": "PREMIER LEAGUE", "laliga": "LA LIGA"}
LEAGUE_PLAIN = {"nfl": "NFL", "nba": "NBA", "epl": "Premier League", "laliga": "La Liga"}
HASHTAGS = {"nfl": "#NFL #NFLFootball #FootballSunday", "nba": "#NBA #Basketball #NBAHighlights",
            "epl": "#PremierLeague #EPL #Football", "laliga": "#LaLiga #Football #Soccer"}
SPOILER_WORDS = ("win", "won", "lost", "lose", "beat", "comeback", "overtime", "shootout", "blowout",
                 "score", "points", "goal", "upset", "rout", "collapse")

DISPLAY, DISPLAY_X = "big-shoulders-display-800.ttf", "big-shoulders-display-900.ttf"
BODY, BODY_B, BODY_X = "figtree-500.ttf", "figtree-700.ttf", "figtree-800.ttf"


# ============================================================ basics
_fonts = {}


def font(name, size):
    size = max(8, int(size))
    if (name, size) not in _fonts:
        _fonts[(name, size)] = ImageFont.truetype(os.path.join(FONTS, name), size)
    return _fonts[(name, size)]


def load_config():
    cfg = json.load(open(CONFIG))
    for label, lines in cfg.get("mascot_lines", {}).items():   # keep the spoiler promise
        for line in lines:
            low = f" {line.lower()} "
            bad = [w for w in SPOILER_WORDS if f" {w} " in low or f" {w}." in low or f" {w}," in low]
            if bad:
                raise SystemExit(f"Mascot line may hint at a result ({bad}): {line}")
    return cfg


def hexrgb(h):
    h = (h or "#556070").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def visible(c):
    lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    if lum >= 70:
        return c
    t = min(1.0, (70 - lum) / 70) * 0.55
    return tuple(int(v + (255 - v) * t) for v in c)


def clamp(t):
    return max(0.0, min(1.0, t))


def ease_out(t):
    return 1 - (1 - clamp(t)) ** 3


def ease_back(t):
    t = clamp(t)
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def max_size(text, name, start, max_w):
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    size = start
    while size > 20 and d.textlength(text, font=font(name, size)) > max_w:
        size -= 2
    return size


def center_text(d, y, text, f, fill, cx=W // 2):
    d.text((cx - d.textlength(text, font=f) / 2, y), text, font=f, fill=fill)


def wrap(d, text, f, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=f) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def wordmark(d, x, y, size):
    f = font(DISPLAY_X, size)
    d.text((x, y), "THRILL", font=f, fill=INK)
    d.text((x + d.textlength("THRILL", font=f), y), "CHECK", font=f, fill=GOLD)


def wordmark_centered(d, y, size):
    w = ImageDraw.Draw(Image.new("RGB", (8, 8))).textlength("THRILLCHECK", font=font(DISPLAY_X, size))
    wordmark(d, W / 2 - w / 2, y, size)


_bg = None


def background():
    global _bg
    if _bg is None:
        _bg = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(_bg)
        for i in range(0, 900, 6):
            k = 1 - i / 900
            d.line([(0, i), (W, i)], fill=tuple(int(b + (p - b) * k * 0.55) for b, p in zip(BG, PANEL)), width=6)
    return _bg.copy()


def pick_line(cfg, label, key):
    lines = cfg["mascot_lines"].get(label) or ["Press play."]
    return lines[int(hashlib.md5(key.encode()).hexdigest(), 16) % len(lines)]


# ============================================================ drawings
def gauge_layer(score, color, r=260, w=52):
    size = (2 * r + 40) * SS
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    c, R, Wd = size / 2, r * SS, w * SS
    box = [c - R, c - R, c + R, c + R]
    d.arc(box, 135, 405, fill=TRACK + (255,), width=Wd)
    if score > 0.3:
        end = 135 + 270 * min(score, 100) / 100
        d.arc(box, 135, end, fill=color + (255,), width=Wd)
        for ang in (135, end):
            x = c + (R - Wd / 2) * math.cos(math.radians(ang))
            y = c + (R - Wd / 2) * math.sin(math.radians(ang))
            d.ellipse([x - Wd / 2, y - Wd / 2, x + Wd / 2, y + Wd / 2], fill=color + (255,))
    return layer.resize((size // SS, size // SS), Image.LANCZOS)


def mascot(mood, t, size=260, score=None, ring=GOLD):
    """Thrill, the gauge with a face. mood: hype, happy, meh, sleep, think."""
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = S / 2
    bob = {"hype": 10 * math.sin(t * 18), "sleep": 6 * math.sin(t * 2.2)}.get(mood, 8 * math.sin(t * 4)) * SS
    shake = 6 * SS * math.sin(t * 45) if mood == "hype" else 0
    cx += shake
    cy = S * 0.54 + bob
    r = S * 0.25
    body, edge = GOLD + (255,), (184, 132, 22, 255)
    # gauge ring around the head (the brand mark)
    rr, rw = r + 18 * SS, 14 * SS
    d.arc([cx - rr, cy - rr, cx + rr, cy + rr], 135, 405, fill=TRACK + (255,), width=rw)
    fill = (score if score is not None else {"hype": 92, "happy": 72, "meh": 50, "sleep": 15, "think": 50}[mood])
    d.arc([cx - rr, cy - rr, cx + rr, cy + rr], 135, 135 + 270 * fill / 100, fill=ring + (255,), width=rw)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=body, outline=edge, width=5 * SS)
    ink = (20, 26, 40, 255)
    ex, ey, er = r * 0.38, cy - r * 0.12, r * 0.17
    blink = (t % 2.6) < 0.12 and mood in ("happy", "think", "meh")
    if mood == "sleep" or blink:
        for sx in (-1, 1):
            d.arc([cx + sx * ex - er, ey - er * 0.6, cx + sx * ex + er, ey + er * 0.9], 20, 160, fill=ink, width=5 * SS)
    elif mood == "meh":
        for sx in (-1, 1):
            d.ellipse([cx + sx * ex - er, ey - er, cx + sx * ex + er, ey + er], fill=(255, 255, 255, 255))
            d.ellipse([cx + sx * ex - er * 0.45, ey - er * 0.1, cx + sx * ex + er * 0.45, ey + er * 0.8], fill=ink)
            d.rectangle([cx + sx * ex - er - 2, ey - er - 2, cx + sx * ex + er + 2, ey - er * 0.05], fill=body)
            d.line([cx + sx * ex - er, ey - er * 0.05, cx + sx * ex + er, ey - er * 0.05], fill=edge, width=4 * SS)
    else:
        big = 1.25 if mood == "hype" else 1.0
        look = -er * 0.35 if mood == "think" else 0
        for sx in (-1, 1):
            d.ellipse([cx + sx * ex - er * big, ey - er * big, cx + sx * ex + er * big, ey + er * big], fill=(255, 255, 255, 255))
            pr = er * (0.42 if mood == "hype" else 0.55)
            d.ellipse([cx + sx * ex - pr, ey - pr + look, cx + sx * ex + pr, ey + pr + look], fill=ink)
    my = cy + r * 0.38
    if mood == "hype":
        d.chord([cx - r * 0.34, my - r * 0.22, cx + r * 0.34, my + r * 0.30], 0, 180, fill=(170, 40, 30, 255), outline=ink, width=4 * SS)
        # sweat drop and burst lines
        d.ellipse([cx + r * 0.62, cy - r * 0.55, cx + r * 0.78, cy - r * 0.3], fill=(120, 190, 255, 255))
        for k in range(8):
            a = math.radians(k * 45 + t * 90)
            r1, r2 = rr + 22 * SS, rr + (42 + 10 * math.sin(t * 20 + k)) * SS
            d.line([cx + r1 * math.cos(a), cy + r1 * math.sin(a), cx + r2 * math.cos(a), cy + r2 * math.sin(a)],
                   fill=(255, 106, 61, 255), width=6 * SS)
    elif mood == "happy":
        d.arc([cx - r * 0.32, my - r * 0.3, cx + r * 0.32, my + r * 0.18], 20, 160, fill=ink, width=6 * SS)
    elif mood == "meh":
        d.line([cx - r * 0.22, my, cx + r * 0.22, my - r * 0.04], fill=ink, width=6 * SS)
    elif mood == "think":
        d.line([cx - r * 0.12, my, cx + r * 0.22, my - r * 0.06], fill=ink, width=6 * SS)
    else:  # sleep
        d.ellipse([cx - r * 0.09, my - r * 0.06, cx + r * 0.09, my + r * 0.14], fill=ink)
    img = img.resize((size, size), Image.LANCZOS)
    dd = ImageDraw.Draw(img)
    if mood == "sleep":                                  # floating Zzz
        for k in range(3):
            ph = (t * 0.6 + k / 3) % 1
            dd.text((size * 0.68 + ph * 30 + k * 6, size * 0.28 - ph * 80), "z" if k < 2 else "Z",
                    font=font(DISPLAY_X, 30 + k * 10), fill=INK + (int(255 * (1 - ph)),))
    if mood == "think":
        dd.text((size * 0.72, size * 0.02 + 6 * math.sin(t * 5)), "?", font=font(DISPLAY_X, 80), fill=GOLD + (255,))
    return img


def bubble(img, x, y, w, text, accent=GOLD):
    d = ImageDraw.Draw(img)
    f = font(BODY_X, 44)
    lines = wrap(d, text, f, w - 60)[:3]
    h = 40 + 56 * len(lines)
    d.rounded_rectangle([x, y, x + w, y + h], radius=34, fill=PANEL, outline=accent, width=4)
    d.polygon([(x + 4, y + h / 2 - 18), (x - 26, y + h / 2), (x + 4, y + h / 2 + 18)], fill=accent)
    for i, ln in enumerate(lines):
        d.text((x + 30, y + 22 + i * 56), ln, font=f, fill=INK)
    return h


# ============================================================ sound effects (synthesized)
def _env(n, attack=0.005, decay=6.0):
    t = np.arange(n) / SR
    a = np.clip(t / attack, 0, 1)
    return a * np.exp(-decay * t)


def sfx(kind, value=50.0):
    if kind == "whoosh":
        n = int(SR * 0.42)
        noise = np.random.default_rng(7).standard_normal(n)
        out, y = np.zeros(n), 0.0
        alphas = np.linspace(0.02, 0.35, n) ** 1.2
        for i in range(n):
            y += alphas[i] * (noise[i] - y)
            out[i] = y
        env = np.sin(np.linspace(0, math.pi, n)) ** 2
        return 0.55 * out * env / (np.abs(out).max() + 1e-9)
    if kind == "tick":
        n = int(SR * 0.03)
        f = 520 + value * 9
        return 0.22 * np.sin(2 * math.pi * f * np.arange(n) / SR) * _env(n, 0.001, 140)
    if kind == "hit":
        n = int(SR * 0.5)
        tt = np.arange(n) / SR
        boom = np.sin(2 * math.pi * (95 - 50 * tt) * tt) * np.exp(-9 * tt)
        click = np.random.default_rng(3).standard_normal(n) * np.exp(-90 * tt) * 0.3
        return 0.7 * (boom + click)
    if kind == "chime":
        n = int(SR * 0.9)
        tt = np.arange(n) / SR
        s = sum(np.sin(2 * math.pi * f * tt) * a for f, a in ((880, 1), (1320, 0.6), (1760, 0.3)))
        return 0.28 * s * _env(n, 0.003, 4.5)
    if kind == "womp":
        n = int(SR * 0.9)
        tt = np.arange(n) / SR
        f = 260 - 120 * tt
        ph = 2 * math.pi * np.cumsum(f) / SR
        return 0.3 * (np.sign(np.sin(ph)) * 0.3 + np.sin(ph)) * _env(n, 0.01, 3.2)
    if kind == "pop":
        n = int(SR * 0.12)
        tt = np.arange(n) / SR
        return 0.35 * np.sin(2 * math.pi * (600 + 900 * tt) * tt) * _env(n, 0.001, 35)
    if kind == "coin":
        n = int(SR * 0.5)
        tt = np.arange(n) / SR
        a = np.sin(2 * math.pi * 988 * tt) * (tt < 0.08)
        b = np.sin(2 * math.pi * 1319 * tt) * (tt >= 0.08)
        return 0.25 * (a + b) * _env(n, 0.002, 6)
    if kind == "riser":
        n = int(SR * 0.6)
        tt = np.arange(n) / SR
        ph = 2 * math.pi * np.cumsum(200 + 900 * (tt / tt[-1]) ** 2) / SR
        return 0.18 * np.sin(ph) * np.linspace(0, 1, n) ** 2
    if kind == "tock":
        n = int(SR * 0.08)
        return 0.3 * np.sin(2 * math.pi * 330 * np.arange(n) / SR) * _env(n, 0.001, 60)
    raise ValueError(kind)


def verdict_sound(label):
    return {"Must watch": "chime", "Worth watching": "pop", "Fans only": "pop", "Skip it": "womp"}[label]


def fill_ticks(start, dur, score):
    """Ticks while a gauge fills: one per 5 points, pitch rising with the value."""
    ev = []
    for v in range(5, int(score) + 1, 5):
        frac = v / max(score, 1)
        # invert ease_out: value fraction f at time u where 1-(1-u)^3 = f
        u = 1 - (1 - frac) ** (1 / 3)
        ev.append((start + u * dur, "tick", v))
    return ev


# ============================================================ data
def load_games(src):
    if src.startswith("http"):
        req = urllib.request.Request(src, headers={"User-Agent": "Thrillcheck shorts"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    else:
        data = json.load(open(src))
    games = data["games"] if isinstance(data, dict) else data
    for g in games:
        g.pop("final", None)                 # results never reach the video code
    return games


def latest_group(games, league, group=None, today=None, min_games=4):
    today = (today or date.today()).isoformat()
    lg = [g for g in games if g["league"] == league and g["date"] <= today and g.get("score") is not None]
    if not lg:
        return None, []
    groups = {}
    for g in lg:
        groups.setdefault((g["season"], g["groupOrder"]), []).append(g)
    if group:
        keys = [k for k in groups if str(k[1]) == str(group) or groups[k][0]["group"] == str(group)]
        if not keys:
            return None, []
        key = max(keys)
    else:
        keys = sorted((k for k in groups if len(groups[k]) >= min_games), reverse=True)
        key = keys[0] if keys else max(groups)
    pool = sorted(groups[key], key=lambda g: (-g["score"], g["date"]))
    return pool[0], pool


def team_table(games, league, season, min_games):
    acc = {}
    for g in games:
        if g["league"] != league or g["season"] != season or g.get("score") is None:
            continue
        for side in ("away", "home"):
            code = g[side]
            a = acc.setdefault(code, {"code": code, "name": g.get(f"{side}Name") or code,
                                      "short": g.get(f"{side}Short") or code, "color": g.get(f"{side}Color"), "s": []})
            a["s"].append(g["score"])
    rows = [dict(a, avg=sum(a["s"]) / len(a["s"]), n=len(a["s"])) for a in acc.values() if len(a["s"]) >= min_games]
    return sorted(rows, key=lambda r: -r["avg"])


# ============================================================ scenes
# A scene = (duration seconds, frame generator, list of (time, sound, value) events relative to scene start)
def scene_intro(cfg, fmt, league, group_label, n_games, mood="think"):
    f = cfg["formats"][fmt]
    l1, l2 = f["hook"]
    sub = f["sub"].format(n=n_games)
    dur = 3.0
    cap1 = max_size(l1, DISPLAY_X, 200, W - 140)
    cap2 = max_size(l2, DISPLAY_X, 220, W - 140)
    head = f"{LEAGUE_NAME[league]} · {group_label.upper()}" if group_label else LEAGUE_NAME[league]

    def frames():
        for i in range(int(dur * FPS)):
            t = i / FPS
            img = background()
            d = ImageDraw.Draw(img)
            wordmark_centered(d, 230 - 40 * (1 - ease_out(t / 0.5)), 64)
            if t > 0.15:
                k = min(ease_back((t - 0.15) / 0.55), 1.04)
                center_text(d, 470, l1, font(DISPLAY_X, cap1 * 0.85 * k), INK)
                center_text(d, 470 + cap1 * 0.85 + 10, l2, font(DISPLAY_X, cap2 * k), GOLD)
            y2 = 470 + cap1 * 0.85 + cap2 + 60
            if t > 0.6:
                b = ease_out((t - 0.6) / 0.4)
                fh = font(DISPLAY, max_size(head, DISPLAY, 92, 900))
                center_text(d, y2 + 30 * (1 - b), head, fh, INK)
            if t > 0.9:
                center_text(d, y2 + 120, sub, font(BODY_B, max_size(sub, BODY_B, 46, 940)), MUTED)
            if t > 1.3:
                fp = font(BODY_X, max_size(f["pill"], BODY_X, 44, 860))
                tw = d.textlength(f["pill"], font=fp)
                py = y2 + 220
                d.rounded_rectangle([W / 2 - tw / 2 - 40, py, W / 2 + tw / 2 + 40, py + 96], radius=48, outline=GOLD, width=4)
                center_text(d, py + 21, f["pill"], fp, GOLD)
            if cfg.get("mascot") and t > 1.0:
                m = mascot(mood, t, 300)
                k = ease_back((t - 1.0) / 0.5)
                img.paste(m, (int(W / 2 - 150), int(1540 + 200 * (1 - min(k, 1)))), m)
            yield img
    events = [(0.05, "riser", 0), (0.62, "hit", 0), (1.3, "pop", 0)]
    return dur, frames, events


def card_layout(g, rank_text, league, group_label, rank_color):
    ac, hc = visible(hexrgb(g.get("awayColor"))), visible(hexrgb(g.get("homeColor")))
    static = background()
    d = ImageDraw.Draw(static)
    wordmark(d, 64, 96, 52)
    lab = f"{LEAGUE_NAME[league]} · {group_label.upper()}"
    fl = font(DISPLAY, 50)
    d.text((W - 64 - d.textlength(lab, font=fl), 100), lab, font=fl, fill=MUTED)
    d.line([(64, 180), (W - 64, 180)], fill=LINE, width=2)

    def team(y, name, col):
        d.rounded_rectangle([64, y + 8, 84, y + 108], radius=8, fill=col)
        f = font(DISPLAY_X, max_size(name.upper(), DISPLAY_X, 110, W - 64 - 120))
        d.text((116, y), name.upper(), font=f, fill=INK)
    team(360, g.get("awayName") or g["away"], ac)
    d.text((116, 486), "at", font=font(BODY_B, 44), fill=MUTED)
    team(548, g.get("homeName") or g["home"], hc)
    d.text((64, 1660), g.get("kickoff", "").replace("·", "  ·  "), font=font(BODY_B, 38), fill=MUTED)
    return static


def scene_card(cfg, g, rank_text, league, group_label, guess_pause=0.0, rank_color=GOLD, dur=None):
    color = LABEL_COLOR.get(g["label"], GOLD)
    static = card_layout(g, rank_text, league, group_label, rank_color)
    slide = 0.35
    fill_start = slide + guess_pause
    fill_dur = 1.9
    verdict_at = fill_start + fill_dur + 0.05
    dur = dur or (verdict_at + 2.6)
    line = pick_line(cfg, g["label"], g["id"])
    mood_after = MOOD.get(g["label"], "happy")
    cx, cy = W // 2, 960

    def frames():
        for i in range(int(dur * FPS)):
            t = i / FPS
            img = static.copy()
            d = ImageDraw.Draw(img)
            k = min(ease_back(t / 0.45), 1.04)
            d.text((64, 216 + (1 - min(1, k)) * 30), rank_text, font=font(DISPLAY_X, 104 * k), fill=rank_color)
            if t < fill_start:
                gl = gauge_layer(0, TRACK)
                img.paste(gl, (cx - gl.width // 2, cy - gl.height // 2), gl)
                if guess_pause and t > slide:                 # countdown while people guess
                    left = fill_start - t
                    frac = left / guess_pause
                    rr = 234                                  # countdown runs along the gauge track
                    d.arc([cx - rr, cy - rr, cx + rr, cy + rr], 135, 135 + 270 * frac, fill=GOLD, width=14)
                    q = font(DISPLAY_X, 230)
                    center_text(d, cy - 150, "?", q, GOLD, cx)
                    center_text(d, cy + 100, f"{math.ceil(left)}", font(DISPLAY_X, 70), INK, cx)
                    center_text(d, 1250, "Comment your guess", font(BODY_X, 52), GOLD)
                val = 0
            else:
                val = g["score"] * ease_out((t - fill_start) / fill_dur)
                gl = gauge_layer(val, color)
                img.paste(gl, (cx - gl.width // 2, cy - gl.height // 2), gl)
                num = str(int(round(val)))
                fn = font(DISPLAY_X, 230)
                center_text(d, cy - 150, num, fn, INK, cx)
                center_text(d, cy + 95, "THRILL SCORE", font(BODY_X, 38), MUTED, cx)
            if t > verdict_at:
                p = ease_back((t - verdict_at) / 0.4)
                fv = font(DISPLAY_X, 88 * min(p, 1.05))
                label = g["label"].upper()
                tw = d.textlength(label, font=fv)
                ph = 128 * min(p, 1.05)
                y0 = 1230 - ph / 2
                d.rounded_rectangle([cx - tw / 2 - 56 * p, y0, cx + tw / 2 + 56 * p, y0 + ph], radius=int(ph / 2), fill=color)
                d.text((cx - tw / 2, y0 + ph * 0.12), label, font=fv, fill=BG if g["label"] == "Skip it" else (255, 255, 255))
            if cfg.get("mascot"):
                mood = "think" if t < verdict_at else mood_after
                m = mascot(mood, t, 300, score=val if t >= fill_start else 0, ring=color if t >= fill_start else GOLD)
                img.paste(m, (20, 1310), m)
                if t > verdict_at + 0.35:
                    bubble(img, 330, 1395, 640, line, accent=color)
                elif guess_pause and slide < t < fill_start:
                    bubble(img, 330, 1395, 640, "Hmm... what's your guess?", accent=GOLD)
            yield img
    events = [(0.0, "whoosh", 0)]
    if guess_pause:
        events += [(slide + k, "tock", 0) for k in range(int(guess_pause))]
    events += fill_ticks(fill_start, fill_dur, g["score"])
    events += [(verdict_at, "hit", 0), (verdict_at + 0.02, verdict_sound(g["label"]), 0)]
    return dur, frames, events


def scene_teams(cfg, rows, worst, league, season_label):
    f = cfg["formats"]["teams"]
    top = rows[: f.get("top", 5)][::-1]                 # reveal #5 first
    n = len(top)
    step = 1.15
    dur = 0.6 + n * step + 3.2
    maxw = 620

    def frames():
        for i in range(int(dur * FPS)):
            t = i / FPS
            img = background()
            d = ImageDraw.Draw(img)
            wordmark(d, 64, 96, 52)
            lab = f"{LEAGUE_NAME[league]} · {season_label}"
            fl = font(DISPLAY, 50)
            d.text((W - 64 - d.textlength(lab, font=fl), 100), lab, font=fl, fill=MUTED)
            d.line([(64, 180), (W - 64, 180)], fill=LINE, width=2)
            d.text((64, 214), "MOST ENTERTAINING", font=font(DISPLAY_X, 96), fill=GOLD)
            d.text((64, 318), "Average Thrill Score per game", font=font(BODY_B, 40), fill=MUTED)
            for j, r in enumerate(top):
                appear = 0.6 + j * step
                if t < appear:
                    continue
                rank = n - j
                y = 420 + (rank - 1) * 176
                a = ease_out((t - appear) / 0.45)
                x0 = 64 - 60 * (1 - a)
                col = visible(hexrgb(r["color"]))
                d.text((x0, y), f"#{rank}", font=font(DISPLAY_X, 84), fill=GOLD if rank == 1 else INK)
                nm = r["name"].upper()
                d.text((x0 + 130, y), nm, font=font(DISPLAY_X, max_size(nm, DISPLAY_X, 66, 760)), fill=INK)
                bw = maxw * r["avg"] / 100 * ease_out((t - appear - 0.1) / 0.7)
                d.rounded_rectangle([x0 + 130, y + 86, x0 + 130 + max(bw, 12), y + 122], radius=18, fill=col)
                d.text((x0 + 150 + max(bw, 12), y + 78), f"{r['avg']:.0f}", font=font(DISPLAY_X, 52), fill=INK)
                if rank == 1 and t > appear + 0.6:
                    d.rounded_rectangle([40, y - 14, W - 40, y + 146], radius=20, outline=GOLD, width=4)
            end = 0.6 + n * step
            if t > end and worst:
                a = ease_out((t - end) / 0.4)
                y = 1330 + 40 * (1 - a)
                if cfg.get("mascot"):
                    m = mascot("sleep", t, 280)
                    img.paste(m, (20, int(y) - 25), m)
                bubble(img, 310, int(y) + 20, 680, f"Least entertaining: {worst['short']} ({worst['avg']:.0f}). Bring a pillow.", accent=MUTED)
            elif cfg.get("mascot") and t > 0.6 + (n - 1) * step + 0.5:
                m = mascot("hype", t, 280)
                img.paste(m, (20, 1305), m)
                bubble(img, 310, 1350, 680, f"{top[-1]['short']} never bore you.", accent=GOLD)
            yield img
    events = [(0.0, "whoosh", 0)] + [(0.6 + j * step, "pop", 0) for j in range(n)]
    events += [(0.6 + (n - 1) * step + 0.5, "chime", 0)]
    if worst:
        events += [(0.6 + n * step, "womp", 0)]
    return dur, frames, events


def scene_outro(cfg):
    c = cfg["cta"]
    dur = 4.2
    cap = max_size(c["url"], DISPLAY_X, 170, W - 140)

    def frames():
        for i in range(int(dur * FPS)):
            t = i / FPS
            img = background()
            d = ImageDraw.Draw(img)
            a = ease_out(t / 0.5)
            for j, ln in enumerate(c["headline"]):
                center_text(d, 360 + j * 130 + 30 * (1 - a), ln, font(DISPLAY_X, 120), INK)
            if t > 0.45:
                k = min(ease_back((t - 0.45) / 0.5), 1.04)
                center_text(d, 680, c["url"], font(DISPLAY_X, cap * k), GOLD)
            if t > 1.0:
                center_text(d, 900, c["free_line"], font(BODY_X, 60), INK)
            if t > 1.4:
                p = min(ease_back((t - 1.4) / 0.45), 1.04)
                fp = font(BODY_X, max_size(c["pay_line"], BODY_X, 58, 880) * p)
                tw = d.textlength(c["pay_line"], font=fp)
                y0 = 1000
                d.rounded_rectangle([W / 2 - tw / 2 - 44, y0, W / 2 + tw / 2 + 44, y0 + 112 * p], radius=56, fill=GOLD)
                center_text(d, y0 + 24 * p, c["pay_line"], fp, BG)
            if t > 2.0:
                center_text(d, 1180, c["tagline"], font(BODY_X, 54), MUTED)
            if cfg.get("mascot") and t > 1.6:
                m = mascot("happy", t, 300)
                k = ease_back((t - 1.6) / 0.5)
                img.paste(m, (int(W / 2 - 150), int(1290 + 160 * (1 - min(k, 1)))), m)
            yield img
    events = [(0.0, "whoosh", 0), (0.45, "hit", 0), (1.4, "coin", 0), (2.0, "chime", 0)]
    return dur, frames, events


# ============================================================ assembling a video
def encode(frames, path):
    tmp = path[:-4] + ".part.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-r", str(FPS), tmp]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    last = None
    for img in frames():
        p.stdin.write(img.tobytes())
        last = img
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit(f"ffmpeg failed on {path}")
    os.replace(tmp, path)
    return last


def build(cfg, scenes, out_base, cover_index):
    work = tempfile.mkdtemp()
    parts, events, t0, cover = [], [], 0.0, None
    for i, (dur, frames, ev) in enumerate(scenes):
        path = os.path.join(work, f"{i:02d}.mp4")
        last = encode(frames, path)
        if i == cover_index:
            cover = last
        parts.append(path)
        events += [(t0 + t, k, v) for t, k, v in ev]
        t0 += int(dur * FPS) / FPS
    listing = os.path.join(work, "list.txt")
    open(listing, "w").write("".join(f"file '{p}'\n" for p in parts))
    joined = os.path.join(work, "joined.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", joined], check=True)

    audio = np.zeros(int(SR * (t0 + 0.5)))
    if cfg.get("sound", True):
        cache = {}
        for t, kind, v in events:
            key = (kind, v if kind == "tick" else 0)
            if key not in cache:
                cache[key] = sfx(kind, v)
            s = cache[key]
            i0 = int(t * SR)
            i1 = min(len(audio), i0 + len(s))
            audio[i0:i1] += s[: i1 - i0]
        audio *= cfg.get("sound_volume", 0.8)
        audio = np.tanh(audio * 1.2) * 0.9                # gentle limiter
    wav = os.path.join(work, "sound.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32000).astype(np.int16).tobytes())
    out = out_base + ".mp4"
    tmp = out_base + ".part.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", joined, "-i", wav, "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-shortest", "-movflags", "+faststart", tmp], check=True)
    os.replace(tmp, out)
    if cover is not None:
        cover.save(out_base + "-cover.png")
    shutil.rmtree(work, ignore_errors=True)
    return out


def caption(cfg, fmt, league, when, lines):
    name = LEAGUE_PLAIN[league]
    title = cfg["formats"][fmt]["title"].format(league=name, when=when)
    body = [f"TITLE: {title}", "", "DESCRIPTION:"]
    body += lines
    body += ["", f"Every game rated at https://thrillcheck.com. {cfg['cta']['free_line']} {cfg['cta']['pay_line']}",
             "", f"{HASHTAGS[league]} #Shorts #SportsReplay #NoSpoilers #Thrillcheck"]
    return "\n".join(body) + "\n"


# ============================================================ formats
def make(cfg, fmt, league, games, out_dir, group=None, today=None):
    os.makedirs(out_dir, exist_ok=True)
    if fmt == "teams":
        lg = [g for g in games if g["league"] == league and g.get("score") is not None]
        if not lg:
            return None
        season = max(g["season"] for g in lg)
        label = next(g["seasonLabel"] for g in lg if g["season"] == season)
        rows = team_table(games, league, season, cfg["formats"]["teams"].get("min_games", 3))
        if len(rows) < 5:
            return None
        top = rows[: cfg["formats"]["teams"].get("top", 5)]
        worst = rows[-1]
        scenes = [scene_intro(cfg, "teams", league, f"{label} SEASON", len(rows), mood="hype"),
                  scene_teams(cfg, rows, worst, league, label), scene_outro(cfg)]
        base = os.path.join(out_dir, f"teams-{league}-{label}")
        build(cfg, scenes, base, cover_index=1)
        lines = [f"Which {LEAGUE_PLAIN[league]} teams play the most entertaining games? Ranked by average Thrill Score "
                 "(how close, tense and dramatic their games are). No scores, no spoilers.", ""]
        lines += [f"#{i + 1}  {r['name']}  ({r['avg']:.0f})" for i, r in enumerate(top)]
        lines += ["", f"Least entertaining: {worst['name']} ({worst['avg']:.0f})", "Is your team on the list? Tell us in the comments."]
        open(base + ".txt", "w").write(caption(cfg, "teams", league, label, lines))
        return base + ".mp4"

    top, pool = latest_group(games, league, group, today)
    if not pool:
        return None
    when = top["groupLabel"]
    gslug = str(top["group"]).replace(" ", "")
    base = os.path.join(out_dir, f"{fmt}-{league}-{gslug}")
    if fmt == "worth":
        n = cfg["formats"]["worth"].get("games", 4)
        best = pool[: max(1, n - 1)]
        rest = [g for g in pool[len(best):] if g["label"] in ("Skip it", "Fans only")]
        skip = rest[-1] if rest else None
        order = ([skip] if skip else []) + best[::-1]
        scenes = [scene_intro(cfg, "worth", league, when, len(pool))]
        for i, g in enumerate(order):
            from_end = len(order) - 1 - i
            if skip and i == 0:
                scenes.append(scene_card(cfg, g, "SKIP THIS ONE", league, when, rank_color=MUTED))
            else:
                scenes.append(scene_card(cfg, g, {0: "#1  WATCH THIS"}.get(from_end, f"#{from_end + 1}"), league, when))
        scenes.append(scene_outro(cfg))
        build(cfg, scenes, base, cover_index=len(scenes) - 2)
        lines = [f"Missed the {LEAGUE_PLAIN[league]}? Here's what's worth the replay, rated by how close, tense and "
                 "dramatic each game was. No scores, no spoilers.", ""]
        lines += [f"{g['score']}  {g.get('awayShort') or g['away']} at {g.get('homeShort') or g['home']}  ({g['label']})" for g in reversed(order)]
    elif fmt == "guess":
        n = cfg["formats"]["guess"].get("games", 3)
        picks = [pool[0]] + ([pool[-1]] if len(pool) > 1 else []) + ([pool[len(pool) // 2]] if len(pool) > 2 else [])
        picks = sorted({g["id"]: g for g in picks[:n]}.values(), key=lambda g: g["date"])
        pause = cfg["formats"]["guess"].get("pause_seconds", 2.6)
        scenes = [scene_intro(cfg, "guess", league, when, len(pool))]
        for i, g in enumerate(picks):
            scenes.append(scene_card(cfg, g, f"GAME {i + 1} OF {len(picks)}", league, when, guess_pause=pause))
        scenes.append(scene_outro(cfg))
        build(cfg, scenes, base, cover_index=1)
        lines = ["How close was your guess? Thrill Scores rate how close, tense and dramatic a game was, "
                 "from 0 to 100. No scores, no spoilers.", ""]
        lines += [f"{g.get('awayShort') or g['away']} at {g.get('homeShort') or g['home']}: {g['score']}" for g in picks]
        lines += ["", "Comment your guesses before you check!"]
    elif fmt == "snooze":
        g = pool[-1]
        scenes = [scene_intro(cfg, "snooze", league, when, len(pool), mood="sleep"),
                  scene_card(cfg, g, "SNOOZEFEST", league, when, rank_color=MUTED, dur=6.2),
                  scene_outro(cfg)]
        build(cfg, scenes, base, cover_index=1)
        lines = [f"The least exciting {LEAGUE_PLAIN[league]} game of {when}: "
                 f"{g.get('awayName') or g['away']} at {g.get('homeName') or g['home']}, Thrill Score {g['score']}. "
                 "Save your evening for something better. No scores, no spoilers."]
    else:
        raise SystemExit(f"Unknown format {fmt}")
    open(base + ".txt", "w").write(caption(cfg, fmt, league, when, lines))
    return base + ".mp4"


def central_today():
    now = datetime.now(timezone.utc) - timedelta(hours=6)
    return now.date()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", default="worth", choices=["worth", "guess", "teams", "snooze"])
    ap.add_argument("--league", default="nfl", choices=list(LEAGUE_NAME))
    ap.add_argument("--games", default=os.path.join(HERE, "site", "data", "games.json"))
    ap.add_argument("--group", help="week / matchweek number, or NBA date like 2026-11-08")
    ap.add_argument("--out", default=os.path.join(HERE, "shorts"))
    ap.add_argument("--daily", action="store_true", help="make today's formats from the config for every league")
    ap.add_argument("--today", help="pretend today is YYYY-MM-DD (for testing)")
    a = ap.parse_args()

    cfg = load_config()
    games = load_games(a.games)
    today = date.fromisoformat(a.today) if a.today else central_today()
    if not a.daily:
        out = make(cfg, a.format, a.league, games, a.out, a.group, today)
        print(f"Made {out}" if out else f"Nothing to make for {a.format} {a.league}")
        return
    day = today.strftime("%a").lower()
    fmts = cfg["rotation"].get(day, ["worth"])
    for lg in cfg["leagues"]:
        recent = [g for g in games if g["league"] == lg and g["date"] <= today.isoformat()]
        fresh = recent and max(g["date"] for g in recent) >= (today - timedelta(days=cfg.get("max_age_days", 4))).isoformat()
        for fmt in fmts:
            if fmt != "teams" and not fresh:
                print(f"Skipped {fmt} {lg}: no games in the last {cfg.get('max_age_days', 4)} days")
                continue
            try:
                out = make(cfg, fmt, lg, games, a.out, today=today)
                print(f"Made {out}" if out else f"Nothing to make for {fmt} {lg}")
            except Exception as e:
                print(f"Failed {fmt} {lg}: {e}")


if __name__ == "__main__":
    main()
