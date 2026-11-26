"""越限告警：工厂最关心的是"现在还能不能继续加料"，而不是曲线好不好看。

每条告警给出：级别（严重 / 警告）、指标、当前值、限值、建议动作。
判据取自沼气工程的常规操作区间，可按厂调整；这里集中一处，方便校准。
"""

from __future__ import annotations

from typing import Any

# 常规操作区间（中温湿式 CSTR 经验值）
PH_MIN = 6.8
PH_WARN = 7.0
PH_MAX = 7.6
VFA_TAC_WARN = 0.30
VFA_TAC_MAX = 0.40
OLR_MAX = 5.0
OLR_WARN = 4.5
HRT_MIN = 20.0
GAS_DROP_WARN = 0.15  # 较前 7 天均值下降 15%


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN -> None


def evaluate(latest: dict[str, Any], series: dict[str, list[float]] | None = None) -> list[dict[str, Any]]:
    """Return the alerts for one snapshot, most severe first."""
    out: list[dict[str, Any]] = []

    ph = _num(latest.get("ph"))
    if ph is not None:
        if ph < PH_MIN:
            out.append({"level": "serious", "metric": "pH", "value": round(ph, 2),
                        "text": f"pH {ph:.2f} 低于 {PH_MIN}",
                        "advice": "立即减少进料 20–30%，必要时投加石灰调碱，暂停高酸原料"})
        elif ph < PH_WARN:
            out.append({"level": "warn", "metric": "pH", "value": round(ph, 2),
                        "text": f"pH {ph:.2f} 偏低",
                        "advice": "降低负荷并观察产气，避免继续加高蛋白料"})
        elif ph > PH_MAX:
            out.append({"level": "warn", "metric": "pH", "value": round(ph, 2),
                        "text": f"pH {ph:.2f} 偏高",
                        "advice": "警惕氨抑制，减少鸡粪等高氮料"})

    ratio = _num(latest.get("vfa_tac"))
    if ratio is not None:
        if ratio > VFA_TAC_MAX:
            out.append({"level": "serious", "metric": "VFA/TAC", "value": round(ratio, 3),
                        "text": f"VFA/TAC {ratio:.2f} 超过 {VFA_TAC_MAX}",
                        "advice": "酸化风险：停止增加负荷，检查搅拌与温度，考虑出料稀释"})
        elif ratio > VFA_TAC_WARN:
            out.append({"level": "warn", "metric": "VFA/TAC", "value": round(ratio, 3),
                        "text": f"VFA/TAC {ratio:.2f} 偏高",
                        "advice": "暂缓加料 1–2 天，观察比值是否回落"})

    olr = _num(latest.get("olr"))
    if olr is not None:
        if olr > OLR_MAX:
            out.append({"level": "serious", "metric": "OLR", "value": round(olr, 2),
                        "text": f"有机负荷 {olr:.2f} 超过 {OLR_MAX} kg VS/(m³·d)",
                        "advice": "超负荷：按总料量下调 15%，或延长停留时间"})
        elif olr > OLR_WARN:
            out.append({"level": "warn", "metric": "OLR", "value": round(olr, 2),
                        "text": f"有机负荷 {olr:.2f} 接近上限 {OLR_MAX}",
                        "advice": "还有约 10% 余量，再加料前先确认 pH 与 VFA/TAC 稳定"})

    hrt = _num(latest.get("hrt"))
    if hrt is not None and hrt < HRT_MIN:
        out.append({"level": "warn", "metric": "HRT", "value": round(hrt, 1),
                    "text": f"停留时间 {hrt:.1f} d 短于 {HRT_MIN} d",
                    "advice": "产甲烷菌可能被冲走，降低进料量或提高罐容利用率"})

    # 产气异常：与最近 7 天均值比
    if series:
        gas = [g for g in series.get("q_gas", []) if _num(g) is not None]
        if len(gas) >= 8:
            recent = _num(gas[-1])
            base = sum(float(g) for g in gas[-8:-1]) / 7.0
            if recent is not None and base > 0 and (recent - base) / base < -GAS_DROP_WARN:
                out.append({"level": "warn", "metric": "产气", "value": round(recent, 0),
                            "text": f"产气 {recent:.0f} m³/d，比前 7 天均值低 {abs((recent - base) / base) * 100:.0f}%",
                            "advice": "先查仪表与搅拌，再查是否有毒性物质进入"})

    order = {"serious": 0, "warn": 1}
    out.sort(key=lambda a: order.get(a["level"], 2))
    return out


def summary(alerts: list[dict[str, Any]]) -> str:
    """One line for the status bar."""
    if not alerts:
        return "运行正常"
    serious = sum(1 for a in alerts if a["level"] == "serious")
    if serious:
        return f"{serious} 项严重告警，需立即处理"
    return f"{len(alerts)} 项提醒"
