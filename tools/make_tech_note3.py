"""生成「技术选型说明：三个引入模块」DOCX（两页 A4）。

第 1 页：总览 + Julia 求解器
第 2 页：参数标定 + 状态估计 + 三者定位
数据均为本会话实测。
"""

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ACCENT = RGBColor(0x0F, 0x6E, 0x6A)
MUTED = RGBColor(0x55, 0x60, 0x6B)
INK = RGBColor(0x1A, 0x1E, 0x24)
WARN = RGBColor(0xA8, 0x4A, 0x0A)
FONT = "Microsoft YaHei"

doc = Document()

sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
sec.top_margin = Cm(1.2)
sec.bottom_margin = Cm(1.0)
sec.left_margin = Cm(1.5)
sec.right_margin = Cm(1.5)

style = doc.styles["Normal"]
style.font.size = Pt(8.7)
style.font.name = FONT
style.font.color.rgb = INK
style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
style.paragraph_format.space_after = Pt(2.2)
style.paragraph_format.line_spacing = 1.09


def _run(p, text, size, bold=False, color=INK, mono=False):
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    r.font.name = "Consolas" if mono else FONT
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    return r


def para(text="", size=8.7, bold=False, color=INK, before=0, after=2, align=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    if align is not None:
        p.alignment = align
    _run(p, text, size, bold, color)
    return p


def rich(parts, before=0, after=2):
    """parts: [(text, bold, color)]"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    for text, bold, color in parts:
        _run(p, text, 8.7, bold, color)
    return p


def heading(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    _run(p, text, 10.0, True, ACCENT)
    pPr = p._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "4")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "0F6E6A")
    borders.append(bottom)
    pPr.append(borders)


def table(rows, widths, size=8.0):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = t.cell(i, j)
            cell.width = Cm(widths[j])
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0.8)
            p.paragraph_format.space_after = Pt(0.8)
            p.paragraph_format.line_spacing = 1.0
            _run(p, str(val), size, bold=(i == 0))
            if i > 0 and j > 0:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if i == 0:
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:fill"), "EEF5F4")
                cell._tc.get_or_add_tcPr().append(shd)
    return t


# ================================================================ 第 1 页
para("技术选型说明", size=8.0, color=MUTED, after=0)
para("三个外部模块的引入：求解器 / 参数标定 / 状态估计", size=13, bold=True, after=1)
para(
    "大创项目《面向农业沼气的甲烷产量参数敏感性分析与关键筛选检验》· 核心建模与算法 · "
    "下列数据均为本机实测，可用文末命令复跑",
    size=8.0,
    color=MUTED,
    after=1,
)

heading("总览：三个模块各自解决什么、在本项目里算什么")
table(
    [
        ["模块", "版本 / 许可", "解决什么", "本项目定位"],
        ["PyADM1ODE（基线）", "0.6.2 / MIT", "ADM1da 41 状态机理模型与发酵罐组件", "主线基准"],
        ["Julia + Rodas4P", "1.13 / MIT", "刚性 ODE 求解，替代 scipy·BDF", "支撑条件"],
        ["pyadm1ode_calibration", "0.2.8 / MIT", "由数据反演动力学参数", "深化项 A"],
        ["pyadm1ode_estimation", "0.2.5 / MIT", "由易测量反推内部状态（UKF）", "深化项 B"],
    ],
    widths=[4.0, 2.9, 6.4, 2.7],
)

heading("一、求解器：Julia + Rodas4P —— 只换求解器，不动模型")
para(
    "ADM1da 的 41 状态方程与 Python 侧接口保持不变，仅把刚性 ODE 积分交由 Julia 完成：右端项逐行移植，"
    "用 Rodas4P（Rosenbrock 法）替代 scipy 的 BDF。引入方式为四层：模型源（adm1da.jl）→ 预编译包"
    "（PyADM1Julia，把 JIT 烘进 pkgimage）→ 桥接（adm1_julia.py，参数打包 / 状态往返）→ 后端开关"
    "（PYADM1_SOLVER=scipy|adm1jl，默认 scipy）。"
)
table(
    [
        ["场景", "scipy·BDF", "Julia·Rodas4P", "加速", "终值一致性"],
        ["150 天单消化器", "0.609 s", "0.121 s", "5.02×", "沼气 2185.1 = 2185.1"],
        ["30 天单消化器", "0.142 s", "0.023 s", "6.22×", "沼气 2162.0 = 2162.0"],
        ["本项目 115 次筛选实验", "92.5 s", "约 30 s", "约 3×", "轨迹最大相对偏差 2.7×10⁻⁶"],
    ],
    widths=[4.6, 3.0, 3.4, 1.9, 4.1],
)
para(
    "四个关键决定：① 参数以 Python 的 PARAM_FIELDS 为唯一真源，100 项参数 + 37 项进料打包成 137 维向量，"
    "Julia 用 NamedTuple 解包，字段错位立即报错而非静默读错；② 模型写两份，用 9 项 parity 测试逐分量对账；"
    "③ calc_ph / ph_inhib 不写类型标注，否则 ForwardDiff 的 Dual 数进不来；"
    "④ 预编译工作负载不能包在 try/catch 里，否则 @compile_workload 不缓存。",
    before=2.5,
)
rich(
    [
        ("缺陷与边界：", True, INK),
        (
            "冷启动 6.3 s（缓存热）～20 s（文件冷），150 天每次省 0.49 s、30 天省 0.12 s，"
            "故需连跑 13–53 次才回本；scipy 的 AdaptiveODESolver 无对应物（max_step 只能近似为 dtmax）；"
            "多 300 MB 运行时，换机需重建；juliacall 跨线程调用会僵死，必须单线程串行。",
            False,
            INK,
        ),
    ],
    before=1.5,
)
rich(
    [
        ("结论：", True, INK),
        (
            "写「条件保障」而非成果——它不改变问题与结论，只把机时压下来；短任务不划算，默认仍用 scipy。",
            False,
            INK,
        ),
    ],
    before=1.5,
)

heading("二、参数标定：pyadm1ode_calibration —— 由数据反演参数")
para(
    "用途：给定进料序列与实测产气，优化器（差分进化 / Nelder-Mead / L-BFGS-B / PSO 等）搜索使仿真贴近实测的参数组，"
    "并提供拟合指标与可辨识性分析。用法只有三步：MeasurementData(df) → "
    "run_initial_calibration(meas, [参数], objectives=['Q_ch4']) → apply_calibration(result)。"
)
table(
    [
        ["验证方式", "合成数据（用已知真值生成实测，再由算法反推）"],
        ["实测结果", "真值 k_hyd_ch = 9.40 → 反推 9.40（误差 0.00%）；RMSE 120.50 → 0.0008；R² = 1.0000"],
        ["代价", "Nelder-Mead 16 次迭代 / 30 天数据 / 4.4 秒；差分进化约慢一个量级"],
    ],
    widths=[3.2, 13.8],
)
rich(
    [
        ("必须记住的坑：", True, WARN),
        (
            "标定包自带 41 个参数名，但 pyadm1 0.6.2 的 kinetic 字典里只有 33 个能对上——"
            "k_dis 与 7 个 k_dec_X_* 写进去只落在 _calibration_params，仿真结果一点不变，表现为「校准无效」。"
            "正确名是 k_dis_PS / k_dis_PF、k_dec_su 之类。写脚本前先断言参数名在 _kinetic 里。",
            False,
            INK,
        ),
    ],
    before=2,
)
rich(
    [
        ("局限：", True, INK),
        (
            "本次用合成数据，只能证明「优化器找得到参数」，不能证明模型符合现实；真实数据缺失时多数参数不可辨识。",
            False,
            INK,
        ),
    ],
    before=1.5,
)

doc.add_page_break()
heading("三、状态估计：pyadm1ode_estimation —— 由易测量反推内部状态")
para(
    "用途：41 状态 SR-UKF（另有 PINN 与 GRU observer），把气相流量、pH 这类易测信号与机理模型融合，"
    "反推不可测的中间状态。本次用孪生实验验证：真值与滤波器两个装置预热到同一状态，滤波器只喂带噪声的 "
    "Q_gas / Q_ch4 / pH。"
)
table(
    [
        ["验证方式", "孪生实验：两侧预热到同一状态，先验故意设错（进料取真值的 60%）"],
        ["实测结果", "状态中位误差 2.76%（38/41 状态参与统计，剔除近零量）；pH 预测中位偏差 0.054"],
        ["代价", "每步 43 状态 → 87 个 sigma 点，每点推进一遍机理模型，约 2.5 秒/小时；12 小时约 30 秒"],
    ],
    widths=[3.2, 13.8],
)
rich(
    [
        ("关键发现（写进报告的诚实边界）：", True, WARN),
        (
            "进料通道在 16 小时内基本拉不动（先验 6.84 → 估计 6.85，真值 11.40）：消化器把扰动缓冲掉了，"
            "产气响应滞后，短窗口内接近不可观测；把先验调到 1.0 时进料估计精确到 11.396 / 6.097，可见滤波器本身无问题。",
            False,
            INK,
        ),
    ],
    before=2,
)
rich(
    [
        ("局限：", True, INK),
        (
            "同样依赖匹配的实测数据；PINN 分支需 torch（CPU 版约 200 MB），与筛选主线并列而非前置。",
            False,
            INK,
        ),
    ],
    before=1.5,
)

heading("四、定位与口径")
para(
    "三者都不进入主线结论：Julia 是条件保障，标定属深化项 A，估计属深化项 B；主线结论仍由筛选实验给出。"
    "把它们的实测数字作为技术储备呈现可提高可信度，但不改变验收标准。",
    before=1,
)

heading("五、答辩口径（三句话）")
para(
    "① 主线是参数筛选：12 个候选、单因素排序、30 组新组合配对检验，优先组误差比对照组低 61.6%，"
    "bootstrap 95%CI 不含 0，判定有效。",
    before=1,
)
para(
    "② Julia 只换求解器：结果与 scipy 一致（2.7×10⁻⁶），长仿真快 5–6 倍，属条件保障而非成果。"
)
para(
    "③ 标定与估计是深化项：都用合成数据验证方法本身，能反演不等于符合现实，不进入主线结论。"
)

# -------------------------------------------------------------- 页脚
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(6)
_run(
    p,
    "复跑：python experiment_screening.py --seed 20261117 ｜ 标定/估计见网页 /calibration、/estimation",
    7.2,
    color=MUTED,
    mono=True,
)

doc.save("技术选型说明_三个模块.docx")
print("saved 技术选型说明_三个模块.docx")
