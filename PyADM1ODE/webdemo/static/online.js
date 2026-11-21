/* 在线更正页面：逐条量测推进，状态更正与参数更正分开显示 */
"use strict";

const $ = (id) => document.getElementById(id);

const LAYOUT = (extra = {}) => {
  // title 要从 extra 里摘出来单独构造，不能靠后面的 ...extra 覆盖，
  // 否则 Plotly 收到的是一个字符串而不是 title 对象。
  const { title, yaxis, margin, ...rest } = extra;
  return {
    title: title
      ? {
          text: title,
          x: 0.012,
          xanchor: "left",
          y: 0.97,
          yanchor: "top",
          font: { size: 11.5, color: "#8b95a5" },
        }
      : undefined,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#8b95a5", size: 11, family: '"Cascadia Mono", Consolas, monospace' },
    margin: margin || { l: 56, r: 16, t: title ? 34 : 16, b: 34 },
    xaxis: { gridcolor: "#1b2028", zeroline: false, title: { text: "时间 [h]", font: { size: 10 } } },
    yaxis: { gridcolor: "#1b2028", zeroline: false, ...(yaxis || {}) },
    legend: { orientation: "h", y: 1.18, x: 0, font: { size: 10 } },
    hovermode: "x unified",
    ...rest,
  };
};

const CONFIG = { displayModeBar: false, responsive: true };

const state = { running: false, last: null, auto: false, pending: false };

function setStatus(text, cls) {
  const el = $("status");
  el.textContent = text;
  el.className = "status" + (cls ? " " + cls : "");
}

/** 按钮可用性集中在一处算，自动连播时只锁「启动」和手动推进。 */
function syncButtons() {
  const busy = state.pending;
  $("start").disabled = busy || state.running;
  $("auto").disabled = busy || !state.running;
  $("step1").disabled = busy || !state.running || state.auto;
  $("step6").disabled = busy || !state.running || state.auto;
  $("drift").disabled = busy || !state.running || state.auto;
  $("stop").disabled = busy || !state.running;
  $("auto").textContent = state.auto ? "暂停自动" : "自动运行";
  $("auto").classList.toggle("on", state.auto);
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

function brief(j) {
  const err = j.state_error === null ? "—" : (j.state_error * 100).toFixed(1) + "%";
  const feed = j.feed_est[0] === null ? "—" : j.feed_est[0].toFixed(1);
  return `t = ${j.t.toFixed(0)} h ｜ 误差 ${err} ｜ 进料 ${feed} / ${j.feed_true[0].toFixed(1)}`;
}

async function start() {
  state.pending = true;
  syncButtons();
  setStatus("正在建立装置并预热 30 天…（约 10 秒）", "busy");
  try {
    const j = await post("/api/online/start", {
      dt: parseFloat($("dt").value) || 1,
      prior_scale: parseFloat($("prior").value) || 0.6,
      noise: parseFloat($("noise").value) || 0.02,
      calib_every: parseInt($("every").value, 10),
    });
    state.running = true;
    state.last = j;
    render(j);
    setStatus("会话已启动。点「自动运行」看它一条条更正，或手动推进。", "ok");
  } catch (e) {
    setStatus("启动失败：" + e.message, "err");
  } finally {
    state.pending = false;
    syncButtons();
  }
}

async function stepOnce() {
  const j = await post("/api/online/step", { steps: 1 });
  state.last = j;
  render(j);
  return j;
}

async function step(n) {
  if (!state.running || state.auto) return;
  state.pending = true;
  syncButtons();
  setStatus(`推进 ${n} 小时…每条量测约 6 秒，请稍候`, "busy");
  try {
    const j = await post("/api/online/step", { steps: n });
    state.last = j;
    render(j);
    const cost = j.steps ? j.cost_ms / j.ran : 0;
    setStatus(`${brief(j)} ｜ 单步 ${(cost / 1000).toFixed(1)} s`, "ok");
  } catch (e) {
    setStatus("推进失败：" + e.message, "err");
  } finally {
    state.pending = false;
    syncButtons();
  }
}

/** 自动连播：一次一小时，边跑边重绘；点「暂停自动」或结束会话即停。 */
async function toggleAuto() {
  if (state.auto) {
    state.auto = false;
    syncButtons();
    setStatus(`已暂停在 t = ${state.last ? state.last.t.toFixed(0) : "?"} h`, "");
    return;
  }
  if (!state.running) return;

  state.auto = true;
  syncButtons();

  while (state.auto && state.running) {
    try {
      const j = await stepOnce();
      const cost = j.ran ? j.cost_ms / j.ran / 1000 : 0;
      setStatus(`自动运行中 · ${brief(j)} ｜ 单步 ${cost.toFixed(1)} s`, "busy");
    } catch (e) {
      state.auto = false;
      setStatus("自动运行中断：" + e.message, "err");
      break;
    }
    // 给浏览器一帧时间重绘，否则连续请求会把画面卡住
    await new Promise((r) => setTimeout(r, 250));
  }

  state.auto = false;
  syncButtons();
}

/** 制造参数漂移：真值参数乘一个倍数，滤波器仍拿着旧值。 */
async function drift() {
  if (!state.running || state.auto) return;
  state.pending = true;
  syncButtons();
  setStatus("正在把真值参数拉开…", "busy");
  try {
    const j = await post("/api/online/perturb", { factor: 1.3 });
    state.last = j;
    render(j);
    const last = j.events[j.events.length - 1];
    const c = last && last.changed;
    setStatus(
      c
        ? `真值 ${c.param} 已漂移：${c.before.toFixed(3)} → ${c.after.toFixed(3)}。等下一个检查点看判据怎么说。`
        : "已漂移",
      "ok"
    );
  } catch (e) {
    setStatus("漂移失败：" + e.message, "err");
  } finally {
    state.pending = false;
    syncButtons();
  }
}

async function stop() {
  state.auto = false;
  state.pending = true;
  syncButtons();
  try {
    await post("/api/online/stop");
  } catch (e) {
    /* 停不下来也无所谓，下一次启动会覆盖 */
  }
  state.running = false;
  state.last = null;
  state.pending = false;
  syncButtons();
  setStatus("会话已结束。可以改设置重新启动。", "");
  for (const id of ["k-time", "k-err", "k-nis", "k-feed", "k-param", "k-cost"]) $(id).textContent = "—";
  for (const id of ["chart-err", "chart-nis", "chart-feed"]) Plotly.purge($(id));
  $("events").innerHTML = "";
}

function render(r) {
  $("k-time").textContent = r.t.toFixed(0);
  $("k-err").textContent = r.state_error === null ? "—" : (r.state_error * 100).toFixed(2);
  $("k-nis").textContent = r.nis === null || !isFinite(r.nis) ? "—" : r.nis.toFixed(2);
  const fe = r.feed_est;
  $("k-feed").textContent =
    fe[0] === null ? "—" : `${fe[0].toFixed(1)} / ${r.feed_true[0].toFixed(1)}`;
  $("k-param-name").textContent = r.param.name;
  $("k-param").textContent = r.param.value.toFixed(3);
  $("k-cost").textContent = r.ran ? ((r.cost_ms / r.ran) / 1000).toFixed(1) : "—";

  const s = r.series;

  Plotly.react(
    $("chart-err"),
    [{
      x: s.t, y: s.err.map((v) => (isFinite(v) ? v * 100 : null)),
      type: "scatter", mode: "lines", name: "状态中位误差",
      line: { color: "#34d3c8", width: 2 }, connectgaps: false,
    }],
    LAYOUT({
      title: "状态中位误差（越小越好）",
      yaxis: { title: { text: "%", font: { size: 10 } }, rangemode: "tozero" },
    }),
    CONFIG
  );

  Plotly.react(
    $("chart-nis"),
    [
      {
        x: s.t, y: s.nis.map((v) => (isFinite(v) ? v : null)),
        type: "scatter", mode: "lines", name: "NIS",
        line: { color: "#f2b344", width: 2 }, connectgaps: false,
      },
      {
        x: s.t, y: s.t.map(() => 1), type: "scatter", mode: "lines",
        name: "理想值 1", line: { color: "#4a5568", width: 1, dash: "dot" },
      },
    ],
    LAYOUT({ title: "NIS（新息一致性，越接近 1 越合理）" }),
    CONFIG
  );

  Plotly.react(
    $("chart-feed"),
    [
      { x: s.t, y: s.feed0, type: "scatter", mode: "lines", name: "玉米青贮（估计）", line: { color: "#34d3c8", width: 2 } },
      { x: s.t, y: s.truth0, type: "scatter", mode: "lines", name: "玉米青贮（真值）", line: { color: "#34d3c8", width: 1, dash: "dot" } },
      { x: s.t, y: s.feed1, type: "scatter", mode: "lines", name: "猪粪（估计）", line: { color: "#f2b344", width: 2 } },
      { x: s.t, y: s.truth1, type: "scatter", mode: "lines", name: "猪粪（真值）", line: { color: "#f2b344", width: 1, dash: "dot" } },
    ],
    LAYOUT({ title: "进料估计 vs 真值", yaxis: { title: { text: "m³/d", font: { size: 10 } } } }),
    CONFIG
  );

  renderEvents(r.events);

  // 首次渲染时容器可能还没拿到高度，画布会被定死；渲染后补一次 resize。
  requestAnimationFrame(() => {
    for (const id of ["chart-err", "chart-nis", "chart-feed"]) {
      const el = $(id);
      if (el && el.data) Plotly.Plots.resize(el);
    }
  });
}

function renderEvents(events) {
  const box = $("events");
  if (!events || !events.length) {
    box.innerHTML = '<div class="hint">还没有到检查点（每 N 条量测查一次）。</div>';
    return;
  }
  const rows = events
    .slice()
    .reverse()
    .map((e) => {
      const kind = e.kind || (e.changed ? "calibrated" : "trigger");
      let verdict;
      if (kind === "perturb") {
        verdict = `<b class="warn">人工漂移</b> 真值 ${e.changed.param}: ${e.changed.before.toFixed(3)} → ${e.changed.after.toFixed(3)}`;
      } else if (kind === "calibrated") {
        verdict = `<b class="ok">已更正</b> ${e.changed.param}: ${e.changed.before.toFixed(3)} → ${e.changed.after.toFixed(3)}`;
      } else {
        verdict = `<b class="muted">未更正</b> ${e.reason}`;
      }
      return `<tr><td>${e.t.toFixed(0)} h</td><td>${verdict}</td><td>${e.ms ? e.ms.toFixed(0) + " ms" : "—"}</td></tr>`;
    })
    .join("");
  box.innerHTML = `<table class="ev"><thead><tr><th>时刻</th><th>触发判据结果</th><th>耗时</th></tr></thead><tbody>${rows}</tbody></table>`;
}

$("start").addEventListener("click", start);
$("auto").addEventListener("click", toggleAuto);
$("step1").addEventListener("click", () => step(1));
$("step6").addEventListener("click", () => step(6));
$("drift").addEventListener("click", drift);
$("stop").addEventListener("click", stop);

syncButtons();

// 刷新后恢复进行中的会话，避免视图被清空
(async function restore() {
  try {
    const res = await fetch("/api/online/state");
    const j = await res.json();
    if (j.active && j.snapshot) {
      state.running = true;
      state.last = j.snapshot;
      render(j.snapshot);
      syncButtons();
      setStatus(`已恢复进行中的会话：${brief(j.snapshot)}`, "ok");
    }
  } catch (e) {
    /* 没有会话就当没发生 */
  }
})();
