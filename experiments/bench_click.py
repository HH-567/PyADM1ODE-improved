"""Measure click-to-result latency for each backend, end to end over HTTP.

Times are what a user actually waits: POST /api/simulate (or /api/compare)
from the moment the request is sent until the JSON body has arrived.
"""

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8848"

FEED = [
    {"substrate_id": "maize_silage_milk_ripeness", "q": 11.4},
    {"substrate_id": "swine_manure", "q": 6.1},
    {"substrate_id": "cattle_manure", "q": 5.0},
]


def body(backend: str, duration: float = 150.0) -> dict:
    return {
        "backend": backend,
        "duration": duration,
        "dt": 1.0,
        "temperature_c": 42.0,
        "v_liq": 1050.0,
        "v_gas": 150.0,
        "k_l_a": 200.0,
        "feed": FEED,
    }


def post(path: str, payload: dict, timeout: float = 600.0) -> tuple[int, float, int]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return r.status, time.perf_counter() - t0, len(raw)


def wait_health(limit: float = 60.0) -> float:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < limit:
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                r.read()
            return time.perf_counter() - t0
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    raise SystemExit("server never became healthy")


def main() -> int:
    t_up = wait_health()
    print(f"服务可用: {t_up:.1f}s")

    # 1) 立刻点：Julia 还在后台预热
    s, dt_cold, n = post("/api/simulate", body("adm1jl"))
    print(
        f"① 启动后立刻点（adm1jl, 150d）: {dt_cold:5.2f}s  status={s}  {n/1024:.0f} KB"
    )

    # 2) 原版 scipy
    s, dt_scipy, n = post("/api/simulate", body("scipy"))
    print(
        f"② 原版 scipy（150d）           : {dt_scipy:5.2f}s  status={s}  {n/1024:.0f} KB"
    )

    # 3) Julia 热态
    s, dt_warm, n = post("/api/simulate", body("adm1jl"))
    print(
        f"③ adm1jl 热态（150d）          : {dt_warm:5.2f}s  status={s}  {n/1024:.0f} KB"
    )

    # 4) 对比按钮（两个求解器 + 预热）
    s, dt_cmp, n = post("/api/compare", body("scipy") | {"duration": 150.0})
    print(
        f"④ 两求解器对比（热态）         : {dt_cmp:5.2f}s  status={s}  {n/1024:.0f} KB"
    )

    # 5) 短仿真（30 天），模拟常见交互
    s, dt_short_jl, _ = post("/api/simulate", body("adm1jl", 30.0))
    s, dt_short_scipy, _ = post("/api/simulate", body("scipy", 30.0))
    print(f"⑤ 30 天：adm1jl 热 {dt_short_jl:.2f}s  /  scipy {dt_short_scipy:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
