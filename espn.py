"""
espn.py  -  free, fast game data from ESPN's public scoreboard feed (no key, no cost)

What it does each run:
  NFL / NBA  For games that finished in the last few days, it downloads ESPN's play-by-play
             (with ESPN's own win probability for the NFL) and saves it in the same shape as
             the nflverse / hoopR files. The usual scoring code then rates those games right
             away, instead of waiting a day for nflverse / hoopR to catch up. When nflverse or
             hoopR has the game, that copy is used; ESPN only fills the gap. If ESPN is ever
             unavailable, nflverse and hoopR still rate everything, just later.
  Soccer     Premier League and La Liga ratings come entirely from ESPN: the minute of every
             goal and red card for this season and last season (the comparison pool).

ESPN does not officially offer this feed to the public, so it could change or stop.
Everything here fails soft: if a request breaks, the run carries on with what it has.

Usage:  python espn.py            (normally run by the GitHub workflow before build_site.py)
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RESULTS = os.path.join(HERE, "results")
CACHE = os.path.join(RESULTS, "espn_cache.json")
BASE = "https://site.api.espn.com/apis/site/v2/sports"
PATHS = {"nfl": "football/nfl", "nba": "basketball/nba", "epl": "soccer/eng.1", "laliga": "soccer/esp.1"}
ET = "America/New_York"
RECENT_DAYS = 6          # NFL/NBA: look back this far for finished games nflverse/hoopR may not have yet
KEEP_DAYS = 21           # drop cached NFL/NBA play-by-play older than this
SOCCER_SEASONS = {2025: ("2025-08-08", "2026-05-31"), 2026: ("2026-08-14", "2027-05-31")}

# ESPN team codes that differ from nflverse
NFL_CODES = {"WSH": "WAS", "LAR": "LA"}
NFL_DIVISIONS = [{"BUF", "MIA", "NE", "NYJ"}, {"BAL", "CIN", "CLE", "PIT"}, {"HOU", "IND", "JAX", "TEN"},
                 {"DEN", "KC", "LV", "LAC"}, {"DAL", "NYG", "PHI", "WAS"}, {"CHI", "DET", "GB", "MIN"},
                 {"ATL", "CAR", "NO", "TB"}, {"ARI", "LA", "SF", "SEA"}]
NFL_POST_WEEK = {1: 19, 2: 20, 3: 21, 5: 22}   # ESPN postseason week -> nflverse week (4 is the Pro Bowl)


# ------------------------------------------------------------------ fetching
def get(path, params=""):
    url = f"{BASE}/{path}{params}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Thrillcheck)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def scoreboard(lg, day):
    sb = get(f"{PATHS[lg]}/scoreboard", f"?dates={day:%Y%m%d}")
    time.sleep(0.15)
    return (sb or {}).get("events", [])


def completed(ev):
    return bool(ev.get("status", {}).get("type", {}).get("completed"))


def clock_secs(display):
    """'12:34' -> 754, '8.1' -> 8.1"""
    try:
        if ":" in display:
            m, s = display.split(":")
            return int(m) * 60 + float(s)
        return float(display)
    except Exception:
        return 0.0


def et(ts):
    return pd.Timestamp(ts).tz_convert(ET)


def load_cache():
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE))
        except Exception:
            pass
    return {}


# ------------------------------------------------------------------ NFL
def nfl_rows(summary):
    """ESPN summary -> rows shaped like nflverse play-by-play (plus the columns build_site needs)."""
    h = summary["header"]
    comp = h["competitions"][0]
    teams = {t["homeAway"]: NFL_CODES.get(t["team"]["abbreviation"], t["team"]["abbreviation"]) for t in comp["competitors"]}
    stype = h.get("season", {}).get("type", 2)
    week = int(h.get("week") or 0)
    if stype == 3:
        week = NFL_POST_WEEK.get(week, 0)
    if stype not in (2, 3) or not week:
        return []
    season = int(h["season"]["year"])
    gid = f"{season}_{week:02d}_{teams['away']}_{teams['home']}"
    t = et(comp["date"])
    plays = {p["id"]: p for d in summary.get("drives", {}).get("previous", []) for p in d.get("plays", [])}
    div = int(any(teams["away"] in d and teams["home"] in d for d in NFL_DIVISIONS))
    rows = []
    for w in summary.get("winprobability", []):
        p = plays.get(w.get("playId"))
        if not p:
            continue
        q = int(p.get("period", {}).get("number") or 0)
        c = clock_secs(p.get("clock", {}).get("displayValue", "0:00"))
        secs = c if q >= 5 else (4 - q) * 900 + c
        rows.append({
            "game_id": gid, "game_date": t.strftime("%Y-%m-%d"), "week": week, "season": season,
            "season_type": "POST" if stype == 3 else "REG", "home_team": teams["home"], "away_team": teams["away"],
            "qtr": q, "game_seconds_remaining": float(secs), "home_wp": float(w.get("homeWinPercentage", np.nan)),
            "total_home_score": int(p.get("homeScore") or 0), "total_away_score": int(p.get("awayScore") or 0),
            "start_time": t.strftime("%m/%d/%y, %H:%M:%S"), "div_game": div,
            "away_score": int(p.get("awayScore") or 0), "home_score": int(p.get("homeScore") or 0),
            "passer_player_name": None, "passing_yards": np.nan, "rusher_player_name": None, "rushing_yards": np.nan,
            "receiver_player_name": None, "receiving_yards": np.nan,
            "yards_gained": float(p.get("statYardage") or 0), "espn_id": comp["id"],
        })
    return rows if len(rows) >= 20 else []


# ------------------------------------------------------------------ NBA
def nba_rows(summary):
    """ESPN summary -> rows shaped like hoopR play-by-play."""
    h = summary["header"]
    comp = h["competitions"][0]
    tm = {t["homeAway"]: t["team"] for t in comp["competitors"]}
    t = et(comp["date"])
    rows = []
    for p in summary.get("plays", []):
        per = int(p.get("period", {}).get("number") or 0)
        c = clock_secs(p.get("clock", {}).get("displayValue", "0:00"))
        secs = c if per >= 5 else (4 - per) * 720 + c
        rows.append({
            "game_id": str(comp["id"]), "season": int(h["season"]["year"]), "season_type": int(h["season"].get("type", 2)),
            "period_number": per, "start_game_seconds_remaining": float(secs), "end_game_seconds_remaining": float(secs),
            "away_score": int(p.get("awayScore") or 0), "home_score": int(p.get("homeScore") or 0),
            "game_date_time": t, "home_team_abbrev": tm["home"]["abbreviation"], "away_team_abbrev": tm["away"]["abbreviation"],
            "home_team_name": tm["home"].get("location"), "home_team_mascot": tm["home"].get("name"),
            "away_team_name": tm["away"].get("location"), "away_team_mascot": tm["away"].get("name"),
        })
    return rows if len(rows) >= 100 else []


def update_us_league(lg, cache):
    """Fetch recently finished NFL/NBA games and keep their play-by-play in the cache."""
    store = cache.setdefault(lg, {})
    today = datetime.now(timezone.utc).astimezone().date()
    new = 0
    for k in range(RECENT_DAYS + 1):
        day = today - timedelta(days=k)
        for ev in scoreboard(lg, day):
            if not completed(ev) or ev["id"] in store:
                continue
            s = get(f"{PATHS[lg]}/summary", f"?event={ev['id']}")
            time.sleep(0.2)
            if not s:
                continue
            try:
                rows = nfl_rows(s) if lg == "nfl" else nba_rows(s)
            except Exception as e:
                print(f"  {lg} {ev['id']}: could not read ({e})")
                continue
            if rows:
                store[ev["id"]] = {"date": day.isoformat(), "rows": rows}
                new += 1
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    for k in [k for k, v in store.items() if v["date"] < cutoff]:
        del store[k]
    rows = [r for v in store.values() for r in v["rows"]]
    path = os.path.join(DATA, f"espn_{lg}.parquet")
    if rows:
        df = pd.DataFrame(rows)
        if lg == "nba":
            df["game_date_time"] = pd.to_datetime(df.game_date_time, utc=True).dt.tz_convert(ET)
        df.to_parquet(path)
    elif os.path.exists(path):
        os.remove(path)
    print(f"{lg.upper()}: {new} new games from ESPN, {len(store)} recent games held")


# ------------------------------------------------------------------ soccer
def soccer_game(ev, lg):
    """One finished match from a scoreboard -> compact record with goal / red card timing."""
    comp = ev["competitions"][0]
    side = {t["homeAway"]: t for t in comp["competitors"]}
    ids = {side["home"]["team"]["id"]: "home", side["away"]["team"]["id"]: "away"}
    events = []
    for d in comp.get("details", []):
        team = ids.get(str(d.get("team", {}).get("id")))
        disp = d.get("clock", {}).get("displayValue", "")
        minute = (d.get("clock", {}).get("value") or 0) / 60
        if d.get("shootout"):
            continue
        if d.get("scoringPlay") and team:
            events.append({"m": round(minute, 1), "t": team, "k": "og" if d.get("ownGoal") else "g", "st": "+" in disp})
        elif d.get("redCard") and team:
            events.append({"m": round(minute, 1), "t": team, "k": "r", "st": "+" in disp})
    t = et(ev["date"])

    def team(s):
        x = s["team"]
        return {"code": x["abbreviation"], "name": x.get("displayName") or x.get("name"),
                "short": x.get("shortDisplayName") or x.get("name"), "color": x.get("color"), "alt": x.get("alternateColor")}
    return {"id": ev["id"], "league": lg, "date": t.strftime("%Y-%m-%d"), "start": t.isoformat(),
            "season": int(ev.get("season", {}).get("year") or 0), "home": team(side["home"]), "away": team(side["away"]),
            "hs": int(side["home"].get("score") or 0), "as": int(side["away"].get("score") or 0), "ev": sorted(events, key=lambda e: e["m"])}


def update_soccer(lg, cache):
    """Day-by-day scoreboards for this season and last. Finished days are cached and never refetched."""
    days = cache.setdefault(f"{lg}_days", {})
    games = cache.setdefault(f"{lg}_games", {})
    today = datetime.now(timezone.utc).astimezone().date()
    fetched = 0
    for start, end in SOCCER_SEASONS.values():
        d, last = date.fromisoformat(start), min(date.fromisoformat(end), today)
        while d <= last:
            key = d.isoformat()
            fresh = d >= today - timedelta(days=2)   # recent days can still change
            if key not in days or fresh:
                evs = scoreboard(lg, d)
                fetched += 1
                fin = [e for e in evs if completed(e)]
                for e in fin:
                    try:
                        games[e["id"]] = soccer_game(e, lg)
                    except Exception as ex:
                        print(f"  {lg} {e.get('id')}: could not read ({ex})")
                if not fresh and len(fin) == len(evs):
                    days[key] = len(fin)
            d += timedelta(days=1)
    print(f"{lg.upper()}: {len(games)} matches held ({fetched} days fetched this run)")


def soccer_features(g):
    """Turn goal timing into the ingredients of a Thrill Score."""
    for flip_own_goals in (False, True):   # ESPN's own-goal credit is checked against the final score
        f = _soccer_features(g, flip_own_goals)
        if f:
            return f
    return None                             # goal list does not match the final score: skip rather than guess


def _soccer_features(g, flip_own_goals):
    h = a = 0
    late = swings = 0.0
    worst = {"home": 0, "away": 0}   # biggest deficit each side faced
    stoppage_drama = 0
    reds = 0
    for e in g["ev"]:
        if e["k"] == "r":
            reds += 1
            continue
        team = e["t"]
        if e["k"] == "og" and flip_own_goals:
            team = "away" if team == "home" else "home"
        before = np.sign(h - a)
        if team == "home":
            h += 1
        else:
            a += 1
        changed = before != np.sign(h - a)
        swings += 1 if changed else 0
        if e["m"] >= 75:
            late += 1.0 if changed else 0.35
            if changed and e["st"]:
                stoppage_drama = 1
        worst["home"] = max(worst["home"], a - h)
        worst["away"] = max(worst["away"], h - a)
    if (h, a) != (g["hs"], g["as"]):
        return None
    margin = h - a
    comeback = 0.0
    for side, sign in (("home", 1), ("away", -1)):
        if worst[side] > 0 and sign * margin >= 0:
            comeback = max(comeback, worst[side] + (0.5 if sign * margin > 0 else 0))
    return {"goals": h + a, "closeness": -abs(margin), "late_drama": late, "comeback": comeback,
            "swings": swings, "reds": min(reds, 2), "stoppage": float(stoppage_drama)}


SOCCER_WEIGHTS = {"late_drama": 0.25, "goals": 0.20, "closeness": 0.15, "swings": 0.15,
                  "comeback": 0.15, "reds": 0.05, "stoppage": 0.05}
LABELS = [(80, "Must watch"), (60, "Worth watching"), (40, "Fans only"), (0, "Skip it")]


def score_soccer(cache):
    """Percentile-blend each match against every match in the same league (this season and last)."""
    out = []
    for lg in ("epl", "laliga"):
        rows = []
        for g in cache.get(f"{lg}_games", {}).values():
            f = soccer_features(g)
            if f:
                rows.append({**g, **f})
        if not rows:
            continue
        df = pd.DataFrame(rows)
        total = 0
        for k, w in SOCCER_WEIGHTS.items():
            pct = df[k] * 100 if k == "stoppage" else df[k].rank(pct=True) * 100
            total = total + w * pct
        df["watch_score"] = total.round(0).clip(0, 100).astype(int)
        # matchweek: each team's nth league game of the season (rearranged games can drift by one)
        df = df.sort_values("start")
        n = {}
        mw = []
        for r in df.itertuples():
            for side in ("home", "away"):
                k = (r.season, getattr(r, side)["code"])
                n[k] = n.get(k, 0) + 1
            mw.append(max(n[(r.season, r.home["code"])], n[(r.season, r.away["code"])]))
        df["matchweek"] = mw
        out += df.to_dict("records")
    return out


def main():
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(RESULTS, exist_ok=True)
    cache = load_cache()
    for lg in ("nfl", "nba"):
        try:
            update_us_league(lg, cache)
        except Exception as e:
            print(f"{lg.upper()}: ESPN update failed ({e}); nflverse/hoopR data still used")
    for lg in ("epl", "laliga"):
        try:
            update_soccer(lg, cache)
        except Exception as e:
            print(f"{lg.upper()}: ESPN update failed ({e})")
    json.dump(cache, open(CACHE, "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
