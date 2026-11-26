/* 传感器：读的是工作区里那个罐，按小时喂给滤波器 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { active: false, busy: false, snap: null };

// 统一主题（chart-theme.js）：颜色、单位、刻度全站一致
const LAYOUT = (title, ytitle) => CHART.layout({ title, yTitle: ytitle, xTitle: "罐龄 [天]" });
const CONFIG = CHART.config;

function setStatus(t, c) {
  const el = $("status");
  el.textContent = t;
  el.className = "status" + (c ? " " + c : "");
}

function syncButtons() {
  const on = state.active && !state.busy;
  $("poll").disabled = !on;
  $("poll6").disabled = !on;
  $("push").disabled = !on;
  $("perturb").disabled = !on;
}

/** 模拟工艺变化：把罐内参数拉开，再让诊断去识别。 */
async function perturb() {
  if (!state.active) return;
  state.busy = true;
  syncButtons();
  try {
    const factor = parseFloat($("drift").value) || 1.3;
    const snap = await post("/api/sensors/perturb", { factor, parameter: "k_m_ac" });
    state.snap = snap;
    render(snap);
    await refreshDiagnosis();
    const d = (snap.drifts || [])[(snap.drifts || []).length - 1];
    setStatus(
      d ? `已把 ${d.param} 从 ${d.before} 拉到 ${d.after}（×${factor}）。接下来读数会反映出来。` : "已施加偏移",
      "ok"
    );
  } catch (e) {
    setStatus("施加偏移失败：" + e.message, "err");
  } finally {
    state.busy = false;
    syncButtons();
  }
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok || j.ok === false) throw new Error(j.detail || `HTTP ${res.status}`);
  return j;
}

function sensorCfg() {
  return {
    bias: (parseFloat($("bias").value) || 0) / 100,
    ph_bias: parseFloat($("phb").value) || 0,
    noise: parseFloat($("noise").value) || 0.02,
  };
}

async function poll(n = 1) {
  if (!state.active) return;
  state.busy = true;
  syncButtons();
  try {
    await post("/api/sensors/config", sensorCfg());
    let snap = null;
    for (let i = 0; i < n; i++) {
      setStatus(`读数中… ${i + 1}/${n}`, "busy");
      snap = await post("/api/sensors/poll");
    }
    state.snap = snap;
    render(snap);
    refreshDiagnosis();
    const h = snap.hourly;
    setStatus(
      `罐龄 ${snap.t_days.toFixed(2)} 天 ｜ 读数 ${h.q_gas[h.q_gas.length - 1]} vs 真值 ${h.truth_gas[h.truth_gas.length - 1]} ｜ 状态误差 ${(h.err[h.err.length - 1] * 100).toFixed(1)}%`,
      "ok"
    );
  } catch (e) {
    setStatus("取数失败：" + e.message, "err");
  } finally {
    state.busy = false;
    syncButtons();
  }
}

async function push() {
  if (!state.active) return;
  const body = {};
  const qg = parseFloat($("qg").value);
  const qc = parseFloat($("qc").value);
  const ph = parseFloat($("ph").value);
  if (!isNaN(qg)) body.Q_gas = qg;
  if (!isNaN(qc)) body.Q_ch4 = qc;
  if (!isNaN(ph)) body.pH = ph;
  if (!Object.keys(body).length) {
    setStatus("至少填一个读数", "err");
    return;
  }
  state.busy = true;
  syncButtons();
  try {
    const snap = await post("/api/sensors/push", body);
    state.snap = snap;
    render(snap);
    refreshDiagnosis();
    setStatus(`已注入外部读数（不推进罐龄），滤波器当前状态误差 ${(snap.hourly.err[snap.hourly.err.length - 1] * 100).toFixed(1)}%`, "ok");
  } catch (e) {
    setStatus("注入失败：" + e.message, "err");
  } finally {
    state.busy = false;
    syncButtons();
  }
}

function render(s) {
  const h = s.hourly || { t: [], q_gas: [], truth_gas: [], err: [] };
  const n = h.t.length;
  $("k-t").textContent = `${s.t_days.toFixed(2)} d`;
  $("k-rg").textContent = n ? h.q_gas[n - 1] : "—";
  $("k-tg").textContent = n ? h.truth_gas[n - 1] : "—";
  const rg = n ? h.q_gas[n - 1] : null;
  const tg = n ? h.truth_gas[n - 1] : null;
  $("k-err").textContent = rg && tg ? (((rg - tg) / tg) * 100).toFixed(1) + "%" : "—";
  $("k-st").textContent = n && h.err[n - 1] !== null ? (h.err[n - 1] * 100).toFixed(1) + "%" : "—";
  $("k-n").textContent = n;
  Plotly.react(
    $("chart-gas"),
    [
      CHART.line(h.t, h.q_gas, { name: "传感器读数", color: CHART.COLOR.gas, unit: "m³/d" }),
      CHART.line(h.t, h.truth_gas, { name: "真值", color: CHART.COLOR.muted, dash: "dot", unit: "m³/d" }),
    ],
    LAYOUT("传感器读数 vs 真值", "m³/d"),
    CONFIG
  );
  Plotly.react(
    $("chart-err"),
    [
      CHART.line(
        h.t,
        h.err.map((v) => (v === null || !isFinite(v) ? null : v * 100)),
        { name: "状态中位误差", color: CHART.COLOR.err, unit: "%" }
      ),
    ],
    LAYOUT("滤波器状态误差（每读一次修正一次）", "%"),
    CONFIG
  );
}

$("poll").addEventListener("click", () => poll(1));
$("poll6").addEventListener("click", () => poll(6));
$("push").addEventListener("click", push);
$("perturb").addEventListener("click", perturb);

// 范例演示：用合成快照跑同一套规则，标清"（合成）"，随时可回到实时
document.querySelectorAll("[data-demo]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    try {
      const j = await fetch("/api/diagnose/demo").then((r) => r.json());
      if (!j.ok) throw new Error("范例不可用");
      const it = (j.items || []).find((x) => x.id === btn.dataset.demo);
      if (!it) throw new Error("没有这个范例");
      paintDiagnosis(it.result, "范例·合成");
      setStatus(`范例「${it.label}」：${it.hint}。这是合成数据，仅演示判断逻辑。`, "ok");
    } catch (e) {
      setStatus("范例失败：" + e.message, "err");
    }
  })
);
$("demo-live").addEventListener("click", () => {
  refreshDiagnosis();
  setStatus("已回到实时诊断。", "ok");
});

/** 渲染一份诊断结果（实时或范例共用）。 */
function paintDiagnosis(j, tag) {
  const box = $("diag");
  box.className = "diag" + (j.level === "serious" ? " serious" : j.level === "warn" ? " warn" : "");
  const causes = (j.causes || [])
    .map(
      (c) => `<div class="cause">
        <b>${c.cause}</b><span style="opacity:.7">（可能性：${c.likelihood}）</span>
        <div class="ev">证据：${c.evidence}</div>
        <div class="act">立即：${c.now}</div>
        <div class="act">24 小时内：${c.later}</div>
      </div>`
    )
    .join("");
  const ev = j.evidence || {};
  const evLine = Object.entries(ev)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k} ${typeof v === "number" ? v.toFixed(2) : v}`)
    .join(" ｜ ");
  box.innerHTML = `<div><b>${j.level === "serious" ? "⛔ 异常" : j.level === "warn" ? "⚠ 注意" : "✓ 正常"}</b>
    ｜ ${tag ? `<span style="color:#ffd9a0">[${tag}]</span> ` : ""}${j.headline}
    <div style="opacity:.75">${evLine}</div></div>
    ${causes || `<div class="ev" style="margin-top:4px">${j.detail || ""}</div>`}
    <div class="ev" style="margin-top:6px">${j.note || ""}</div>`;
}

/** 诊断面板：正常/注意/异常 + 可能原因 + 立即动作。 */
async function refreshDiagnosis() {
  const box = $("diag");
  try {
    const j = await fetch("/api/diagnose").then((r) => r.json());
    if (!j.ok) {
      box.className = "diag";
      box.textContent = "诊断：" + (j.headline || "暂无数据");
      return;
    }
    paintDiagnosis(j);
  } catch (e) {
    box.className = "diag";
    box.textContent = "诊断不可用：" + e.message;
  }
}

$("poll").addEventListener("click", () => poll(1));
$("poll6").addEventListener("click", () => poll(6));
$("push").addEventListener("click", push);

(async function boot() {
  try {
    const j = await fetch("/api/sensors/state").then((r) => r.json());
    if (j.active) {
      state.active = true;
      state.snap = j;
      render(j);
      refreshDiagnosis();
      setStatus(`已接上工作区会话：第 ${j.day} 天（罐龄 ${j.t_days.toFixed(2)} 天）。`, "ok");
    } else {
      setStatus("还没有会话：先到「按天推进」启动一个工作区会话，再回来读数。", "err");
    }
  } catch (e) {
    setStatus("读取会话失败：" + e.message, "err");
  }
  syncButtons();
})();
