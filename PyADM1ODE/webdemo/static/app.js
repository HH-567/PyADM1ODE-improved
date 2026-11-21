/* PyADM1ODE 可视化操作平台 — 前端逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  meta: null,
  feeds: [],
  last: null,
  busy: false,
};

const PLOT_CONFIG = { displayModeBar: false, responsive: true };

/* ---------------------------------------------------------------- utils */

function fmt(x, digits = 1) {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return Number(x).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text;
  el.className = "status" + (kind ? " " + kind : "");
}

function setBusy(busy) {
  state.busy = busy;
  $("run").disabled = busy;
  $("compare").disabled = busy;
  $("reset").disabled = busy;
}

function readNumber(id, fallback) {
  const v = parseFloat($(id).value);
  return Number.isFinite(v) ? v : fallback;
}

/* ---------------------------------------------------------------- meta */

async function init() {
  try {
    const res = await fetch("/api/meta");
    if (!res.ok) throw new Error("HTTP " + res.status);
    state.meta = await res.json();
  } catch (err) {
    setStatus("无法连接后端：" + err.message, "error");
    return;
  }

  $("badge-model").textContent = state.meta.model;
  $("badge-version").textContent = "v" + state.meta.version;

  const juliaOpt = $("backend").querySelector('option[value="adm1jl"]');
  if (!state.meta.julia_installed) {
    juliaOpt.disabled = true;
    juliaOpt.textContent = "ADM1jl · Rodas4P（未安装 Julia）";
  }

  state.feeds = state.meta.defaults.feed.map((f) => ({ ...f }));
  renderFeeds();
  bindEvents();
  await runSimulation();

  // ?compare=1 runs the two-solver comparison on load — handy for a live demo.
  if (new URLSearchParams(window.location.search).get("compare") === "1") {
    await runCompare();
  }
}

/* ---------------------------------------------------------------- feed rows */

function renderFeeds() {
  const container = $("feed-rows");
  container.innerHTML = "";

  state.feeds.forEach((row, index) => {
    const div = document.createElement("div");
    div.className = "feed-row";

    const select = document.createElement("select");
    for (const id of state.meta.substrates) {
      const opt = document.createElement("option");
      opt.value = id;
      // English id first, Chinese gloss in brackets: cattle_manure（牛粪）
      const zh = (state.meta.substrate_labels || {})[id];
      opt.textContent = zh ? `${id}（${zh}）` : id;
      opt.title = opt.textContent;
      if (id === row.substrate_id) opt.selected = true;
      select.appendChild(opt);
    }
    select.title = select.options[select.selectedIndex]?.textContent || "";
    select.addEventListener("change", () => {
      state.feeds[index].substrate_id = select.value;
    });

    const q = document.createElement("input");
    q.type = "number";
    q.min = "0";
    q.max = "500";
    q.step = "0.1";
    q.value = row.q;
    q.title = "进料量 [m³/d]";
    q.addEventListener("input", () => {
      state.feeds[index].q = parseFloat(q.value) || 0;
      updateFeedTotal();
    });

    const remove = document.createElement("button");
    remove.className = "remove";
    remove.textContent = "×";
    remove.title = "移除该基质";
    remove.disabled = state.feeds.length <= 1;
    remove.addEventListener("click", () => {
      if (state.feeds.length <= 1) return;
      state.feeds.splice(index, 1);
      renderFeeds();
    });

    div.append(select, q, remove);
    container.appendChild(div);
  });

  updateFeedTotal();
}

function updateFeedTotal() {
  const total = state.feeds.reduce((acc, f) => acc + (f.q || 0), 0);
  $("feed-total").textContent = fmt(total, 1);
}

/* ---------------------------------------------------------------- events */

function bindEvents() {
  $("add-feed").addEventListener("click", () => {
    const used = new Set(state.feeds.map((f) => f.substrate_id));
    const next = state.meta.substrates.find((s) => !used.has(s)) || state.meta.substrates[0];
    state.feeds.push({ substrate_id: next, q: 0 });
    renderFeeds();
  });

  $("temp").addEventListener("input", (e) => {
    $("temp-out").textContent = fmt(parseFloat(e.target.value), 1);
  });
  $("kla").addEventListener("input", (e) => {
    $("kla-out").textContent = e.target.value;
  });

  $("backend").addEventListener("change", (e) => {
    $("badge-backend").textContent = e.target.value;
    if (e.target.value === "adm1jl") {
      setStatus("已选择 Julia 后端（ADM1jl · Rodas4P）");
    }
  });

  $("run").addEventListener("click", runSimulation);
  $("compare").addEventListener("click", runCompare);
  $("reset").addEventListener("click", resetDefaults);
  $("export").addEventListener("click", exportCsv);
}

function resetDefaults() {
  const d = state.meta.defaults;
  $("temp").value = d.temperature_c;
  $("temp-out").textContent = fmt(d.temperature_c, 1);
  $("kla").value = d.k_l_a;
  $("kla-out").textContent = d.k_l_a;
  $("vliq").value = d.v_liq;
  $("vgas").value = d.v_gas;
  $("duration").value = d.duration;
  $("dt").value = d.dt;
  $("backend").value = d.backend;
  $("badge-backend").textContent = d.backend;
  state.feeds = d.feed.map((f) => ({ ...f }));
  renderFeeds();
  setStatus("已恢复默认参数");
}

/* ---------------------------------------------------------------- request */

function buildRequest() {
  return {
    backend: $("backend").value,
    duration: readNumber("duration", 150),
    dt: readNumber("dt", 1),
    temperature_c: readNumber("temp", 42),
    v_liq: readNumber("vliq", 1050),
    v_gas: readNumber("vgas", 150),
    k_l_a: readNumber("kla", 200),
    feed: state.feeds
      .filter((f) => f.q > 0)
      .map((f) => ({ substrate_id: f.substrate_id, q: f.q })),
  };
}

/* ---------------------------------------------------------------- simulate */

async function runSimulation() {
  if (state.busy) return;

  const req = buildRequest();
  if (req.feed.length === 0) {
    setStatus("至少需要一种基质，且进料量大于 0", "error");
    return;
  }

  setBusy(true);
  setStatus("正在积分 " + req.duration + " 天，请稍候…", "busy");
  const tick = setInterval(() => {
    const el = $("status");
    el.textContent = "正在积分 " + req.duration + " 天" + ".".repeat((Date.now() / 400) % 4 | 0);
  }, 400);

  try {
    const res = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);

    state.last = data;
    renderResult(data);
    setStatus(
      "完成 · " + fmt(data.elapsed_s, 2) + " s · " + data.summary.points + " 个时间点 · " + data.backend,
      "ok",
    );
    $("run-meta").textContent =
      "求解器 " + data.backend + " · 耗时 " + fmt(data.elapsed_s, 2) + " s · " +
      data.summary.points + " 点 · 总进料 " + fmt(data.summary.feed_total, 1) + " m³/d";
    $("export").disabled = false;
  } catch (err) {
    setStatus("仿真失败：" + err.message, "error");
  } finally {
    clearInterval(tick);
    setBusy(false);
  }
}

/* ---------------------------------------------------------------- render */

function renderResult(data) {
  updateKpis(data.summary);
  updateSchematic(data.summary);
  drawCharts(data.series);
}

function updateKpis(s) {
  const cards = [
    { label: "平均沼气产量", value: fmt(s.avg_q_gas, 1), unit: "m³/d", cls: "gas" },
    { label: "平均甲烷占比", value: fmt(s.avg_ch4_pct, 1), unit: "%", cls: "methane" },
    { label: "累计产气量", value: fmt(s.total_gas_nm3, 0), unit: "Nm³", cls: "" },
    { label: "末态 pH", value: fmt(s.final_ph, 2), unit: "", cls: "methane" },
    { label: "水力停留时间", value: fmt(s.final_hrt, 1), unit: "d", cls: "" },
    { label: "VFA / TAC", value: fmt(s.final_vfa, 2) + " / " + fmt(s.final_tac, 2), unit: "kg/m³", cls: "warn" },
  ];

  $("kpis").innerHTML = cards
    .map(
      (c) => `
      <div class="kpi ${c.cls}">
        <div class="k-label">${c.label}</div>
        <div class="k-value">${c.value}<span class="k-unit">${c.unit}</span></div>
      </div>`
    )
    .join("");
}

function updateSchematic(s) {
  $("sv-feed").textContent = fmt(s.feed_total, 1) + " m³/d";
  $("sv-gas").textContent = fmt(s.avg_q_gas, 0) + " Nm³/d";
  $("sv-ph").textContent = fmt(s.final_ph, 2);
  $("sv-ch4").textContent = fmt(s.avg_ch4_pct, 1) + " %";
  $("sv-hrt").textContent = fmt(s.final_hrt, 1) + " d";
  $("sv-vfa").textContent = fmt(s.final_vfa, 2);

  const t = readNumber("temp", 42);
  const v = readNumber("vliq", 1050);
  $("sv-digester").textContent = fmt(v, 0) + " m³ · " + fmt(t, 1) + " °C";
}

/* ---------------------------------------------------------------- charts */

function baseLayout(yTitle) {
  return {
    margin: { l: 52, r: 18, t: 22, b: 40 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#8b98a8", size: 11, family: "Segoe UI, Microsoft YaHei, sans-serif" },
    xaxis: {
      title: { text: "时间 [d]", font: { size: 10 } },
      gridcolor: "#1a222c",
      zerolinecolor: "#1a222c",
      tickfont: { size: 10 },
    },
    yaxis: {
      title: { text: yTitle, font: { size: 10 } },
      gridcolor: "#1a222c",
      zerolinecolor: "#1a222c",
      tickfont: { size: 10 },
      rangemode: "tozero",
    },
    showlegend: true,
    legend: { orientation: "h", y: 1.16, x: 0, font: { size: 10 } },
    hovermode: "x unified",
    hoverlabel: { bgcolor: "#11161d", bordercolor: "#222c38", font: { color: "#e6edf3", size: 11 } },
  };
}

function drawCharts(s) {
  Plotly.react(
    "chart-gas",
    [
      {
        x: s.time, y: s.q_gas, name: "沼气", type: "scatter", mode: "lines",
        line: { color: "#38bdf8", width: 1.6 },
        fill: "tozeroy", fillcolor: "rgba(56,189,248,0.12)",
        hovertemplate: "%{y:.1f} m³/d<extra>沼气</extra>",
      },
      {
        x: s.time, y: s.q_ch4, name: "甲烷", type: "scatter", mode: "lines",
        line: { color: "#4ade80", width: 1.6 },
        hovertemplate: "%{y:.1f} m³/d<extra>甲烷</extra>",
      },
    ],
    baseLayout("m³/d"),
    PLOT_CONFIG
  );

  Plotly.react(
    "chart-pct",
    [
      {
        x: s.time, y: s.ch4_pct, name: "CH₄", type: "scatter", mode: "lines",
        line: { color: "#4ade80", width: 1.8 },
        hovertemplate: "%{y:.1f} %<extra>CH₄</extra>",
      },
    ],
    baseLayout("%"),
    PLOT_CONFIG
  );

  Plotly.react(
    "chart-ph",
    [
      {
        x: s.time, y: s.ph, name: "pH", type: "scatter", mode: "lines",
        line: { color: "#2dd4bf", width: 1.8 },
        hovertemplate: "%{y:.2f}<extra>pH</extra>",
      },
    ],
    (() => {
      const l = baseLayout("pH");
      l.yaxis.rangemode = "normal";
      return l;
    })(),
    PLOT_CONFIG
  );

  const layoutVfa = baseLayout("VFA [kg/m³]");
  layoutVfa.yaxis2 = {
    title: { text: "TAC [kg/m³]", font: { size: 10 } },
    overlaying: "y",
    side: "right",
    gridcolor: "rgba(0,0,0,0)",
    tickfont: { size: 10 },
    rangemode: "tozero",
  };
  layoutVfa.margin.r = 54;

  Plotly.react(
    "chart-vfa",
    [
      {
        x: s.time, y: s.vfa, name: "VFA", type: "scatter", mode: "lines",
        line: { color: "#f59e0b", width: 1.6 },
        hovertemplate: "%{y:.2f} kg/m³<extra>VFA</extra>",
      },
      {
        x: s.time, y: s.tac, name: "TAC", type: "scatter", mode: "lines",
        line: { color: "#a78bfa", width: 1.6, dash: "dot" },
        yaxis: "y2",
        hovertemplate: "%{y:.2f} kg/m³<extra>TAC</extra>",
      },
    ],
    layoutVfa,
    PLOT_CONFIG
  );
}

/* ---------------------------------------------------------------- compare */

async function runCompare() {
  if (state.busy) return;

  const req = buildRequest();
  if (req.feed.length === 0) {
    setStatus("至少需要一种基质，且进料量大于 0", "error");
    return;
  }

  setBusy(true);
  setStatus("正在用两个求解器各跑一遍…", "busy");

  try {
    const res = await fetch("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
    renderCompare(data);
    setStatus("对比完成 · 加速 " + data.speedup + "×", "ok");
  } catch (err) {
    setStatus("对比失败：" + err.message, "error");
  } finally {
    setBusy(false);
  }
}

function renderCompare(d) {
  const dev = d.max_relative_deviation;
  const rows = [
    ["求解器耗时", fmt(d.scipy.elapsed_s, 3) + " s", fmt(d.adm1jl.elapsed_s, 3) + " s"],
    ["平均沼气 [m³/d]", fmt(d.scipy.summary.avg_q_gas, 2), fmt(d.adm1jl.summary.avg_q_gas, 2)],
    ["平均 CH₄ [%]", fmt(d.scipy.summary.avg_ch4_pct, 2), fmt(d.adm1jl.summary.avg_ch4_pct, 2)],
    ["末态 pH", fmt(d.scipy.summary.final_ph, 3), fmt(d.adm1jl.summary.final_ph, 3)],
  ];

  const devRows = Object.entries(dev)
    .map(([k, v]) => `<tr><td>${k.toUpperCase()} 最大相对偏差</td><td class="num" colspan="2">${v === null ? "—" : v.toExponential(2)}</td></tr>`)
    .join("");

  $("compare-body").innerHTML = `
    <table class="cmp">
      <thead><tr><th>指标</th><th class="num">scipy · BDF</th><th class="num">ADM1jl · Rodas4P</th></tr></thead>
      <tbody>
        ${rows.map((r) => `<tr><td>${r[0]}</td><td class="num">${r[1]}</td><td class="num">${r[2]}</td></tr>`).join("")}
        ${devRows}
      </tbody>
    </table>
    <p style="font-family:var(--mono);font-size:12px;color:var(--muted);margin:10px 0 0">
      总加速 <span class="win">${d.speedup}×</span>
    </p>`;
  $("compare-panel").hidden = false;
}

/* ---------------------------------------------------------------- export */

function exportCsv() {
  const d = state.last;
  if (!d) return;

  const keys = ["time", "q_gas", "q_ch4", "q_co2", "ch4_pct", "ph", "vfa", "tac", "ts", "hrt"];
  const header = keys.join(",");
  const rows = [];
  const n = d.series.time.length;
  for (let i = 0; i < n; i++) {
    rows.push(keys.map((k) => (d.series[k][i] === null ? "" : d.series[k][i])).join(","));
  }

  const blob = new Blob([header + "\n" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "pyadm1_series.csv";
  a.click();
  URL.revokeObjectURL(url);
}

/* ---------------------------------------------------------------- start */

document.addEventListener("DOMContentLoaded", init);
