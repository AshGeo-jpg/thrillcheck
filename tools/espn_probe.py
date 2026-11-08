"""Diagnostic: grab a few real ESPN responses and print them (gzip+base64) to the log."""
import base64, gzip, io, json, tarfile, time, urllib.request
from datetime import date, timedelta

BASE = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUES = {"nfl": "football/nfl", "nba": "basketball/nba", "epl": "soccer/eng.1", "laliga": "soccer/esp.1"}

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

buf = io.BytesIO()
tar = tarfile.open(fileobj=buf, mode="w:gz")
def add(name, data):
    ti = tarfile.TarInfo(name); ti.size = len(data); tar.addfile(ti, io.BytesIO(data))

end = date.today(); start = end - timedelta(days=10)
import urllib.error
for u in ["football/nfl/scoreboard", "football/nfl/scoreboard?dates=20261101", f"football/nfl/scoreboard?dates={start:%Y%m%d}-{end:%Y%m%d}",
          "football/nfl/scoreboard?seasontype=2&week=9", "soccer/eng.1/scoreboard", "soccer/eng.1/scoreboard?dates=20261101",
          "soccer/eng.1/scoreboard?dates=20261025-20261107&limit=100", "basketball/nba/scoreboard?dates=20261105"]:
    try:
        d = get(f"{BASE}/{u}"); print("OK ", u, len(d), len(json.loads(d).get("events", [])))
    except urllib.error.HTTPError as ex:
        print("ERR", u, ex.code, ex.read()[:200])
    except Exception as ex:
        print("ERR", u, ex)
for lg, path in LEAGUES.items():
    url = f"{BASE}/{path}/scoreboard?dates={start:%Y%m%d}-{end:%Y%m%d}&limit=100"
    try:
        sb = get(url); add(f"{lg}_scoreboard.json", sb)
        evs = [e for e in json.loads(sb).get("events", []) if e.get("status", {}).get("type", {}).get("completed")]
        print(lg, "events", len(json.loads(sb).get("events", [])), "completed", len(evs))
        for e in evs[-2:]:
            s = get(f"{BASE}/{path}/summary?event={e['id']}"); add(f"{lg}_summary_{e['id']}.json", s)
            print(" summary", e["id"], e.get("name"), len(s))
            time.sleep(0.5)
    except Exception as ex:
        print(lg, "ERROR", ex)
# a season-range scoreboard to see how far back one call reaches
try:
    sb = get(f"{BASE}/soccer/eng.1/scoreboard?dates=20250801-20260601&limit=1000"); add("epl_season_2025.json", sb)
    print("epl season events", len(json.loads(sb).get("events", [])))
except Exception as ex:
    print("season ERROR", ex)
tar.close()
b = base64.b64encode(buf.getvalue()).decode()
print("PROBE_SIZE", len(b))
for i in range(0, len(b), 8000):
    print("PROBE:" + b[i:i + 8000])
