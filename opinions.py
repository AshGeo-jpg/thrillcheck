"""
opinions.py  -  adds "what people are saying" to each game's Thrill Score

For each game:
  1. Searches the web (Brave Search API) for recaps and fan reactions.
  2. Sends the search snippets to Claude, which returns a spoiler-free
     "buzz" rating (0-100) and a one-line blurb.
  3. A spoiler guard rejects any blurb that leaks a score or a winner.
  4. Final score = 75% game data + 25% buzz.

Cost (Nov 2026 list prices): ~2 searches at $5 per 1,000 = $0.01 per game,
plus a few thousand Claude Haiku tokens = well under $0.01 per game.
A full NFL season (~285 games) costs roughly $3 to $5.

Setup:
  pip install anthropic requests pandas
  export BRAVE_API_KEY=...       (api-dashboard.search.brave.com)
  export ANTHROPIC_API_KEY=...   (console.anthropic.com)

Usage:
  python opinions.py              # adds buzz to games in results/watch_scores.csv
  python opinions.py --limit 10   # only the 10 most recent games (cheap test)
"""
import argparse
import json
import os
import re
import time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SCORES = os.path.join(HERE, "results", "watch_scores.csv")
CACHE = os.path.join(HERE, "results", "buzz_cache.json")
MODEL = "claude-haiku-5-5"
DATA_WEIGHT, BUZZ_WEIGHT = 0.75, 0.25

TEAM_NAMES = {
    "ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills", "CAR": "Panthers",
    "CHI": "Bears", "CIN": "Bengals", "CLE": "Browns", "DAL": "Cowboys", "DEN": "Broncos",
    "DET": "Lions", "GB": "Packers", "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars",
    "KC": "Chiefs", "LA": "Rams", "LAC": "Chargers", "LV": "Raiders", "MIA": "Dolphins",
    "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants", "NYJ": "Jets",
    "PHI": "Eagles", "PIT": "Steelers", "SEA": "Seahawks", "SF": "49ers", "TB": "Buccaneers",
    "TEN": "Titans", "WAS": "Commanders",
}

PROMPT = """You rate how worth-watching a finished {league} game was, for fans who have NOT seen it.
Below are web search snippets about the {away} at {home} game on {date}.

Judge the buzz: did people call it a classic, wild, thrilling, historic, or boring and sloppy?
Count big moments (records, huge plays, comebacks) and ignore betting and fantasy chatter.

Return ONLY JSON:
{{"buzz": <0-100, how strongly people say it was worth watching>,
  "blurb": "<one sentence, max 20 words, makes a fan want to watch or skip>",
  "tags": []}}

STRICT SPOILER RULES for the blurb: never mention the score, the winner, the loser, who led,
how it ended, or anything that hints at the result. That includes how much scoring there was
("shootout", "offenses came to play", "defensive battle"), comebacks, overtime, blowouts,
late drama, records or individual stat lines. Only say how worth watching it is, in general terms.

Snippets:
{snippets}"""

SPOILER_PATTERNS = [r"\d+\s*[-–]\s*\d+", r"\b(won|wins|win|beat|beats|defeat|lost|loses|loss|"
                    r"victory|upset|walk-?off|game-?winner|winning|comeback|overtime|shootout|"
                    r"blowout|offens\w*|defens\w*|record|touchdowns?|yards|points?)\b"]


def search(query, key):
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                     headers={"X-Subscription-Token": key, "Accept": "application/json"},
                     params={"q": query, "count": 10}, timeout=20)
    r.raise_for_status()
    return [f"{x.get('title', '')}: {x.get('description', '')}"
            for x in r.json().get("web", {}).get("results", [])]


def spoiler_free(text):
    return not any(re.search(p, text, re.I) for p in SPOILER_PATTERNS)


def rate_game(row, brave_key, client, league="nfl"):
    if league == "nba":
        away, home = f"{row.away_city} {row.away_short}", f"{row.home_city} {row.home_short}"
    else:
        away, home = TEAM_NAMES[row.away], TEAM_NAMES[row.home]
    year = str(row.date)[:4]
    snippets = []
    for q in (f"{away} vs {home} {row.date} recap", f"{away} {home} game {year} fans reaction"):
        snippets += search(q, brave_key)
        time.sleep(1.1)  # stay under the rate limit
    if not snippets:
        return None
    msg = client.messages.create(
        model=MODEL, max_tokens=300,
        messages=[{"role": "user", "content": PROMPT.format(
            league=league.upper(), away=away, home=home, date=row.date, snippets="\n".join(snippets[:20]))}])
    text = msg.content[0].text
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    if not spoiler_free(data.get("blurb", "")):
        data["blurb"] = ""          # drop a leaky blurb rather than spoil the game
    data["tags"] = []  # tags come only from pre-game facts in build_site.py
    return data


def main():
    import anthropic
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--league", choices=["nfl", "nba"], default="nfl")
    a = ap.parse_args()

    scores = SCORES if a.league == "nfl" else os.path.join(HERE, "results", "nba_scores.csv")
    df = pd.read_csv(scores).sort_values("date", ascending=False)
    if a.league == "nba":
        df["game_id"] = ("nba-" + df.date + "-" + df.away + "-" + df.home).str.lower()
    if a.limit:
        df = df.head(a.limit)
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    client = anthropic.Anthropic()
    brave_key = os.environ["BRAVE_API_KEY"]

    for row in df.itertuples():
        if row.game_id in cache:
            continue
        try:
            cache[row.game_id] = rate_game(row, brave_key, client, a.league)
            print(row.game_id, cache[row.game_id])
        except Exception as e:
            print(row.game_id, "skipped:", e)
        json.dump(cache, open(CACHE, "w"), indent=1)

    if a.league != "nfl":
        print("Saved NBA buzz to the cache. build_site.py applies it.")
        return
    full = pd.read_csv(SCORES)
    full["buzz"] = full.game_id.map(lambda g: (cache.get(g) or {}).get("buzz"))
    full["blurb"] = full.game_id.map(lambda g: (cache.get(g) or {}).get("blurb", ""))
    full["tags"] = full.game_id.map(lambda g: ", ".join((cache.get(g) or {}).get("tags", [])))
    has = full.buzz.notna()
    full.loc[has, "watch_score"] = (DATA_WEIGHT * full.loc[has, "watch_score"]
                                    + BUZZ_WEIGHT * full.loc[has, "buzz"]).round(0)
    full.to_csv(os.path.join(HERE, "results", "watch_scores_with_buzz.csv"), index=False)
    print(f"Done. {has.sum()} games now include web buzz.")


if __name__ == "__main__":
    main()
