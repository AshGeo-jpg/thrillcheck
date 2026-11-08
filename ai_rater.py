"""
ai_rater.py  -  rates finished games from web reports, minutes after they end

How it works for each finished game that has no rating yet:
  1. Searches the web (Brave Search API) for recaps, box scores and reactions.
  2. Claude reads the search results and fills in the SAME checklist for every game:
       final score as reported by each source, extra time, lead changes, whether it was
       decided late, the biggest deficit overcome, late scores, standout moments,
       and how fans and writers reacted. Plus one spoiler-free blurb.
  3. Accuracy guard: the final score must match in at least two different websites,
     and Claude must be at least 60% confident. Otherwise the game is marked "pending"
     and retried on the next run.
  4. A fixed formula (below) turns the checklist into the Thrill Score, so every
     game is scored the same way.

Status of each rating:
  NFL / NBA        "early"  (replaced by the play-by-play formula when that data arrives)
  Premier League,
  La Liga          "final"  (no free play-by-play exists, so the AI rating stands; shown as beta)

Setup (environment variables):
  BRAVE_API_KEY        api-dashboard.search.brave.com
  ANTHROPIC_API_KEY    console.anthropic.com
  FOOTBALL_DATA_KEY    football-data.org (free) - needed for soccer fixtures only
Optional:
  CLAUDE_MODEL         default claude-haiku-5-5 (cheapest); claude-sonnet-5-5 judges better

Usage:
  python ai_rater.py                  # all leagues
  python ai_rater.py --leagues epl laliga --limit 10
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
CACHE = os.path.join(RESULTS, "ai_ratings.json")
FIXTURES = os.path.join(RESULTS, "fixtures.json")
ET = ZoneInfo("America/New_York")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")

NFL_SCHEDULE = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
NBA_SCHEDULE = "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/schedules/parquet/nba_schedule_{}.parquet"
SOCCER = {"epl": {"code": "PL", "name": "Premier League"}, "laliga": {"code": "PD", "name": "La Liga"}}

# How long after the start a game is surely over, and how each league's formula is scaled.
LEAGUE = {
    "nfl":    {"length_h": 3.75, "margin": 21, "comeback": 14, "lead_changes": 4, "status": "early"},
    "nba":    {"length_h": 2.9,  "margin": 20, "comeback": 15, "lead_changes": 12, "status": "early"},
    "epl":    {"length_h": 2.1,  "margin": 3,  "comeback": 2,  "lead_changes": 2, "status": "final", "soccer": True},
    "laliga": {"length_h": 2.1,  "margin": 3,  "comeback": 2,  "lead_changes": 2, "status": "final", "soccer": True},
}

SOCCER_COLORS = {
    "ARS": "#EF0107", "AVL": "#670E36", "BOU": "#B50E12", "BRE": "#E30613", "BHA": "#0057B8", "BUR": "#6C1D45",
    "CHE": "#034694", "CRY": "#1B458F", "EVE": "#003399", "FUL": "#1A1A1A", "LEE": "#1D428A", "LIV": "#C8102E",
    "MCI": "#5B9BD5", "MUN": "#DA291C", "NEW": "#241F20", "NOT": "#DD0000", "SUN": "#EB172B", "TOT": "#132257",
    "WHU": "#7A263A", "WOL": "#B8860B", "IPS": "#3A64A3", "LEI": "#003090", "SOU": "#D71920", "NOR": "#00A650",
    "RMA": "#00529F", "FCB": "#A50044", "BAR": "#A50044", "ATM": "#CB3524", "ATH": "#EE2523", "RSO": "#0067B1",
    "VIL": "#C9A100", "BET": "#00954C", "SEV": "#D4021D", "VAL": "#F18E00", "CEL": "#5DA9DD", "GET": "#005999",
    "OSA": "#D91A21", "RAY": "#E53027", "MLL": "#E20613", "ALA": "#0761AF", "ESP": "#007FC8", "GIR": "#CD2534",
    "LEV": "#B4053F", "ELC": "#05642C", "OVI": "#0047AB",
}
PALETTE = ["#1F4E79", "#7A1F3D", "#1E6B52", "#5B2C83", "#8A4B08", "#2E4057", "#9C1C1C", "#0E5A8A"]


def _color(tla):
    return SOCCER_COLORS.get(tla) or PALETTE[sum(map(ord, tla or "X")) % len(PALETTE)]


def _when(utc):
    t = pd.Timestamp(utc)
    t = (t.tz_localize("UTC") if t.tzinfo is None else t).tz_convert(ET)
    return t, t.strftime("%Y-%m-%d"), t.strftime("%a %b %-d · %-I:%M %p ET")


# ------------------------------------------------------------------ fixtures
def nfl_fixtures(season):
    from build_site import TEAMS, ROUND_NAMES
    g = pd.read_csv(NFL_SCHEDULE)
    g = g[(g.season == season)]
    out = []
    for r in g.itertuples():
        start = pd.Timestamp(f"{r.gameday} {r.gametime}", tz=ET)
        a, h = TEAMS.get(r.away_team), TEAMS.get(r.home_team)
        if not a or not h:
            continue
        out.append({
            "id": r.game_id.lower().replace("_", "-"), "league": "nfl", "season": season, "seasonLabel": str(season),
            "group": str(r.week), "groupOrder": int(r.week), "groupLabel": ROUND_NAMES.get(r.week, "") or f"Week {r.week}",
            "groupShort": {19: "WC", 20: "DIV", 21: "CONF", 22: "SB"}.get(r.week, str(r.week)),
            "date": r.gameday, "start": start.isoformat(), "kickoff": start.strftime("%a %b %-d · %-I:%M %p ET"),
            "away": r.away_team, "home": r.home_team, "awayName": f"{a[0]} {a[1]}", "homeName": f"{h[0]} {h[1]}",
            "awayShort": a[1], "homeShort": h[1], "awayColor": a[2], "homeColor": h[2],
            "vibes": (["Division rivalry"] if getattr(r, "div_game", 0) == 1 else []),
        })
    return out


def nba_fixtures(season):
    from build_site import NBA_TEAMS
    d = pd.read_parquet(NBA_SCHEDULE.format(season))
    out = []
    for r in d.itertuples():
        t, date, kick = _when(r.start_date)
        day = datetime.strptime(date, "%Y-%m-%d")
        out.append({
            "id": f"nba-{date}-{r.away_abbreviation}-{r.home_abbreviation}".lower(), "league": "nba", "season": season,
            "seasonLabel": f"{season - 1}-{str(season)[2:]}", "group": date, "groupOrder": int(date.replace("-", "")),
            "groupLabel": day.strftime("%a, %b %-d"), "groupShort": day.strftime("%b %-d"),
            "date": date, "start": t.isoformat(), "kickoff": kick,
            "away": r.away_abbreviation, "home": r.home_abbreviation,
            "awayName": f"{r.away_location} {r.away_name}", "homeName": f"{r.home_location} {r.home_name}",
            "awayShort": r.away_name, "homeShort": r.home_name,
            "awayColor": NBA_TEAMS.get(r.away_abbreviation, "#333"), "homeColor": NBA_TEAMS.get(r.home_abbreviation, "#333"),
            "vibes": ["Playoffs"] if r.season_type == 3 else [],
        })
    return out


def soccer_fixtures(league, season_start):
    key = os.environ.get("FOOTBALL_DATA_KEY")
    if not key:
        print(f"Skipping {league}: set FOOTBALL_DATA_KEY (free at football-data.org)")
        return []
    url = f"https://api.football-data.org/v4/competitions/{SOCCER[league]['code']}/matches"
    r = requests.get(url, headers={"X-Auth-Token": key}, params={"season": season_start}, timeout=30)
    r.raise_for_status()
    out = []
    for m in r.json().get("matches", []):
        h, a = m["homeTeam"], m["awayTeam"]
        if not h.get("tla") or not a.get("tla"):
            continue
        t, date, kick = _when(m["utcDate"])
        md = int(m.get("matchday") or 0)
        out.append({
            "id": f"{league}-{date}-{a['tla']}-{h['tla']}".lower(), "league": league, "season": season_start + 1,
            "seasonLabel": f"{season_start}-{str(season_start + 1)[2:]}", "group": str(md), "groupOrder": md,
            "groupLabel": f"Matchweek {md}", "groupShort": f"MW{md}", "date": date, "start": t.isoformat(), "kickoff": kick,
            "away": a["tla"], "home": h["tla"], "awayName": a.get("name", a["tla"]), "homeName": h.get("name", h["tla"]),
            "awayShort": a.get("shortName") or a["tla"], "homeShort": h.get("shortName") or h["tla"],
            "awayColor": _color(a["tla"]), "homeColor": _color(h["tla"]), "vibes": [],
        })
    return out


def load_fixtures(leagues, nfl_season=2026, nba_season=2027, soccer_season=2026):
    fx = []
    for lg in leagues:
        try:
            if lg == "nfl":
                fx += nfl_fixtures(nfl_season)
            elif lg == "nba":
                fx += nba_fixtures(nba_season)
            elif lg in SOCCER:
                fx += soccer_fixtures(lg, soccer_season)
        except Exception as e:
            print(f"Could not load {lg} fixtures: {e}")
    os.makedirs(RESULTS, exist_ok=True)
    json.dump(fx, open(FIXTURES, "w"), indent=0)
    return fx


# ------------------------------------------------------------------ rating
PROMPT = """You check finished {league} games for a site that tells fans whether a game is worth watching,
WITHOUT spoiling it. Below are web search results about {away} at {home} ({date}).

Fill in this checklist using ONLY the search results. If something is not stated, give your best
estimate and lower "confidence". Never invent a score.

Return ONLY JSON:
{{
  "sources": [{{"domain": "<site>", "away_score": <int or null>, "home_score": <int or null>}}],
  "final_away": <int>, "final_home": <int>,
  "extra_time": <true if overtime / extra time>,
  "lead_changes": <int, times the lead changed hands>,
  "decided_late": <true if the result was in doubt in the final {late_window}>,
  "largest_comeback": <the biggest deficit (in {unit}) the team that finished ahead or level overcame; 0 if none>,
  "late_scores": <int, scores in the final {late_window}>,
  "standout_moments": <0-3: records, highlight-reel plays, wild finishes, star performances>,
  "reaction": <-2 dull/boring .. 0 ordinary .. 2 called a classic/instant classic>,
  "confidence": <0.0-1.0, how sure you are about the facts above>,
  "blurb": "<max 20 words: how worth watching it is>"
}}

STRICT SPOILER RULES for the blurb: never mention the score, the winner or loser, who led, how it
ended, comebacks, overtime, blowouts, how much scoring there was, records or stat lines. Describe only
how worth watching it is, in general terms.

Search results:
{snippets}"""


def search(query, key):
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                     headers={"X-Subscription-Token": key, "Accept": "application/json"},
                     params={"q": query, "count": 10, "extra_snippets": "true"}, timeout=20)
    r.raise_for_status()
    out = []
    for x in r.json().get("web", {}).get("results", []):
        dom = re.sub(r"^www\.", "", requests.utils.urlparse(x.get("url", "")).netloc)
        text = " ".join([x.get("description", "")] + (x.get("extra_snippets") or []))
        out.append(f"[{dom}] {x.get('title', '')}: {text}")
    return out


def thrill_score(f, lg):
    p = LEAGUE[lg]
    margin = abs(f["final_away"] - f["final_home"])
    parts = {
        "closeness": 1 - min(margin / p["margin"], 1),
        "late": 1.0 if f.get("decided_late") else 0.0,
        "comeback": min(max(f.get("largest_comeback", 0), 0) / p["comeback"], 1),
        "lead_changes": min(max(f.get("lead_changes", 0), 0) / p["lead_changes"], 1),
        "extra_time": 1.0 if f.get("extra_time") else 0.0,
        "moments": min(max(f.get("standout_moments", 0), 0), 3) / 3,
        "reaction": (min(max(f.get("reaction", 0), -2), 2) + 2) / 4,
        "late_scores": min(max(f.get("late_scores", 0), 0) / 3, 1),
    }
    w = {"closeness": .20, "late": .20, "comeback": .12, "lead_changes": .08, "extra_time": .05,
         "moments": .10, "reaction": .20, "late_scores": .05}
    if p.get("soccer"):  # goals are rare: total goals replaces lead changes
        parts["lead_changes"] = min((f["final_away"] + f["final_home"]) / 5, 1)
    return int(round(100 * sum(w[k] * v for k, v in parts.items())))


def verify(f):
    """Final score must be reported identically by at least two different websites."""
    agree = {s.get("domain") for s in f.get("sources", [])
             if s.get("away_score") == f.get("final_away") and s.get("home_score") == f.get("final_home") and s.get("domain")}
    return len(agree) >= 2 and f.get("confidence", 0) >= 0.6


def rate(fx, brave_key, client, spoiler_re):
    lg = fx["league"]
    unit = "goals" if LEAGUE[lg].get("soccer") else "points"
    late_window = "10 minutes" if LEAGUE[lg].get("soccer") else "5 minutes"
    snippets = []
    for q in (f"{fx['awayName']} vs {fx['homeName']} {fx['date']} recap",
              f"{fx['awayName']} {fx['homeName']} {fx['date']} final score reaction"):
        snippets += search(q, brave_key)
        time.sleep(1.1)
    if not snippets:
        return {"status": "pending", "reason": "no search results"}
    league_name = {"nfl": "NFL", "nba": "NBA"}.get(lg) or SOCCER[lg]["name"]
    msg = client.messages.create(model=MODEL, max_tokens=700, messages=[{"role": "user", "content": PROMPT.format(
        league=league_name, away=fx["awayName"], home=fx["homeName"], date=fx["date"], unit=unit,
        late_window=late_window, snippets="\n".join(snippets[:24]))}])
    text = msg.content[0].text
    f = json.loads(text[text.find("{"): text.rfind("}") + 1])
    if not verify(f):
        return {"status": "pending", "reason": "sources disagree or low confidence", "checked": now()}
    blurb = f.get("blurb", "").strip()
    if spoiler_re.search(blurb):
        blurb = ""
    return {"status": LEAGUE[lg]["status"], "score": thrill_score(f, lg), "blurb": blurb,
            "final": {"away": int(f["final_away"]), "home": int(f["final_home"]), "ot": bool(f.get("extra_time"))},
            "facts": {k: f.get(k) for k in ("lead_changes", "decided_late", "largest_comeback", "late_scores",
                                             "standout_moments", "reaction", "confidence")},
            "rated": now()}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


MAX_TRIES = 6  # each try is 2 searches + 1 Claude call


def due(fx, cache, data_ids):
    """Finished by the clock, not already rated by data, and not already rated (pending retries hourly)."""
    if fx["id"] in data_ids:
        return False
    end = pd.Timestamp(fx["start"]) + timedelta(hours=LEAGUE[fx["league"]]["length_h"])
    if end > pd.Timestamp.now(tz=ET) or end < pd.Timestamp.now(tz=ET) - timedelta(days=10):
        return False
    c = cache.get(fx["id"])
    if not c:
        return True
    if c.get("status") == "pending":
        if c.get("tries", 1) >= MAX_TRIES:  # give up after a few hours; the free data still rates NFL/NBA later
            return False
        return pd.Timestamp.now(tz="UTC") - pd.Timestamp(c.get("checked", "2000-01-01 00:00 UTC").replace(" UTC", ""), tz="UTC") > timedelta(minutes=55)
    return False


def main():
    import anthropic
    from build_site import SPOILER
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="*", default=["nfl", "nba", "epl", "laliga"])
    ap.add_argument("--limit", type=int, default=40, help="max games to rate per run (cost cap)")
    ap.add_argument("--data-ids", default=os.path.join(HERE, "site", "data", "games.json"),
                    help="games already rated from play-by-play data are skipped")
    a = ap.parse_args()

    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    data_ids = set()
    if os.path.exists(a.data_ids):
        data_ids = {g["id"] for g in json.load(open(a.data_ids))["games"] if g.get("rating", "final") == "final"
                    and g["league"] in ("nfl", "nba")}
    fixtures = load_fixtures(a.leagues)
    todo = [f for f in fixtures if due(f, cache, data_ids)][: a.limit]
    print(f"{len(fixtures)} fixtures, {len(todo)} to rate now")
    client, brave_key = anthropic.Anthropic(), os.environ["BRAVE_API_KEY"]
    for fx in todo:
        tries = cache.get(fx["id"], {}).get("tries", 0) + 1
        try:
            cache[fx["id"]] = rate(fx, brave_key, client, SPOILER)
        except Exception as e:
            cache[fx["id"]] = {"status": "pending", "reason": str(e)[:200]}
        if cache[fx["id"]].get("status") == "pending":
            cache[fx["id"]].update(tries=tries, checked=now())
        print(fx["id"], cache[fx["id"]].get("status"), cache[fx["id"]].get("score"))
        json.dump(cache, open(CACHE, "w"), indent=1)


if __name__ == "__main__":
    main()
