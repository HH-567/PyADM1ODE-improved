"""底物库：内置底物 + 用户自定义底物。

自定义底物以**内置底物为模板**覆盖字段——``SubstrateParams`` 有 17 个必填项，
逐项从头构造既啰嗦又容易漏；用户只需要填他真正知道的（TS、蛋白、脂肪、纤维…），
其余沿用模板。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from pyadm1.substrates import SubstrateParams, SubstrateRegistry

import localization as l10n

STORE = Path(__file__).with_name("custom_substrates.json")

# 用户可编辑的字段；未填则沿用模板值。
EDITABLE = (
    "TS", "NH4", "BGP", "BMP", "aXI", "fOTSrf", "fsOTS", "ffOTS", "aSi",
    "fRF", "fRP", "fRFe", "fRA", "Temp", "pH", "KS43", "FFS",
)

# 界面上要显示的物理含义，避免工人看不懂代码字段名。
LABELS = {
    "TS": ("总固体", "kg/t FM"),
    "NH4": ("氨氮", "kg N/t FM"),
    "BGP": ("产气潜力", "Nm³/t VS"),
    "BMP": ("甲烷潜力", "Nm³ CH4/t VS"),
    "aXI": ("惰性颗粒占比", "-"),
    "fOTSrf": ("粗纤维可降解比例", "-"),
    "fsOTS": ("进入慢降解池份额", "-"),
    "ffOTS": ("进入快降解池份额", "-"),
    "aSi": ("溶解惰性占比", "-"),
    "fRF": ("粗纤维", "占 TS"),
    "fRP": ("粗蛋白", "占 TS"),
    "fRFe": ("粗脂肪", "占 TS"),
    "fRA": ("灰分", "占 TS"),
    "Temp": ("底物温度", "°C"),
    "pH": ("底物 pH", "-"),
    "KS43": ("丙酸/丁酸半饱和", "kg COD/m³"),
    "FFS": ("可发酵糖占比", "-"),
}

_registry: SubstrateRegistry | None = None


def registry() -> SubstrateRegistry:
    """Shared registry (it scans the substrate directory once)."""
    global _registry
    if _registry is None:
        _registry = SubstrateRegistry()
    return _registry


def _read_store() -> dict[str, dict[str, Any]]:
    if not STORE.exists():
        return {}
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 坏文件不该让页面打不开
        return {}


def _write_store(data: dict[str, dict[str, Any]]) -> None:
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_substrates() -> list[dict[str, Any]]:
    """All substrates: built-ins first, then the user's own."""
    items: list[dict[str, Any]] = []
    for sid in registry().available():
        try:
            p = registry().get(sid)
        except Exception:  # noqa: BLE001
            continue
        items.append(
            {
                "id": sid,
                "name": l10n.zh_name(sid, getattr(p, "name", sid)),
                "name_en": getattr(p, "name", sid),
                "hint": l10n.zh_hint(sid),
                "custom": False,
                "values": {k: float(getattr(p, k, 0.0)) for k in EDITABLE},
            }
        )
    for sid, rec in sorted(_read_store().items()):
        items.append(
            {
                "id": sid,
                "name": rec.get("name", sid),
                "name_en": "",
                "hint": rec.get("note", ""),
                "custom": True,
                "based_on": rec.get("based_on", "maize_silage_milk_ripeness"),
                "note": rec.get("note", ""),
                "values": rec.get("values", {}),
            }
        )
    return items


def make_params(substrate_id: str) -> SubstrateParams:
    """Resolve an id (built-in or custom) into SubstrateParams."""
    store = _read_store()
    if substrate_id in store:
        rec = store[substrate_id]
        base_id = rec.get("based_on") or "maize_silage_milk_ripeness"
        base = registry().get(base_id)
        over = {k: float(v) for k, v in (rec.get("values") or {}).items() if k in EDITABLE}
        over["name"] = rec.get("name", substrate_id)
        return dataclasses.replace(base, **over)
    return registry().get(substrate_id)


def save_custom(payload: dict[str, Any]) -> dict[str, Any]:
    """Create or update a custom substrate. Returns the stored record."""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("底物名称不能为空")

    sid = str(payload.get("id") or "").strip() or _slug(name)
    values: dict[str, float] = {}
    for k in EDITABLE:
        raw = (payload.get("values") or {}).get(k)
        if raw in (None, ""):
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{LABELS.get(k, (k,))[0]} 必须是数字") from exc
        if k in ("fRF", "fRP", "fRFe", "fRA") and not 0 <= v <= 1:
            raise ValueError(f"{LABELS[k][0]} 必须在 0–1 之间（占 TS 的比例）")
        if k == "TS" and not 0 < v <= 1000:
            raise ValueError("总固体 TS 应在 0–1000 kg/t 之间")
        values[k] = v

    base_id = str(payload.get("based_on") or "maize_silage_milk_ripeness")
    if base_id not in registry().available():
        raise ValueError(f"模板底物 {base_id} 不存在")

    store = _read_store()
    store[sid] = {
        "name": name,
        "based_on": base_id,
        "note": str(payload.get("note") or ""),
        "values": values,
    }
    _write_store(store)
    return {"id": sid, **store[sid]}


def delete_custom(substrate_id: str) -> bool:
    """Remove a custom substrate; built-ins are untouched."""
    store = _read_store()
    if substrate_id not in store:
        return False
    store.pop(substrate_id)
    _write_store(store)
    return True


def _slug(name: str) -> str:
    out = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_").lower()
    return f"custom_{out or 'substrate'}"[:48]


def zh_name(substrate_id: str) -> str:
    """Chinese display name (falls back to the id)."""
    return l10n.zh_name(substrate_id)
