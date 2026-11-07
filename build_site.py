"""
build_site.py  -  turns game ratings into the Thrillcheck website

Steps
  1. Rates every game with score_games.py (free nflverse play-by-play).
  2. Adds spoiler-free vibe tags and a blurb for each game.
     If opinions.py has been run, its Claude-written blurbs and web buzz are used instead.
  3. Writes the site into ./site:
       site/index.html            the app
       site/data/games.json       all game data
       site/games/<id>/index.html one page per game, for Google (spoiler-free titles)
       site/sitemap.xml, robots.txt

Usage
  python build_site.py                 # seasons 2025 and 2026, deploy build
  python build_site.py --inline        # one self-contained file (preview)
  python build_site.py --site-url https://thrillcheck.com
"""
import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone

import pandas as pd

import score_games as S

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
TEMPLATE = os.path.join(HERE, "template.html")
BUZZ = os.path.join(HERE, "results", "buzz_cache.json")
CONFIG = os.path.join(HERE, "config.json")

TEAMS = {
    "ARI": ("Arizona", "Cardinals", "#97233F"), "ATL": ("Atlanta", "Falcons", "#A71930"),
    "BAL": ("Baltimore", "Ravens", "#241773"), "BUF": ("Buffalo", "Bills", "#00338D"),
    "CAR": ("Carolina", "Panthers", "#0085CA"), "CHI": ("Chicago", "Bears", "#0B162A"),
    "CIN": ("Cincinnati", "Bengals", "#FB4F14"), "CLE": ("Cleveland", "Browns", "#311D00"),
    "DAL": ("Dallas", "Cowboys", "#003594"), "DEN": ("Denver", "Broncos", "#FB4F14"),
    "DET": ("Detroit", "Lions", "#0076B6"), "GB": ("Green Bay", "Packers", "#203731"),
    "HOU": ("Houston", "Texans", "#03202F"), "IND": ("Indianapolis", "Colts", "#002C5F"),
    "JAX": ("Jacksonville", "Jaguars", "#006778"), "KC": ("Kansas City", "Chiefs", "#E31837"),
    "LA": ("Los Angeles", "Rams", "#003594"), "LAC": ("Los Angeles", "Chargers", "#0080C6"),
    "LV": ("Las Vegas", "Raiders", "#000000"), "MIA": ("Miami", "Dolphins", "#008E97"),
    "MIN": ("Minnesota", "Vikings", "#4F2683"), "NE": ("New England", "Patriots", "#002244"),
    "NO": ("New Orleans", "Saints", "#D3BC8D"), "NYG": ("New York", "Giants", "#0B2265"),
    "NYJ": ("New York", "Jets", "#125740"), "PHI": ("Philadelphia", "Eagles", "#004C54"),
    "PIT": ("Pittsburgh", "Steelers", "#FFB612"), "SEA": ("Seattle", "Seahawks", "#002244"),
    "SF": ("San Francisco", "49ers", "#AA0000"), "TB": ("Tampa Bay", "Buccaneers", "#D50A0A"),
    "TEN": ("Tennessee", "Titans", "#4B92DB"), "WAS": ("Washington", "Commanders", "#5A1414"),
}

ROUND_NAMES = {19: "Wild Card", 20: "Divisional", 21: "Conference", 22: "Super Bowl"}

# Spoiler-free blurbs, chosen by label and vibe. Never mention scores, winners or endings.
BLURBS = {
    "Must watch": [
        "Clear your evening. This one never lets you settle into the couch.",
        "The kind of game you'll want to talk about tomorrow. Watch every minute.",
        "Pure tension from start to finish. Do not check your phone.",
        "One of the best games of the season. Hit play and enjoy the ride.",
        "Chaotic, gripping and worth every minute of your time.",
    ],
    "Worth watching": [
        "A solid watch with real moments of drama. Time well spent.",
        "Plenty to enjoy here, and it holds your attention when it counts.",
        "Not an all-timer, but a genuinely good game to put on tonight.",
        "Worth the time, especially if you like a competitive game.",
        "A good game with stretches that pull you to the edge of your seat.",
    ],
    "Fans only": [
        "Fine if it's your team. Everyone else can probably catch the highlights.",
        "Some bright spots, but it asks a lot of patience from neutral fans.",
        "A watch for the loyal. Others have better options this week.",
        "Has its moments, though most of them are for diehards.",
        "Watchable with a second screen open. Not much more than that.",
        "Some decent stretches, but plenty of slow ones too.",
        "Worth it for the faithful. Neutral fans can look elsewhere.",
        "A middle-of-the-road game. Good background viewing at best.",
    ],
    "Skip it": [
        "Save your evening for something else. The highlights will do.",
        "Not much drama here. Your time is better spent on another game.",
        "Unless it's your team, you can comfortably skip this one.",
        "A quiet one. Check out the other games this week instead.",
        "This one never really gets going. Pick a different game.",
        "Low on drama from start to finish. Easy skip.",
        "Light on tension. Catch the highlights and move on.",
        "Not a game that needs your full evening.",
        "Little here to hold a neutral fan's attention.",
        "Safe to skip. There are better uses of three hours.",
    ],
}

SPOILER = re.compile(r"\d+\s*[-–]\s*\d+|\b(won|wins|win|beat|beats|defeat|lost|loses|loss|victory|"
                     r"upset|comeback|overtime|walk-?off|winning|shootout|blowout|offens\w*|defens\w*|"
                     r"record|touchdowns?|yards|points?|scor\w*)\b", re.I)


def extra_columns(seasons):
    cols = ["game_id", "start_time", "div_game", "away_score", "home_score", "qtr",
            "passer_player_name", "passing_yards", "rusher_player_name", "rushing_yards",
            "receiver_player_name", "receiving_yards", "yards_gained"]
    out = []
    for s in seasons:
        p = pd.read_parquet(os.path.join(HERE, "data", f"pbp_{s}.parquet"), columns=cols)
        for gid, g in p.groupby("game_id"):
            pas = g.groupby("passer_player_name").passing_yards.sum().max() if g.passing_yards.notna().any() else 0
            rus = g.groupby("rusher_player_name").rushing_yards.sum().max() if g.rushing_yards.notna().any() else 0
            rec = g.groupby("receiver_player_name").receiving_yards.sum().max() if g.receiving_yards.notna().any() else 0
            big = int((g.yards_gained >= 40).sum())
            out.append({
                "game_id": gid, "start_time": g.start_time.dropna().iloc[0] if g.start_time.notna().any() else "",
                "div_game": int(g.div_game.max() or 0),
                "away_final": int(g.away_score.max()), "home_final": int(g.home_score.max()),
                "ot": bool((g.qtr >= 5).any()),
                "star": bool((pas or 0) >= 350 or (rus or 0) >= 150 or (rec or 0) >= 150),
                "big_plays": big,
            })
    return pd.DataFrame(out)


def vibes(r):
    """Only tags a fan could know BEFORE kickoff, so they never hint at how the game went."""
    v = []
    if r.week >= 19:
        v.append(ROUND_NAMES.get(r.week, "Playoffs"))
    if r.div_game:
        v.append("Division rivalry")
    try:
        t = datetime.strptime(r.start_time, "%m/%d/%y, %H:%M:%S")
        if t.hour >= 19 or t.weekday() in (0, 3):
            v.append("Prime time")
    except Exception:
        pass
    return v[:3]


def pick(options, key):
    return options[int(hashlib.md5(key.encode()).hexdigest(), 16) % len(options)]


def kickoff(start, date):
    try:
        t = datetime.strptime(start, "%m/%d/%y, %H:%M:%S")
        return t.strftime("%a %b %-d · %-I:%M %p ET")
    except Exception:
        return datetime.strptime(date, "%Y-%m-%d").strftime("%a %b %-d")


def build_games(seasons):
    df = S.score(seasons).merge(extra_columns(seasons), on="game_id")
    buzz = json.load(open(BUZZ)) if os.path.exists(BUZZ) else {}
    games, used = [], {}
    for r in df.itertuples():
        b = buzz.get(r.game_id) or {}
        score = int(r.watch_score)
        if b.get("buzz") is not None:
            score = int(round(0.75 * score + 0.25 * b["buzz"]))
        label = next(l for t, l in S.LABELS if score >= t)
        v = vibes(r)
        wk = (r.season, r.week, label)
        options = BLURBS[label]
        start = int(hashlib.md5(r.game_id.encode()).hexdigest(), 16) % len(options)
        used_b = used.setdefault(wk, set())
        base = next((options[(start + i) % len(options)] for i in range(len(options))
                     if options[(start + i) % len(options)] not in used_b), options[start])
        used_b.add(base)
        blurb = b.get("blurb") or base
        assert not SPOILER.search(blurb), blurb
        a, h = TEAMS[r.away], TEAMS[r.home]
        games.append({
            "id": r.game_id.lower().replace("_", "-"), "league": "nfl", "season": int(r.season),
            "seasonLabel": str(r.season), "group": str(r.week), "groupOrder": int(r.week),
            "groupLabel": ROUND_NAMES.get(r.week, "") or f"Week {r.week}",
            "groupShort": {19: "WC", 20: "DIV", 21: "CONF", 22: "SB"}.get(r.week, str(r.week)),
            "date": r.date,
            "kickoff": kickoff(r.start_time, r.date),
            "away": r.away, "home": r.home,
            "awayName": f"{a[0]} {a[1]}", "homeName": f"{h[0]} {h[1]}",
            "awayShort": a[1], "homeShort": h[1], "awayColor": a[2], "homeColor": h[2],
            "score": score, "label": label, "blurb": blurb, "vibes": v,
            "source": "data + web buzz" if b.get("buzz") is not None else "game data",
            # Only shown after the viewer taps Reveal.
            "final": {"away": r.away_final, "home": r.home_final, "ot": r.ot},
        })
    games.sort(key=lambda g: (g["season"], g["groupOrder"], g["date"], g["kickoff"]))
    return games


NBA_TEAMS = {
    "ATL": "#C8102E", "BKN": "#111111", "BOS": "#007A33", "CHA": "#1D1160", "CHI": "#CE1141", "CLE": "#860038",
    "DAL": "#00538C", "DEN": "#0E2240", "DET": "#C8102E", "GS": "#1D428A", "HOU": "#CE1141", "IND": "#002D62",
    "LAC": "#C8102E", "LAL": "#552583", "MEM": "#5D76A9", "MIA": "#98002E", "MIL": "#00471B", "MIN": "#0C2340",
    "NO": "#0C2340", "NY": "#F58426", "OKC": "#007AC1", "ORL": "#0077C0", "PHI": "#006BB6", "PHX": "#1D1160",
    "POR": "#E03A3E", "SA": "#3D4246", "SAC": "#5A2D81", "TOR": "#CE1141", "UTAH": "#002B5C", "WSH": "#002B5C",
}
NBA_DIVISIONS = [{"BOS", "BKN", "NY", "PHI", "TOR"}, {"CHI", "CLE", "DET", "IND", "MIL"},
                 {"ATL", "CHA", "MIA", "ORL", "WSH"}, {"DEN", "MIN", "OKC", "POR", "UTAH"},
                 {"GS", "LAC", "LAL", "PHX", "SAC"}, {"DAL", "HOU", "MEM", "NO", "SA"}]


def build_nba(seasons):
    import score_nba as N
    df = N.score(seasons)
    buzz = json.load(open(BUZZ)) if os.path.exists(BUZZ) else {}
    games, used = [], {}
    for r in df.itertuples():
        gid = f"nba-{r.date}-{r.away}-{r.home}".lower()
        b = buzz.get(gid) or {}
        score = int(r.watch_score)
        if b.get("buzz") is not None:
            score = int(round(0.75 * score + 0.25 * b["buzz"]))
        label = next(l for t, l in N.LABELS if score >= t)
        options = BLURBS[label]
        start = int(hashlib.md5(gid.encode()).hexdigest(), 16) % len(options)
        used_b = used.setdefault((r.season, r.date, label), set())
        base = next((options[(start + i) % len(options)] for i in range(len(options))
                     if options[(start + i) % len(options)] not in used_b), options[start])
        used_b.add(base)
        blurb = b.get("blurb") or base
        assert not SPOILER.search(blurb), blurb
        tags = []
        if r.season_type == 3:
            tags.append("Playoffs")
        elif r.season_type == 5:
            tags.append("Play-In")
        if any(r.away in d and r.home in d for d in NBA_DIVISIONS):
            tags.append("Division rivalry")
        t = r.tip
        day = datetime.strptime(r.date, "%Y-%m-%d")
        games.append({
            "id": gid, "league": "nba", "season": int(r.season),
            "seasonLabel": f"{r.season - 1}-{str(r.season)[2:]}",
            "group": r.date, "groupOrder": int(r.date.replace("-", "")),
            "groupLabel": day.strftime("%a, %b %-d"), "groupShort": day.strftime("%b %-d"),
            "date": r.date, "kickoff": t.strftime("%a %b %-d · %-I:%M %p ET"),
            "away": r.away, "home": r.home,
            "awayName": f"{r.away_city} {r.away_short}", "homeName": f"{r.home_city} {r.home_short}",
            "awayShort": r.away_short, "homeShort": r.home_short,
            "awayColor": NBA_TEAMS.get(r.away, "#333"), "homeColor": NBA_TEAMS.get(r.home, "#333"),
            "score": score, "label": label, "blurb": blurb, "vibes": tags,
            "source": "data + web buzz" if b.get("buzz") is not None else "game data",
            "final": {"away": int(r.away_final), "home": int(r.home_final), "ot": bool(r.ot)},
        })
    games.sort(key=lambda g: (g["season"], g["groupOrder"], g["kickoff"]))
    return games


AI_CACHE = os.path.join(HERE, "results", "ai_ratings.json")
FIXTURES = os.path.join(HERE, "results", "fixtures.json")


def merge_ai(games):
    """Add AI-rated games (early NFL/NBA ratings, soccer) and compare AI vs data where both exist."""
    ai = json.load(open(AI_CACHE)) if os.path.exists(AI_CACHE) else {}
    fixtures = json.load(open(FIXTURES)) if os.path.exists(FIXTURES) else []
    by_id = {g["id"]: g for g in games}
    checks = []
    for g in games:
        g.setdefault("rating", "final")
        r = ai.get(g["id"])
        if r and r.get("score") is not None:
            g["aiScore"] = r["score"]
            checks.append((g["id"], g["score"], r["score"], r["score"] - g["score"]))
    used = {}
    for fx in fixtures:
        r = ai.get(fx["id"])
        if fx["id"] in by_id or not r or r.get("status") not in ("early", "final") or r.get("score") is None:
            continue
        score = int(r["score"])
        label = next(l for t, l in S.LABELS if score >= t)
        blurb = r.get("blurb") or ""
        if not blurb or SPOILER.search(blurb):
            opts = BLURBS[label]
            seen = used.setdefault((fx["league"], fx["group"], label), set())
            start = int(hashlib.md5(fx["id"].encode()).hexdigest(), 16) % len(opts)
            blurb = next((opts[(start + i) % len(opts)] for i in range(len(opts)) if opts[(start + i) % len(opts)] not in seen), opts[start])
            seen.add(blurb)
        g = {k: v for k, v in fx.items() if k != "start"}
        g.update({"score": score, "label": label, "blurb": blurb, "final": r["final"],
                  "rating": r["status"], "source": "match reports, read by AI"})
        games.append(g)
    if checks:
        import csv
        os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
        with open(os.path.join(HERE, "results", "ai_check.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["game_id", "data_score", "ai_score", "difference"])
            w.writerows(sorted(checks, key=lambda c: -abs(c[3])))
        big = [c for c in checks if abs(c[3]) >= 20]
        print(f"AI vs data: {len(checks)} games compared, {len(big)} differ by 20+ points (see results/ai_check.csv)")
    games.sort(key=lambda g: (g["league"], g["season"], g["groupOrder"], g["kickoff"]))
    return games


def page_meta(g):
    when = f"{g['groupLabel']}, {g['seasonLabel']}" if g["league"] != "nba" else f"{g['groupLabel']}, {g['date'][:4]}"
    title = f"{g['awayShort']} at {g['homeShort']} ({when}): Worth watching? | Thrillcheck"
    desc = (f"Is {g['awayName']} at {g['homeName']} worth watching? Thrillcheck rates it "
            f"\"{g['label']}\" with no scores and no spoilers.")
    return title, desc


def head_extra(root, site_url, og_image):
    """Tags only the deployed site gets: app manifest, icons, share image, offline helper."""
    img = f"{site_url}/og/{og_image}"
    return (f'<link rel="manifest" href="{root}manifest.webmanifest">\n'
            f'<link rel="icon" type="image/png" sizes="192x192" href="{root}icon-192.png">\n'
            f'<link rel="apple-touch-icon" href="{root}icon-180.png">\n'
            '<meta name="apple-mobile-web-app-capable" content="yes">\n'
            '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">\n'
            f'<meta property="og:image" content="{img}">\n<meta property="og:image:width" content="1200">\n'
            '<meta property="og:image:height" content="630">\n<meta name="twitter:card" content="summary_large_image">\n'
            f'<meta name="twitter:image" content="{img}">\n<script>window.TC_SW = "{root}sw.js";</script>')


def split_template(tpl):
    """Deployed site: move the shared design and code into app.css / app.js so every page stays small."""
    css = tpl[tpl.index("<style>") + 7: tpl.index("</style>")]
    i = tpl.rindex("<script>"); j = tpl.rindex("</script>")
    js = (tpl[i + 8: j].replace("const RAW = __DATA__;", "const RAW = window.TC_DATA || null;")
          .replace('const START = "__START__";', 'const START = window.TC_START || "";')
          .replace('fetch("__DATAPATH__")', "fetch(window.TC_DATAPATH)"))
    v = hashlib.md5((css + js).encode()).hexdigest()[:8]
    page = (tpl[:tpl.index("<style>")] + f'<link rel="stylesheet" href="__ROOT__app.css?v={v}">'
            + tpl[tpl.index("</style>") + 8: i]
            + f'<script>window.TC_START = "__START__"; window.TC_DATAPATH = "__DATAPATH__";</script>\n'
            + f'<script src="__ROOT__app.js?v={v}"></script>' + tpl[j + 9:])
    assert "__DATA__" not in js and "__START__" not in js and "__DATAPATH__" not in js
    return page, css, js


def render(template, title, desc, data_json, start, canonical, data_path="data/games.json", extra=""):
    return (template.replace("__HEADEXTRA__", extra).replace("__DATAPATH__", data_path).replace("__TITLE__", title)
            .replace("__DESC__", desc).replace("__CANONICAL__", canonical).replace("__START__", start)
            .replace("__DATA__", data_json))


MANIFEST = {"name": "Thrillcheck", "short_name": "Thrillcheck", "description": "Is the game worth watching? No spoilers.",
            "start_url": "./", "scope": "./", "display": "standalone", "background_color": "#0b1220", "theme_color": "#0b1220",
            "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}

SW = """// Thrillcheck offline helper: always tries the network first, falls back to the last copy.
const CACHE = "thrillcheck-v1";
self.addEventListener("install", (e) => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  e.respondWith(fetch(req).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req).then((r) => r || caches.match("./"))));
});
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inline", action="store_true", help="single self-contained preview file")
    ap.add_argument("--site-url", default="https://thrillcheck.com")
    ap.add_argument("--seasons", nargs="*", type=int, default=[2025, 2026], help="NFL seasons")
    ap.add_argument("--nba-seasons", nargs="*", type=int, default=[2026, 2027], help="NBA seasons (2027 = 2026-27)")
    a = ap.parse_args()

    config = json.load(open(CONFIG))
    games = merge_ai(build_games(a.seasons) + (build_nba(a.nba_seasons) if a.nba_seasons else []))
    payload = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
               "config": config, "games": games}
    tpl = open(TEMPLATE).read()
    os.makedirs(os.path.join(SITE, "data"), exist_ok=True)
    home_title = "Thrillcheck the game: Is it worth watching? No spoilers."
    home_desc = ("Missed the game? Thrillcheck rates NFL, NBA, Premier League and La Liga games from Must watch to Skip it, "
                 "with no scores and no spoilers.")

    if a.inline:
        html = render(tpl, "Thrillcheck", home_desc,
                      json.dumps(payload, separators=(",", ":")), "", a.site_url + "/", extra="")
        open(os.path.join(SITE, "preview.html"), "w").write(html)
        print("Wrote site/preview.html", len(html) // 1024, "KB")
        return

    import og
    made = og.build_all(games, SITE)
    json.dump(payload, open(os.path.join(SITE, "data", "games.json"), "w"), separators=(",", ":"))
    json.dump(MANIFEST, open(os.path.join(SITE, "manifest.webmanifest"), "w"), indent=1)
    open(os.path.join(SITE, "sw.js"), "w").write(SW)
    page_tpl, css, js = split_template(tpl)
    open(os.path.join(SITE, "app.css"), "w").write(css)
    open(os.path.join(SITE, "app.js"), "w").write(js)

    def page(rel, title, desc, start, image="thrillcheck.png"):
        depth = rel.count("/") + (1 if rel else 0)
        root = "../" * depth
        d = os.path.join(SITE, rel)
        os.makedirs(d, exist_ok=True)
        url = f"{a.site_url}/{rel + '/' if rel else ''}"
        open(os.path.join(d, "index.html"), "w").write(render(
            page_tpl.replace("__ROOT__", root), title, desc, "null", start, url, root + "data/games.json", head_extra(root, a.site_url, image)))
        urls.append(url)

    urls = []
    page("", home_title, home_desc, "")
    for g in games:
        t, desc = page_meta(g)
        page(f"games/{g['id']}", t, desc, "game-" + g["id"], f"{g['id']}-{g['score']}.png")
    page("about", "About Thrillcheck", "Thrillcheck rates whether a game is worth watching, with no scores and no spoilers.", "about")
    page("privacy", "Privacy policy | Thrillcheck", "How Thrillcheck handles your data.", "privacy")
    page("contact", "Contact | Thrillcheck", "Get in touch with Thrillcheck.", "contact")
    names = {"nfl": "NFL", "nba": "NBA", "epl": "Premier League", "laliga": "La Liga"}
    teams = {}
    for g in games:
        teams[(g["league"], g["away"])] = g["awayName"]
        teams[(g["league"], g["home"])] = g["homeName"]
    for (lg, code), name in sorted(teams.items()):
        page(f"teams/{lg}-{code.lower()}", f"{name} games: which are worth watching? No spoilers | Thrillcheck",
             f"Every {name} game rated from Must watch to Skip it, with no scores and no spoilers.", f"team-{lg}-{code.lower()}")
    for lg, season, label in sorted({(g["league"], g["season"], g["seasonLabel"]) for g in games}):
        page(f"best/{lg}-{season}", f"Best {names[lg]} games of {label}, ranked without spoilers | Thrillcheck",
             f"The most watchable {names[lg]} games of the {label} season, ranked by Thrill Score. No scores, no spoilers.",
             f"best-{lg}-{season}")
    open(os.path.join(SITE, "sitemap.xml"), "w").write(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(f"  <url><loc>{u}</loc></url>" for u in urls) + "\n</urlset>\n")
    open(os.path.join(SITE, "robots.txt"), "w").write(f"User-agent: *\nAllow: /\nSitemap: {a.site_url}/sitemap.xml\n")
    print(f"{made} new share images")
    print(f"Built site/ with {len(games)} game pages and {len(urls)} pages in the sitemap")


if __name__ == "__main__":
    main()
