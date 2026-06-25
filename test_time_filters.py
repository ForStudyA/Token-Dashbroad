"""Test time filter boundary conditions."""
import urllib.request, json

base = "http://127.0.0.1:18765"

def get(path):
    r = urllib.request.urlopen(f"{base}{path}")
    return json.loads(r.read())

for tf in ["all", "today", "7d", "30d"]:
    s = get(f"/api/summary?time={tf}")
    logs = get(f"/api/logs?time={tf}&limit=1")
    print(f"time={tf:5s}: summary={s['requests']:4d} req, logs_total={logs['total']:4d}")

# Test empty filter edge case
s_empty = get("/api/summary?time=today&model=nonexistent_model")
print(f"empty model: req={s_empty['requests']}, hit_rate={s_empty['hit_rate']}%")

# Test empty time range
trends_empty = get("/api/trends?time=today&model=nonexistent_model")
print(f"empty trends: {len(trends_empty)} days")
