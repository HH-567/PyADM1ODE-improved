"""出图：单因素排序 + 优先组/对照组误差对照（计划书要求的两张图）"""

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).with_name("results") / "out"
CJK = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["font.sans-serif"] = CJK
plt.rcParams["axes.unicode_minus"] = False

# ---- 数据 ---------------------------------------------------------------
sens = list(csv.DictReader((HERE / "sensitivity.csv").open(encoding="utf-8")))
combos = list(csv.DictReader((HERE / "combinations.csv").open(encoding="utf-8")))
summary = json.loads((HERE / "summary.json").read_text(encoding="utf-8"))

sens.sort(key=lambda r: -float(r["abs_S"]))
good = [c for c in combos if c["valid"] == "True"]

# ---- 图1：单因素排序 ----------------------------------------------------
fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=140)
names = [r["param"] for r in sens][::-1]
vals = [float(r["abs_S"]) for r in sens][::-1]
colors = ["#34d3c8" if n in summary["priority"] else "#7c8ea8" for n in names]
ax.barh(names, vals, color=colors, height=0.62)
ax.set_xlabel("范围归一化敏感度 |S|")
ax.set_title("单因素敏感度排序（青色 = 优先组 top 25%）", fontsize=11)
for i, v in enumerate(vals):
    ax.text(v + max(vals) * 0.015, i, f"{v:.3f}", va="center", fontsize=7.5, color="#3a4a5e")
ax.set_xlim(0, max(vals) * 1.18)
ax.grid(axis="x", alpha=0.25)
fig.tight_layout()
fig.savefig(HERE / "fig_sensitivity.png")

# ---- 图2：误差对照 ------------------------------------------------------
top = [float(c["err_top"]) for c in good]
ctrl = [float(c["err_ctrl"]) for c in good]
fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.6, 4.0), dpi=140,
                             gridspec_kw={"width_ratios": [1.25, 1]})
for i, (t, c) in enumerate(zip(top, ctrl)):
    a1.plot([0, 1], [t, c], color="#b9c4d2", lw=0.8, zorder=1)
a1.scatter([0] * len(top), top, s=22, color="#34d3c8", label="仅优先组 (S_top)", zorder=2)
a1.scatter([1] * len(ctrl), ctrl, s=22, color="#f2b344", label="仅对照组 (S_ctrl)", zorder=2)
a1.set_xticks([0, 1])
a1.set_xticklabels(["S_top", "S_ctrl"])
a1.set_ylabel("相对 S_all 的绝对误差 [m³ CH4/d]")
a1.set_title(f"逐组配对误差（{len(good)} 组）", fontsize=10)
a1.legend(frameon=False, fontsize=8)
a1.grid(axis="y", alpha=0.25)

maes = [summary["mae_top"], summary["mae_ctrl"]]
bars = a2.bar(["优先组", "对照组"], maes, color=["#34d3c8", "#f2b344"], width=0.55)
a2.set_ylabel("MAE [m³ CH4/d]")
a2.set_title(f"MAE 相对降低 {summary['mae_reduction']:.1%}"
             f"（判据 ≥20%）", fontsize=10)
for b, v in zip(bars, maes):
    a2.text(b.get_x() + b.get_width() / 2, v * 1.02, f"{v:.1f}", ha="center", fontsize=9)
a2.set_ylim(0, max(maes) * 1.22)
a2.grid(axis="y", alpha=0.25)
ci = summary["bootstrap_ci95"]
a2.text(0.5, 0.93, f"bootstrap 95%CI [{ci[0]:+.1f}, {ci[1]:+.1f}]",
        transform=a2.transAxes, ha="center", fontsize=8, color="#3a4a5e")
fig.tight_layout()
fig.savefig(HERE / "fig_error_comparison.png")

print("wrote:", HERE / "fig_sensitivity.png")
print("wrote:", HERE / "fig_error_comparison.png")
