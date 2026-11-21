/* 参数标定页面 */
"use strict";

const $ = (id) => document.getElementById(id);

const LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: "#8b95a5", size: 11, family: '"Cascadia Mono", Consolas, monospace' },
  margin: { l: 56, r: 16, t: 34, b: 38 },
  xaxis: { gridcolor: "#1b2028", zeroline: false },
  yaxis: { gridcolor: "#1b2028", zeroline: false },
  legend: { orientation: "h", y: 1.14, x: 0, font: { size: 10 } },
  hovermode: "x unified",
};
const CONFIG = { displayModeBar: false, responsive: true };

const state = { meta: null, running: false };

/* ------------------------------------------------------------------ setup */

async function loadMeta() {
  const res = await fetch("/api/lab/meta");
  const meta = await res.json();
  state.meta = meta;

  const sel = $("param");
  sel.innerHTML = "";
  for (const p of meta.parameters) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = `${p.zh}（${p.id}）`;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", applyDefaults);
  applyDefaults();
  await run();
}

function currentParam() {
  const id = $("param").value;
  return state.meta.parameters.find((p) => p.id === id);
}

function applyDefaults() {
  const p = currentParam();
  if (!p) return;
  const mid = +(p.lo + 0.6 * (p.hi - p.lo)).toFixed(4);
  $("truth").value = mid;
  $("truth").min = p.lo;
  $("truth").max = p.hi;
  $("start").value = p.default;
  $("param-hint").textContent = `范围 ${p.lo} – ${p.hi} ${p.unit}，默认 ${p.default}`;
}

/* ------------------------------------------------------------------- run */

async function run() {
  if (state.running) return;
  state.running = true;
  const btn = $("run");
  btn.disabled = true;
  setStatus("标定中… 30 天的数据大约 5 秒", "");

  const body = {
    parameter: $("param").value,
    truth: parseFloat($("truth").value),
    start: parseFloat($("start").value),
    days: parseInt($("days").value, 10) || 30,
    method: $("method").value,
    max_iterations: 30,
  };

  try {
    const res = await fetch("/api/calibration", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await res.json();
    if (!res.ok || !j.ok) throw new Error(j.detail || `HTTP ${res.status}`);
    render(j);
  } catch (e) {
    setStatus("失败：" + e.message, "err");
  } finally {
    state.running = false;
    btn.disabled = false;
  }
}

function setStatus(text, cls) {
  const el = $("status");
  el.textContent = text;
  el.className = "status" + (cls ? " " + cls : "");
}

/* ---------------------------------------------------------------- render */

function render(r) {
  const unit = r.unit;

  $("kpi-truth").innerHTML = `<b>真值</b><strong>${r.truth.toFixed(3)}</strong><small>${unit}</small>`;
  $("kpi-cal").innerHTML = `<b>校准结果</b><strong>${r.calibrated.toFixed(3)}</strong><small>${unit}</small>`;
  const errPct = (r.rel_error * 100).toFixed(2);
  $("kpi-err").className = "kpi " + (r.rel_error < 0.02 ? "good" : r.rel_error < 0.1 ? "warn" : "bad");
  $("kpi-err").innerHTML = `<b>相对误差</b><strong>${errPct}</strong><small>%</small>`;
  const gain = r.before.rmse > 0 ? r.before.rmse / Math.max(r.after.rmse, 1e-9) : 0;
  const gainText = gain >= 1000 ? gain.toExponential(1) : gain.toFixed(0);
  $("kpi-rmse").className = "kpi " + (r.after.rmse < r.before.rmse ? "good" : "warn");
  $("kpi-rmse").innerHTML = `<b>RMSE 改善</b><strong>×${gainText}</strong><small>倍</small>`;

  setStatus(`${r.method} · ${r.success ? "收敛" : "未收敛"} · ${r.iterations} 次迭代 · ${r.elapsed_s}s`, r.success ? "ok" : "err");
  $("sub").textContent =
    `RMSE ${r.before.rmse.toFixed(2)} → ${r.after.rmse.toFixed(4)} m³/d　` +
    `R² ${r.after.r2.toFixed(4)}　初始猜测 ${r.start} → 校准 ${r.calibrated}`;

  const t = r.series.t;
  Plotly.react(
    "fit",
    [
      { x: t, y: r.series.observed, name: "实测（真值生成）", mode: "markers", marker: { size: 5, color: "#8b95a5" } },
      { x: t, y: r.series.before, name: `校准前（${r.start}）`, mode: "lines", line: { color: "#f87171", width: 1.6, dash: "dot" } },
      { x: t, y: r.series.after, name: `校准后（${r.calibrated}）`, mode: "lines", line: { color: "#34d3c8", width: 2 } },
    ],
    {
      ...LAYOUT,
      title: { text: "产甲烷速率拟合", font: { size: 12, color: "#8b95a5" }, x: 0.01, xanchor: "left" },
      xaxis: { ...LAYOUT.xaxis, title: { text: "时间 [d]", font: { size: 10 } } },
      yaxis: { ...LAYOUT.yaxis, title: { text: "Q_CH4 [m³/d]", font: { size: 10 } } },
    },
    CONFIG
  );

  Plotly.react(
    "cmp",
    [
      {
        x: ["校准前", "校准后"],
        y: [r.before.rmse, r.after.rmse],
        type: "bar",
        width: 0.42,
        marker: { color: ["#f87171", "#34d3c8"] },
        text: [r.before.rmse.toFixed(2), r.after.rmse.toFixed(4)],
        textposition: "outside",
        textfont: { size: 11 },
      },
    ],
    {
      ...LAYOUT,
      title: { text: "与实测的 RMSE（越低越好）", font: { size: 12, color: "#8b95a5" }, x: 0.01, xanchor: "left" },
      yaxis: { ...LAYOUT.yaxis, title: { text: "RMSE [m³/d]", font: { size: 10 } }, type: "log" },
      showlegend: false,
    },
    CONFIG
  );
}

$("run").addEventListener("click", run);
window.addEventListener("resize", () => {
  Plotly.Plots.resize($("fit"));
  Plotly.Plots.resize($("cmp"));
});
loadMeta().catch((e) => setStatus("初始化失败：" + e.message, "err"));