"""Test API endpoints."""
import urllib.request, json

base = "http://127.0.0.1:18765"

def get(path):
    r = urllib.request.urlopen(f"{base}{path}")
    return json.loads(r.read())

try:
    # Test summary
    s = get("/api/summary")
    print(f"summary: {s['requests']} req, cost={s['cost']}, hit_rate={s['hit_rate']}%")

    # Test stats
    stats = get("/api/stats?time=all")
    print(f"stats: {len(stats)} groups")

    # Test trends
    trends = get("/api/trends?time=30d")
    print(f"trends: {len(trends)} days")

    # Test logs
    logs = get("/api/logs?page=1&limit=3")
    print(f"logs: {logs['total']} total, {len(logs['items'])} items")

    # Test providers
    providers = get("/api/providers")
    print(f"providers: {len(providers)} providers")

    # Test models
    models = get("/api/models")
    print(f"models: {models['total']} total, {len(models['models'])} unique")

    print("\nAll API endpoints working correctly.")
except Exception as e:
    print(f"ERROR: {e}")
