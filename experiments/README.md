# experiments —— 改进验证与课题实验

这些脚本都以仓库根目录的 `PyADM1ODE` 包为基础，用 `.venv` 直接跑，不需要额外安装。

```powershell
.\.venv\Scripts\python.exe experiments\experiment_screening.py
```

| 脚本 | 干什么 | 产出 |
| --- | --- | --- |
| `experiment_screening.py` | 参数筛选主线：10+ 候选参数范围归一化敏感度排序，再用**未参与排序的新组合**验证"只留高优先参数"能多大程度重现全参数同时变化时的甲烷日产量 | `results/out/`（`sensitivity.csv`、`combinations.csv`、`summary.json`） |
| `experiment_figures.py` | 把上面的结果画成两张图 | `results/out/fig_sensitivity.png`、`fig_error_comparison.png` |
| `probe_signal_gate.py` | 观测信号能否支撑参数辨识的可行性探针 | 终端输出 |
| `probe_online.py` | 在线校正（逐测量更新 / 窗口重标定）的数值探针 | 终端输出 |
| `demo_calibration.py` | 用 `pyadm1ode_calibration` 从合成"实测数据"反演一个动力学参数 | 终端输出 + 图 |
| `demo_estimation.py` | 用 `pyadm1ode_estimation` 的 UKF 从产气/pH 反推进料量 | 终端输出 + 图 |
| `bench_click.py` | 计时基准（重复跑同一工况取中位数） | 终端输出 |

`results/seed2/` 是换随机种子重跑同一主线的结果，用来确认结论不是单次抽样的运气。
`results/out/结果摘要.md` 是对这批结果的人工整理（结论、置信区间、复跑方法），重跑脚本不会覆盖它。

> 说明：`results/` 里的数值入库存档，方便对照；重跑会覆盖同名文件。
