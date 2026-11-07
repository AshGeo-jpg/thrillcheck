"""
og.py  -  draws the spoiler-free share image for each game (1200x630), the site's
default share image, and the app icons. Fonts are bundled in ./fonts (SIL Open Font License).
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")
BG, INK, MUTED, GOLD = (11, 18, 32), (238, 242, 247), (142, 155, 176), (245, 189, 58)
LABEL = {"Must watch": (255, 106, 61), "Worth watching": (53, 196, 134), "Fans only": (111, 155, 255), "Skip it": (137, 148, 168)}
LEAGUE = {"nfl": "NFL", "nba": "NBA", "epl": "PREMIER LEAGUE", "laliga": "LA LIGA"}


def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size)


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def gauge(d, cx, cy, r, w, score, color, track=(36, 48, 72)):
    box = [cx - r, cy - r, cx + r, cy + r]
    d.arc(box, 135, 405, fill=track, width=w)
    if score > 0:
        d.arc(box, 135, 135 + 270 * score / 100, fill=color, width=w)
    for ang, col in ((135, color if score > 0 else track), (135 + 270 * score / 100, color)):
        import math
        x, y = cx + (r - w / 2) * math.cos(math.radians(ang)), cy + (r - w / 2) * math.sin(math.radians(ang))
        if score > 0:
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=col)


def fit(d, text, name, size, max_w):
    while size > 30:
        f = font(name, size)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return font(name, size)


def wordmark(d, x, y, size=46):
    f = font("big-shoulders-display-900.ttf", size)
    d.text((x, y), "THRILL", font=f, fill=INK)
    d.text((x + d.textlength("THRILL", font=f), y), "CHECK", font=f, fill=GOLD)


def stripes(img, colors):
    d = ImageDraw.Draw(img)
    w = img.width // 10
    for i in range(10):
        d.rectangle([i * w, 0, (i + 1) * w, img.height], fill=colors[i % 2])


def game_card(g, path):
    img = Image.new("RGB", (1200, 630), BG)
    d = ImageDraw.Draw(img)
    # team color bar
    d.rectangle([0, 0, 600, 12], fill=hexrgb(g["awayColor"]))
    d.rectangle([600, 0, 1200, 12], fill=hexrgb(g["homeColor"]))
    wordmark(d, 64, 52)
    kick = f"{LEAGUE.get(g['league'], '')}  ·  {g['groupLabel'].upper()}"
    d.text((64, 140), kick, font=font("big-shoulders-display-800.ttf", 34), fill=GOLD)
    tf = "big-shoulders-display-900.ttf"
    d.text((64, 190), g["awayShort"].upper(), font=fit(d, g["awayShort"].upper(), tf, 120, 640), fill=INK)
    d.text((70, 320), "AT", font=font("big-shoulders-display-800.ttf", 40), fill=MUTED)
    d.text((64, 362), g["homeShort"].upper(), font=fit(d, g["homeShort"].upper(), tf, 120, 640), fill=INK)
    col = LABEL[g["label"]]
    gauge(d, 930, 290, 165, 34, g["score"], col)
    nf = font("big-shoulders-display-900.ttf", 150)
    s = str(g["score"])
    d.text((930 - d.textlength(s, font=nf) / 2, 200), s, font=nf, fill=INK)
    lf = font("figtree-800.ttf", 30)
    lab = g["label"].upper()
    lw = d.textlength(lab, font=lf)
    d.rounded_rectangle([930 - lw / 2 - 24, 470, 930 + lw / 2 + 24, 522], radius=26, fill=tuple(int(c * .22) + int(BG[i] * .78) for i, c in enumerate(col)))
    d.text((930 - lw / 2, 478), lab, font=lf, fill=col)
    d.text((64, 556), "Thrillcheck the game.  No scores. No spoilers.", font=font("figtree-700.ttf", 28), fill=MUTED)
    # 256-colour PNG: looks the same for these flat graphics at under half the file size
    img.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(path, optimize=True)


def site_card(path, total):
    img = Image.new("RGB", (1200, 630), BG)
    d = ImageDraw.Draw(img)
    wordmark(d, 64, 60, 56)
    f = font("big-shoulders-display-900.ttf", 150)
    d.text((64, 150), "THRILLCHECK", font=f, fill=INK)
    d.text((64, 290), "THE GAME", font=f, fill=GOLD)
    d.text((64, 470), "Is the replay worth your time? Every game rated 0 to 100.", font=font("figtree-700.ttf", 34), fill=INK)
    d.text((64, 520), f"No scores. No spoilers.  {total:,} games rated.", font=font("figtree-500.ttf", 30), fill=MUTED)
    gauge(d, 1010, 250, 120, 26, 86, LABEL["Must watch"])
    img.save(path, optimize=True)


def icon(path, size):
    s = 512
    img = Image.new("RGB", (s, s), BG)
    d = ImageDraw.Draw(img)
    gauge(d, 256, 262, 170, 46, 78, GOLD, track=(40, 52, 78))
    d.line([(172, 268), (236, 330), (346, 206)], fill=INK, width=46, joint="curve")
    for x, y in ((172, 268), (346, 206)):
        d.ellipse([x - 23, y - 23, x + 23, y + 23], fill=INK)
    img.resize((size, size), Image.LANCZOS).save(path, optimize=True)


def build_all(games, site_dir, force=False):
    out = os.path.join(site_dir, "og")
    os.makedirs(out, exist_ok=True)
    made = 0
    keep = {"thrillcheck.png"}
    for g in games:
        name = f"{g['id']}-{g['score']}.png"  # score in the name: rescoring makes a new image
        keep.add(name)
        p = os.path.join(out, name)
        if force or not os.path.exists(p):
            game_card(g, p)
            made += 1
    for f in os.listdir(out):  # drop images for old scores so the site does not keep growing
        if f.endswith(".png") and f not in keep:
            os.remove(os.path.join(out, f))
    site_card(os.path.join(out, "thrillcheck.png"), len(games))
    for n in (192, 512, 180):
        icon(os.path.join(site_dir, f"icon-{n}.png"), n)
    return made
