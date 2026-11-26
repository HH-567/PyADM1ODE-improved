"""异常诊断：从传感器读数与运行曲线判断"现在正常吗、可能是什么问题、怎么办"。

规则来自沼气工程的常见故障模式，每条都带**证据**（用了哪几个数）和
**可执行动作**。这里不做"AI 黑箱"：每一条判断都能被现场核对，误判了也知道该改哪条阈值。

特别区分两类异常：
* 过程异常（酸化、氨抑制、产气骤降…）——罐子真的出问题；
* 数据异常（读数与模型严重不符、丢数、偏置过大）——传感器或通信出问题，
  这时候按过程异常去调料反而可能把事情搞坏。
"""

from __future__ import annotations

from typing import Any

PH_ACID = 6.8
PH_LOW = 7.0
PH_HIGH = 7.6
VFA_TAC_WARN = 0.30
VFA_TAC_ACID = 0.40
GAS_DROP = 0.15          # 较近 7 天均值下降
CH4_DROP_POINTS = 3.0    # 甲烷占比下降百分点
NIS_BAD = 4.0            # 新息一致性指标：>4 说明模型与读数严重不符


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _mean(xs: list[float]) -> float | None:
    vals = [v for v in xs if v is not None and v == v]
    return sum(vals) / len(vals) if vals else None


# 范例：给演示用的合成快照。它们不来自真实运行，界面上必须标明"合成数据"，
# 否则就成了拿假数据充现场证据。
DEMO_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "acid",
        "label": "酸化",
        "hint": "进料过猛 / 温度骤降：产酸菌跑赢产甲烷菌",
        "snapshot": {
            "latest": {"ph": 6.62, "vfa_tac": 0.55, "q_gas": 1780, "ch4_pct": 43.5, "olr": 5.6},
            "series": {"q_gas": [2300] * 7 + [1780], "ch4_pct": [49] * 7 + [43.5]},
            "hourly": {"err": [0.18], "nis": [0.9]},
            "feed": {"maize": 16.0, "swine": 9.0},
        },
    },
    {
        "id": "ammonia",
        "label": "氨抑制",
        "hint": "高氮原料（鸡粪、粪水）加太多",
        "snapshot": {
            "latest": {"ph": 7.82, "vfa_tac": 0.22, "q_gas": 1520, "ch4_pct": 46.0, "olr": 4.1},
            "series": {"q_gas": [2150] * 7 + [1520], "ch4_pct": [50] * 7 + [46.0]},
            "hourly": {"err": [0.12], "nis": [0.8]},
            "feed": {"chicken": 6.0, "swine": 8.0},
        },
    },
    {
        "id": "gasdrop",
        "label": "产气骤降（搅拌/短路）",
        "hint": "pH 正常但气量掉了一大截，多半不是酸化",
        "snapshot": {
            "latest": {"ph": 7.32, "vfa_tac": 0.24, "q_gas": 1400, "ch4_pct": 49.5, "olr": 4.0},
            "series": {"q_gas": [2150] * 7 + [1400], "ch4_pct": [50] * 7 + [49.5]},
            "hourly": {"err": [0.11], "nis": [0.7]},
            "feed": {"maize": 11.0, "swine": 6.0},
        },
    },
    {
        "id": "sensor",
        "label": "读数可疑（先查表）",
        "hint": "工艺看着正常，但读数与模型严重不符",
        "snapshot": {
            "latest": {"ph": 7.38, "vfa_tac": 0.26, "q_gas": 2100, "ch4_pct": 49.0, "olr": 4.0},
            "series": {"q_gas": [2120] * 8, "ch4_pct": [49] * 8},
            "hourly": {"err": [0.34], "nis": [6.8]},
            "feed": {"maize": 11.0, "swine": 6.0},
        },
    },
]


def list_demo() -> list[dict[str, Any]]:
    """Run every canned scenario through the same rules as the live path."""
    out = []
    for sc in DEMO_SCENARIOS:
        res = diagnose(sc["snapshot"])
        out.append({"id": sc["id"], "label": sc["label"], "hint": sc["hint"], "result": res})
    return out


def diagnose(snap: dict[str, Any]) -> dict[str, Any]:
    """Return ``{level, headline, causes, evidence}`` for one session snapshot."""
    latest = snap.get("latest") or {}
    series = snap.get("series") or {}
    hourly = snap.get("hourly") or {}

    ph = _f(latest.get("ph"))
    ratio = _f(latest.get("vfa_tac"))
    gas = _f(latest.get("q_gas"))
    ch4_pct = _f(latest.get("ch4_pct"))
    olr = _f(latest.get("olr"))

    gas_hist = [(_f(v) or 0.0) for v in (series.get("q_gas") or [])]
    ch4_hist = [(_f(v) or 0.0) for v in (series.get("ch4_pct") or [])]
    gas_base = _mean(gas_hist[-8:-1]) if len(gas_hist) >= 8 else None
    ch4_base = _mean(ch4_hist[-8:-1]) if len(ch4_hist) >= 8 else None
    nis = _f((hourly.get("nis") or [None])[-1]) if hourly.get("nis") else None
    hour_err = _f(hourly.get("err", [None])[-1]) if hourly.get("err") else None

    causes: list[dict[str, Any]] = []

    def add(cause: str, likelihood: str, evidence: str, now: str, later: str) -> None:
        causes.append(
            {
                "cause": cause,
                "likelihood": likelihood,   # 高 / 中 / 低
                "evidence": evidence,
                "now": now,                 # 立即动作
                "later": later,             # 24 小时内
            }
        )

    # ---- 过程：酸化（最常见、最危险） ----------------------------------
    if ph is not None and ratio is not None and (ph < PH_ACID or ratio > VFA_TAC_ACID):
        add(
            "酸化（产酸菌跑在产甲烷菌前面）",
            "高",
            f"pH {ph:.2f}、VFA/TAC {ratio:.2f}（阈值 {PH_ACID} / {VFA_TAC_ACID}）",
            "立即停加料 1–2 天；若 pH < 6.6 投加石灰乳或碳酸氢钠调碱",
            "恢复时按原负荷的 60% 逐步加回；核查是否进了高淀粉/高糖原料",
        )
    elif ph is not None and ratio is not None and (ph < PH_LOW or ratio > VFA_TAC_WARN):
        add(
            "酸化预警",
            "中",
            f"pH {ph:.2f}、VFA/TAC {ratio:.2f}（预警线 {PH_LOW} / {VFA_TAC_WARN}）",
            "今天不要再加料，观察 24 小时 pH 与 VFA/TAC 走向",
            "把总负荷降 10–15%，确认搅拌与加热正常",
        )

    # ---- 过程：氨抑制 --------------------------------------------------
    if ph is not None and ph > PH_HIGH:
        evidence = f"pH {ph:.2f} 高于 {PH_HIGH}"
        if gas_base and gas and gas < gas_base * 0.9:
            evidence += f"，且产气较 7 天均值下降 {(1 - gas / gas_base) * 100:.0f}%"
        add(
            "氨抑制（高蛋白/高氮原料过量）",
            "高" if gas_base and gas and gas < gas_base * 0.85 else "中",
            evidence,
            "停止或大幅削减鸡粪、粪水等高氮原料；有条件就出水稀释",
            "补加碳源（如糖蜜）平衡 C/N；监测氨氮，目标 < 3 g/L",
        )

    # ---- 过程：产气骤降 -------------------------------------------------
    if gas_base and gas and gas < gas_base * (1 - GAS_DROP):
        drop = (1 - gas / gas_base) * 100
        if ph is not None and ph < PH_ACID:
            cause = "毒性物质或负荷冲击（同时伴随酸化）"
            now = "立即停止进料，排查近期是否混入消毒剂、抗生素、高盐废水"
        elif ph is not None and 6.8 <= ph <= 7.6:
            cause = "进料中断、搅拌故障或短路流（pH 正常但气量掉了）"
            now = "先查搅拌与进料泵，再看罐内是否结壳、是否有温度分层"
        else:
            cause = "产气骤降"
            now = "核对仪表与流量计，确认不是读数问题"
        add(
            cause,
            "高" if drop > 25 else "中",
            f"产气 {gas:.0f} m³/d，较近 7 天均值 {gas_base:.0f} 下降 {drop:.0f}%",
            now,
            "记录异常前后 24 小时的进料明细，等 1–2 天看是否自恢复",
        )

    # ---- 过程：甲烷占比下降 ---------------------------------------------
    if ch4_base and ch4_pct and ch4_pct < ch4_base - CH4_DROP_POINTS:
        add(
            "甲烷占比下降（产甲烷菌受抑或气路漏气）",
            "中",
            f"CH₄ {ch4_pct:.1f}%，较近 7 天均值 {ch4_base:.1f}% 低 {ch4_base - ch4_pct:.1f} 个百分点",
            "先查气路与取样管路气密性，排除漏气稀释",
            "测氢气与氨氮；若氢分压升高说明丙酸/丁酸转化受阻",
        )

    # ---- 数据：读数与模型严重不符 ---------------------------------------
    if nis is not None and nis > NIS_BAD:
        add(
            "读数与模型不符（先怀疑仪表，不要急着调工艺）",
            "高",
            f"新息一致性 NIS {nis:.1f} > {NIS_BAD}；状态中位误差 {(hour_err or 0) * 100:.0f}%",
            "核对产气流量计与 pH 探头：是否结垢、是否标定过期、是否取样管堵塞",
            "若仪表正常，则可能是进料组分突变，按进料记录复核",
        )

    # ---- 数据：进料为零 -----------------------------------------------
    feed = snap.get("feed") or {}
    if feed and all((_f(v) or 0) <= 0 for v in feed.values()):
        add(
            "当前没有进料",
            "中",
            "所有原料的当日投加量为 0",
            "确认是否刻意停料；若不是，检查进料泵与称重",
            "恢复进料时从原负荷的 60% 起，避免一次加满",
        )

    # ---- 汇聚 ----------------------------------------------------------
    if not causes:
        level = "normal"
        headline = "运行正常"
        detail = "pH、VFA/TAC、产气与负荷都在常规区间内。"
    else:
        level = "serious" if any(c["likelihood"] == "高" for c in causes) else "warn"
        headline = causes[0]["cause"] if level == "serious" else f"{causes[0]['cause']}（需关注）"
        detail = f"检出 {len(causes)} 条可疑原因，按可能性排序如下。"

    return {
        "ok": True,
        "level": level,
        "headline": headline,
        "detail": detail,
        "causes": causes[:4],
        "evidence": {
            "pH": ph,
            "VFA/TAC": ratio,
            "产气 m³/d": gas,
            "近7天产气均值": round(gas_base, 1) if gas_base else None,
            "CH₄ %": ch4_pct,
            "OLR": olr,
            "NIS": nis,
        },
        "note": "判断基于规则与当前读数，用于提示排查方向；最终处置请结合现场巡检。",
    }
