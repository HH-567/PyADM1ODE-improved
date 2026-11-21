"""Concurrency probe for the demo server.

Reproduces the deadlock scenario: two /api/traces requests at the same time
while /api/health is polled. Before the fix the process wedged — health stopped
answering and the CPU froze.

Run:  .venv\\Scripts\\python.exe webdemo\\_conc_test.py
"""

import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8848"

BODY = {
    "backend": "scipy",
    "duration": 150,
    "dt": 1.0,
    "temperature_c": 42.0,
    "v_liq": 1050.0,
    "v_gas": 150.0,
    "k_l_a": 200.0,
    "feed": [
        {"substrate_id": "maize_silage_milk_ripeness", "q": 11.4},
        {"substrate_id": "swine_manure", "q": 6.1},
        {"substrate_id": "cattle_manure", "q": 5.0},
    ],
    "temp_step_day": 40,
    "temp_step_delta_c": -5,
    "max_points": 600,
}

results = {}
lock = threading.Lock()


def post(path: str, payload: dict, tag: str, timeout: float = 300.0) -> None:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(  # noqa: S310
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            raw = r.read()
        with lock:
            results[tag] = (r.status, round(time.perf_counter() - t0, 2), len(raw))
    except Exception as exc:  # noqa: BLE001
        with lock:
            results[tag] = ("ERR", round(time.perf_counter() - t0, 2), str(exc)[:80])


def probe_health(tag: str) -> None:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=5) as r:  # noqa: S310
            r.read()
        with lock:
            results[tag] = ("ok", round((time.perf_counter() - t0) * 1000, 1), "ms")
    except Exception as exc:  # noqa: BLE001
        with lock:
            results[tag] = ("HUNG", round((time.perf_counter() - t0) * 1000, 1), str(exc)[:60])


def main() -> int:
    print("firing two concurrent /api/traces ...", flush=True)
    t1 = threading.Thread(target=post, args=("/api/traces", BODY, "trace1"))
    t2 = threading.Thread(target=post, args=("/api/traces", BODY, "trace2"))
    t1.start()
    t2.start()

    for i in range(5):
        time.sleep(1.0)
        probe_health(f"health{i + 1}")

    t1.join(320)
    t2.join(320)

    print("\n-- health probes during the load --")
    for i in range(1, 6):
        print(f"   probe {i}: {results.get(f'health{i}')}")
    print("\n-- trace requests --")
    for tag in ("trace1", "trace2"):
        print(f"   {tag}: {results.get(tag)}")

    ok = all(results.get(f"health{i}", ("",))[0] == "ok" for i in range(1, 6))
    both = all(results.get(t, ("",))[0] == 200 for t in ("trace1", "trace2"))
    print(f"\nhealth stayed responsive: {ok}")
    print(f"both traces completed:    {both}")
    return 0 if (ok and both) else 1


if __name__ == "__main__":
    raise SystemExit(main())
