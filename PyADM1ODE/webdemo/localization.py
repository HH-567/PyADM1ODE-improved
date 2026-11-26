"""中文名与文案：工厂里没人看得懂 "Maize silage (milk ripeness)"。

底物名以 ``id -> (中文名, 说明)`` 维护；数据库里的英文名保留作国际化用，
界面优先显示中文。后端返回的英文状态串也在这里翻译。
"""

from __future__ import annotations

SUBSTRATE_ZH: dict[str, tuple[str, str]] = {
    "maize_silage_milk_ripeness": ("玉米青贮（乳熟期）", "青贮玉米，能量高、纤维适中"),
    "maize_silage_gummersbach": ("玉米青贮（Gummersbach）", "德国实测样，作对照用"),
    "swine_manure": ("猪粪", "氮高，负荷要控"),
    "swine_manure_gummersbach": ("猪粪（Gummersbach）", "德国实测样"),
    "cattle_manure": ("牛粪", "缓冲能力强，产气偏低"),
    "cattle_manure_solid": ("牛粪（干清粪）", "含固率高，需稀释"),
    "chicken_manure_dry": ("鸡粪（烘干）", "氨氮极高，少量掺配"),
    "cereal_gps_silage": ("谷类全株青贮", "易降解，产气快"),
    "grass_silage": ("青草青贮", "蛋白中等，纤维较高"),
    "green_rye_silage": ("黑麦青贮", "春季作物，产气中等"),
    "wheat_whole_plant_silage": ("小麦全株青贮", "纤维高，适合掺配"),
    "corn_cob_mix": ("玉米芯混合料", "难降解，起蓬松作用"),
    "onion_waste": ("洋葱废弃物", "酸化快，少量掺配"),
}


def zh_name(substrate_id: str, fallback: str = "") -> str:
    """Chinese display name for a substrate id."""
    hit = SUBSTRATE_ZH.get(substrate_id)
    return hit[0] if hit else (fallback or substrate_id)


def zh_hint(substrate_id: str) -> str:
    """One-line practical note shown under the name."""
    hit = SUBSTRATE_ZH.get(substrate_id)
    return hit[1] if hit else ""


# 后端返回的英文原因串 -> 中文（前端直接显示，工人不用猜）
STATUS_ZH: dict[str, str] = {
    "Prediction within accuracy threshold": "预测误差在容差内，无需重标定",
    "Disabled": "未启用",
    "Too soon since last calibration": "距上次标定太近",
}


def zh_status(reason: str) -> str:
    """Translate the common trigger reasons, keeping the numbers."""
    text = reason or ""
    for en, zh in STATUS_ZH.items():
        if text.startswith(en):
            return text.replace(en, zh, 1)
    return text
