"""Diagnostic: grab a few real ESPN responses and print them (gzip+base64) to the log."""
import base64, io, json, tarfile, time, urllib.request, urllib.error
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

today = date.today()
for lg, path in LEAGUES.items():
    done = []
    for k in range(1, 8):
        d = today - timedelta(days=k)
        try:
            sb = get(f"{BASE}/{path}/scoreboard?dates={d:%Y%m%d}")
        except Exception as ex:
            print(lg, d, "ERR", ex); continue
        evs = json.loads(sb).get("events", [])
        fin = [e for e in evs if e.get("status", {}).get("type", {}).get("completed")]
        print(lg, d, "events", len(evs), "completed", len(fin))
        if fin and not done:
            add(f"{lg}_scoreboard_{d:%Y%m%d}.json", sb)
        done += fin
        time.sleep(0.3)
    for e in done[:2]:
        try:
            s = get(f"{BASE}/{path}/summary?event={e['id']}"); add(f"{lg}_summary_{e['id']}.json", s)
            print(" summary", e["id"], e.get("name"), len(s))
        except Exception as ex:
            print(" summary ERR", e["id"], ex)
        time.sleep(0.3)
for u, name in [("soccer/eng.1/teams", "epl_teams.json"), ("soccer/eng.1/teams/359/schedule?season=2025", "epl_team359_2025.json"),
                ("soccer/eng.1/teams/359/schedule", "epl_team359_now.json"), ("soccer/esp.1/teams", "laliga_teams.json")]:
    try:
        d = get(f"{BASE}/{u}"); add(name, d); print("OK", u, len(d))
    except urllib.error.HTTPError as ex:
        print("ERR", u, ex.code)
tar.close()
b = base64.b64encode(buf.getvalue()).decode()
print("PROBE_SIZE", len(b))
for i in range(0, len(b), 8000):
    print("PROBE:" + b[i:i + 8000])
