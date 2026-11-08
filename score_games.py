"""
score_games.py  -  rates how worth-watching each NFL game was, WITHOUT spoilers

Uses free nflverse play-by-play data (every play, with live win probability).
For each finished game it measures:
  excitement   total swing in win probability over the game (the classic
               "excitement index": a game that see-saws scores high)
  late_drama   win-probability swing in the last 5 minutes + overtime
  comeback     how close the eventual winner came to losing
  closeness    how close the final margin was
  lead_changes how many times the lead flipped
  overtime     whether it went to OT
Each piece becomes a 0-100 percentile versus all games, then they're blended
into one Thrill Score with a spoiler-free label.

Usage:  python score_games.py 2025            (one season)
        python score_games.py 2025 2026       (several)
Data:   https://github.com/nflverse/nflverse-data/releases/tag/pbp
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
COLS = ["game_id", "game_date", "week", "season", "season_type", "home_team", "away_team",
        "qtr", "game_seconds_remaining", "home_wp", "total_home_score", "total_away_score"]

WEIGHTS = {"excitement": 0.35, "late_drama": 0.25, "comeback": 0.15,
           "closeness": 0.15, "lead_changes": 0.05, "overtime": 0.05}

LABELS = [(80, "Must watch"), (60, "Worth watching"), (40, "Fans only"), (0, "Skip it")]


def load(season, cols=COLS):
    """nflverse play-by-play, plus recent games from ESPN (espn.py) that nflverse hasn't posted yet."""
    path = os.path.join(HERE, "data", f"pbp_{season}.parquet")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        url = f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
        print(f"Downloading {url}")
        try:
            pd.read_parquet(url).to_parquet(path)
        except Exception as e:
            print(f"  could not download nflverse {season}: {e}")
    frames = []
    if os.path.exists(path):
        frames.append(pd.read_parquet(path, columns=cols))
    espn = os.path.join(HERE, "data", "espn_nfl.parquet")
    if os.path.exists(espn):
        e = pd.read_parquet(espn)
        e = e[(e.season == season) & ~e.game_id.isin(frames[0].game_id.unique() if frames else [])]
        if len(e):
            frames.append(e[cols])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)


def game_features(g):
    g = g.dropna(subset=["home_wp"]).sort_values("game_seconds_remaining", ascending=False, kind="stable")
    wp = g.home_wp.clip(0, 1).to_numpy()
    if len(wp) < 20:
        return None
    swings = np.abs(np.diff(wp))
    secs = g.game_seconds_remaining.to_numpy()[1:]
    ot = bool((g.qtr >= 5).any())
    late = (secs <= 300) | (g.qtr.to_numpy()[1:] >= 5)

    h, a = g.total_home_score.iloc[-1], g.total_away_score.iloc[-1]
    diff = (g.total_home_score - g.total_away_score).to_numpy()
    signs = np.sign(diff[diff != 0])
    lead_changes = int((np.diff(signs) != 0).sum()) if len(signs) > 1 else 0

    if h > a:
        winner_min_wp = wp.min()
    elif a > h:
        winner_min_wp = (1 - wp).min()
    else:
        winner_min_wp = 0.5   # tie

    return {
        "excitement": swings.sum(),
        "late_drama": swings[late].sum(),
        "comeback": 1 - winner_min_wp,
        "closeness": -abs(h - a),
        "lead_changes": lead_changes,
        "overtime": 1.0 if ot else 0.0,
    }


def score(seasons):
    rows = []
    for s in seasons:
        pbp = load(s)
        for gid, g in pbp.groupby("game_id"):
            f = game_features(g)
            if f is None:
                continue
            first = g.iloc[0]
            rows.append({"game_id": gid, "season": int(first.season), "week": int(first.week),
                         "type": first.season_type, "date": first.game_date,
                         "away": first.away_team, "home": first.home_team,
                         "_final": f"{first.away_team} {int(g.total_away_score.max())} - "
                                   f"{first.home_team} {int(g.total_home_score.max())}", **f})
    df = pd.DataFrame(rows)

    # Each feature -> percentile rank 0..100 across all games, then blend.
    total = 0
    for k, w in WEIGHTS.items():
        pct = df[k].rank(pct=True) * 100 if k != "overtime" else df[k] * 100
        total = total + w * pct
    df["watch_score"] = total.round(0).astype(int)
    df["label"] = df.watch_score.apply(lambda x: next(l for t, l in LABELS if x >= t))
    return df.sort_values("watch_score", ascending=False)


if __name__ == "__main__":
    seasons = [int(x) for x in sys.argv[1:]] or [2025]
    df = score(seasons)
    out = os.path.join(HERE, "results")
    os.makedirs(out, exist_ok=True)
    df.to_csv(os.path.join(out, "watch_scores.csv"), index=False)
    show = ["season", "week", "away", "home", "watch_score", "label"]
    print(df[show].head(15).to_string(index=False))
    print("...")
    print(df[show].tail(5).to_string(index=False))
    print("\nLabel counts:", df.label.value_counts().to_dict())
