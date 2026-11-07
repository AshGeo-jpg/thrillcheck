"""
score_nba.py  -  rates how worth-watching each NBA game was, WITHOUT spoilers

Data: free hoopR play-by-play (https://github.com/sportsdataverse/hoopR-nba-data),
one parquet file per season (2027 = the 2026-27 season).

The NBA data has no win probability, so we estimate it after every play from the
score margin and time left, using the standard basketball model:
    P(home wins) = Phi( margin / (SIGMA * sqrt(seconds_left / 2880)) )
SIGMA (about 13 points) is how much a full NBA game's margin typically varies.
In overtime, seconds_left is the time left in the extra period.

Then the same ingredients as the NFL score: total swing, late drama (last 5 minutes
plus overtime), how close the winner came to losing, final closeness, lead changes,
and overtime. Each becomes a percentile versus all NBA games and is blended.
"""
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/pbp/parquet/play_by_play_{}.parquet"
COLS = ["game_id", "season", "season_type", "period_number", "start_game_seconds_remaining",
        "end_game_seconds_remaining", "away_score", "home_score", "game_date_time",
        "home_team_abbrev", "away_team_abbrev", "home_team_name", "home_team_mascot",
        "away_team_name", "away_team_mascot"]
SIGMA = 13.0
WEIGHTS = {"excitement": 0.35, "late_drama": 0.25, "comeback": 0.15,
           "closeness": 0.15, "lead_changes": 0.05, "overtime": 0.05}
LABELS = [(80, "Must watch"), (60, "Worth watching"), (40, "Fans only"), (0, "Skip it")]


def load(season):
    path = os.path.join(HERE, "data", f"nba_pbp_{season}.parquet")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        print("Downloading", RAW.format(season))
        pd.read_parquet(RAW.format(season), columns=COLS).to_parquet(path)
    return pd.read_parquet(path, columns=COLS)


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def win_prob(margin, secs, final=False):
    if final or secs <= 0:
        return 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5
    return phi(margin / (SIGMA * math.sqrt(secs / 2880) + 0.5))


def game_features(g):
    g = g.dropna(subset=["home_score", "away_score"])
    if len(g) < 100 or g.period_number.max() < 4:
        return None
    h, a = int(g.home_score.iloc[-1]), int(g.away_score.iloc[-1])
    if h == a:
        return None  # unfinished
    secs = g.start_game_seconds_remaining.fillna(0).to_numpy()
    per = g.period_number.to_numpy()
    margin = (g.home_score - g.away_score).to_numpy()
    wp = np.array([win_prob(m, s) for m, s in zip(margin, secs)] + [1.0 if h > a else 0.0])
    swings = np.abs(np.diff(wp))
    late = np.append((per >= 5) | ((per == 4) & (secs <= 300)), True)[1:]
    signs = np.sign(margin[margin != 0])
    return {
        "excitement": swings.sum(),
        "late_drama": swings[late].sum(),
        "comeback": 1 - (wp.min() if h > a else (1 - wp).min()),
        "closeness": -abs(h - a),
        "lead_changes": int((np.diff(signs) != 0).sum()) if len(signs) > 1 else 0,
        "overtime": 1.0 if (per >= 5).any() else 0.0,
        "away_final": a, "home_final": h, "ot": bool((per >= 5).any()),
    }


def score(seasons):
    rows = []
    for s in seasons:
        for gid, g in load(s).groupby("game_id"):
            f = game_features(g)
            if f is None:
                continue
            r = g.iloc[0]
            t = pd.Timestamp(r.game_date_time)
            if t.tzinfo is not None:
                t = t.tz_convert("America/New_York")
            rows.append({"game_id": str(gid), "season": int(r.season), "season_type": int(r.season_type),
                         "tip": t, "date": t.strftime("%Y-%m-%d"),
                         "away": r.away_team_abbrev, "home": r.home_team_abbrev,
                         "away_city": r.away_team_name, "away_short": r.away_team_mascot,
                         "home_city": r.home_team_name, "home_short": r.home_team_mascot, **f})
    df = pd.DataFrame(rows)
    total = 0
    for k, w in WEIGHTS.items():
        total = total + w * (df[k].rank(pct=True) * 100 if k != "overtime" else df[k] * 100)
    df["watch_score"] = total.round(0).astype(int)
    df["label"] = df.watch_score.apply(lambda x: next(l for t, l in LABELS if x >= t))
    return df.sort_values("watch_score", ascending=False)


if __name__ == "__main__":
    seasons = [int(x) for x in sys.argv[1:]] or [2026, 2027]
    df = score(seasons)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    df.to_csv(os.path.join(HERE, "results", "nba_scores.csv"), index=False)
    print(df[["date", "away", "home", "away_final", "home_final", "ot", "watch_score", "label"]].head(12).to_string(index=False))
    print(df.label.value_counts().to_dict())
