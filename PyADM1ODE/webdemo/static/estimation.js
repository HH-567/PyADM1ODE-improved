/* 状态估计页面 */
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

const state = { running: false };

async function run() {
  if (state.running) return;
  state.running = true;
  const btn = $("run");
  btn.disabled = true;

  const hours = Math.max(4, Math.min(24, parseInt($("hours").value, 10) || 12));
  const prior = Math.max(0.2, Math.min(1.4, parseFloat($("prior").value) || 0.6));
  setStatus(`估计中… ${hours} 小时，约 ${Math.round(hours * 2.6)} 秒`, "");

  try {
    const res = await fetch("/api/estimation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hours, prior_scale: prior }),
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

function render(r) {
  const err = r.median_state_error;
  $("kpi-state").className = "kpi " + (err < 0.05 ? "good" : err < 0.15 ? "warn" : "bad");
  $("kpi-state").innerHTML = `<b>状态中位误差</b><strong>${(err * 100).toFixed(2)}</strong><small>%</small>`;

  const trueTotal = r.true_flow[0] + r.true_flow[1];
  const estTotal = r.est_flow[0] + r.est_flow[1];
  const dev = Math.abs(estTotal - trueTotal) / trueTotal;
  $("kpi-feed").className = "kpi " + (dev < 0.05 ? "good" : dev < 0.2 ? "warn" : "bad");
  $("kpi-feed").innerHTML = `<b>进料估计</b><strong>${estTotal.toFixed(1)}</strong><small>真值 ${trueTotal.toFixed(1)} m³/d</small>`;

  // pH 追踪误差：看中位数，第一步的预测是离群值，用最大值会把结论带偏
  const pred = r.ph_series.pred.map((v) => (v == null ? NaN : v));
  const obs = r.ph_series.obs.map((v) => (v == null ? NaN : v));
  const devs = [];
  for (let i = 0; i < Math.min(pred.length, obs.length); i++) {
    if (Number.isFinite(pred[i]) && Number.isFinite(obs[i])) devs.push(Math.abs(pred[i] - obs[i]));
  }
  devs.sort((a, b) => a - b);
  const medDev = devs.length ? devs[Math.floor(devs.length / 2)] : 0;
  const maxDev = devs.length ? devs[devs.length - 1] : 0;
  $("kpi-ph").className = "kpi " + (medDev < 0.02 ? "good" : medDev < 0.1 ? "warn" : "bad");
  $("kpi-ph").innerHTML =
    `<b>pH 追踪</b><strong>${medDev.toFixed(4)}</strong><small>中位（最大 ${maxDev.toFixed(3)}）</small>`;

  $("kpi-cost").innerHTML = `<b>耗时</b><strong>${r.elapsed_s}</strong><small>s</small>`;

  setStatus(
    `${r.steps} 步 · 观测 ${r.sensors.join(" / ")} · 先验 ${(r.prior_scale * 100).toFixed(0)}% 真值 · ${r.aligned ? "状态对齐" : "状态不可比"}`,
    "ok"
  );
  $("sub").textContent =
    `进料通道：真值 ${r.true_flow.map((v) => v.toFixed(2)).join(" / ")}　` +
    `先验 ${r.prior.map((v) => v.toFixed(2)).join(" / ")}　` +
    `估计 ${r.est_flow.map((v) => v.toFixed(2)).join(" / ")} m³/d`;

  const t = r.flow_series.t;
  Plotly.react(
    "flow",
    [
      {
        x: t, y: r.flow_series.maize, name: "估计·玉米青贮", mode: "lines",
        line: { color: "#34d3c8", width: 2 },
      },
      {
        x: t, y: r.flow_series.swine, name: "估计·猪粪", mode: "lines",
        line: { color: "#7cb8ff", width: 2 },
      },
      {
        x: [t[0], t[t.length - 1]], y: [r.true_flow[0], r.true_flow[0]],
        name: "真值·玉米青贮", mode: "lines", line: { color: "#34d3c8", width: 1, dash: "dash" },
      },
      {
        x: [t[0], t[t.length - 1]], y: [r.true_flow[1], r.true_flow[1]],
        name: "真值·猪粪", mode: "lines", line: { color: "#7cb8ff", width: 1, dash: "dash" },
      },
    ],
    {
      ...LAYOUT,
      title: { text: "进料量估计 vs 真值", font: { size: 12, color: "#8b95a5" }, x: 0.01, xanchor: "left" },
      xaxis: { ...LAYOUT.xaxis, title: { text: "时间 [h]", font: { size: 10 } } },
      yaxis: { ...LAYOUT.yaxis, title: { text: "流量 [m³/d]", font: { size: 10 } } },
    },
    CONFIG
  );

  Plotly.react(
    "ph",
    [
      { x: r.ph_series.t, y: r.ph_series.obs, name: "观测（带噪声）", mode: "markers", marker: { size: 5, color: "#8b95a5" } },
      { x: r.ph_series.t, y: r.ph_series.pred, name: "滤波器预测", mode: "lines", line: { color: "#f2b344", width: 2 } },
    ],
    {
      ...LAYOUT,
      title: { text: "pH：预测 vs 观测", font: { size: 12, color: "#8b95a5" }, x: 0.01, xanchor: "left" },
      xaxis: { ...LAYOUT.xaxis, title: { text: "时间 [h]", font: { size: 10 } } },
      yaxis: { ...LAYOUT.yaxis, title: { text: "pH", font: { size: 10 } } },
    },
    CONFIG
  );
}

$("run").addEventListener("click", run);
window.addEventListener("resize", () => {
  Plotly.Plots.resize($("flow"));
  Plotly.Plots.resize($("ph"));
});
run();