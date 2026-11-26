/* 按天推进：手动加料 / 按推荐加料（可锁定原料）/ 四指标同轴对比 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  running: false,
  subs: [],
  busy: false,
  last: null,
  reco: null,
  cmp: null,
  scenarios: [],
  loop: false,
  cmpMode: false,
  wsName: "",
};

const COLORS = CHART.SERIES;

// 布局与配色统一由 chart-theme.js 提供，页面只负责"画什么"
const LAYOUT = (title, ytitle, opts) => CHART.layout(Object.assign({ title, yTitle: ytitle }, opts || {}));
const CONFIG = CHART.config;

function setStatus(t, c) {
  const el = $("status");
  el.textContent = t;
  el.className = "status" + (c ? " " + c : "");
}

function syncButtons() {
  const on = state.running && !state.busy;
  $("start").disabled = state.busy || state.running;
  $("advance").disabled = !on || state.loop;
  $("auto7").disabled = !on || state.loop;
  $("btn-rec").disabled = !on || state.loop;
  $("btn-auto").disabled = !on || state.loop;
  $("btn-cmp").disabled = !on || state.loop;
  $("btn-export").disabled = !state.running;
  $("btn-loop").disabled = state.busy || !state.running;
  $("btn-loop").textContent = state.loop ? "停止自动推进" : "自动推进（每天重算）";
  $("btn-loop").classList.toggle("on", state.loop);
  $("btn-cmp-mode").disabled = !state.cmp;
  $("btn-cmp-mode").textContent = state.cmpMode ? "回到实时视图" : "切到对比视图";
  $("btn-cmp-mode").classList.toggle("on", state.cmpMode);
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

function subOptions(selected) {
  return state.subs
    .map((s) => `<option value="${s.id}" ${s.id === selected ? "selected" : ""}>${s.name}</option>`)
    .join("");
}

function addRow(id, amount, adjustable) {
  const row = document.createElement("div");
  row.className = "feed-row";
  row.innerHTML = `
    <input type="checkbox" ${adjustable === false ? "" : "checked"} title="勾选=工厂可调，不勾=按合同量锁定" />
    <select>${subOptions(id || (state.subs[0] && state.subs[0].id))}</select>
    <input type="number" step="0.5" min="0" value="${amount === undefined ? 10 : amount}" />
    <button class="btn-mini" title="移除">×</button>`;
  row.querySelector("button").addEventListener("click", () => row.remove());
  $("feeds").appendChild(row);
}

function readFeed() {
  const out = {};
  for (const row of $("feeds").querySelectorAll(".feed-row")) {
    const id = row.querySelector("select").value;
    const q = parseFloat(row.querySelector("input[type=number]").value) || 0;
    if (q > 0) out[id] = (out[id] || 0) + q;
  }
  return out;
}

/** 稳定窗口：从原 /optimize 页并过来的四个约束。 */
function readLimits() {
  return {
    ph_min: parseFloat($("lim-ph").value) || 6.8,
    vfa_tac_max: parseFloat($("lim-vfa").value) || 0.4,
    olr_max: parseFloat($("lim-olr").value) || 5.0,
    hrt_min: parseFloat($("lim-hrt").value) || 20,
  };
}

/** 勾选的 = 工厂可调；不勾的由供货决定，推荐不能动。 */
function readAdjustable() {
  const out = [];
  for (const row of $("feeds").querySelectorAll(".feed-row")) {
    const id = row.querySelector("select").value;
    if (row.querySelector("input[type=checkbox]").checked) out.push(id);
  }
  return out;
}

function writeFeed(feed, adjustableMap) {
  const keep = new Set(adjustableMap || []);
  $("feeds").innerHTML = "";
  for (const [id, q] of Object.entries(feed)) addRow(id, q, keep.size ? keep.has(id) : true);
}

/* ---------------------------------------------------------------- 底物编辑 */

/** 打开编辑框：以当前行选中的底物为模板预填。 */
function openSubEditor() {
  const row = $("feeds").querySelector(".feed-row");
  const sid = row ? row.querySelector("select").value : "";
  const base = state.subs.find((s) => s.id === sid) || state.subs[0];
  if (!base) return;
  $("sub-base").innerHTML = state.subs
    .filter((s) => !s.custom)
    .map((s) => `<option value="${s.id}" ${s.id === base.id ? "selected" : ""}>${s.name}</option>`)
    .join("");
  $("sub-name").value = `${base.name}（自定义）`;
  for (const k of ["TS", "NH4", "fRP", "fRFe", "fRF", "fRA"]) {
    const v = base.values ? base.values[k] : undefined;
    $("sub-" + k).value = v === undefined || v === null ? "" : v;
  }
  $("sub-modal").hidden = false;
}

async function saveSubstrate() {
  const values = {};
  for (const k of ["TS", "NH4", "fRP", "fRFe", "fRF", "fRA"]) {
    const raw = $("sub-" + k).value;
    if (raw !== "") values[k] = Number(raw);
  }
  const name = $("sub-name").value.trim();
  if (!name) {
    setStatus("底物名称不能为空", "err");
    return;
  }
  try {
    const j = await post("/api/substrates", {
      name,
      based_on: $("sub-base").value,
      values,
      note: "按天推进页内新建",
    });
    // 刷新底物列表，并把这行切到新建的底物
    const res = await fetch("/api/substrates").then((r) => r.json());
    state.subs = res.items;
    const row = $("feeds").querySelector(".feed-row");
    if (row) {
      const sel = row.querySelector("select");
      sel.innerHTML = subOptions(j.item.id);
      sel.value = j.item.id;
    }
    $("sub-modal").hidden = true;
    setStatus(`已保存底物「${j.item.name}」并在当前行使用。`, "ok");
  } catch (e) {
    setStatus("保存失败：" + e.message, "err");
  }
}

/* ---------------------------------------------------------------- 工作区 */

async function loadWorkspaces() {
  const j = await fetch("/api/workspaces").then((r) => r.json());
  const sel = $("ws-select");
  const items = j.items || [];
  const active = (j.active && j.active.id) || "";
  sel.innerHTML = items.length
    ? items
        .map(
          (w) =>
            `<option value="${w.id}" ${w.id === active ? "selected" : ""}>${w.name}（第 ${(w.summary && w.summary.day) || 0} 天）</option>`
        )
        .join("")
    : `<option value="">（还没有工作区，启动会话时自动新建）</option>`;
  if (active) {
    const cur = items.find((x) => x.id === active);
    state.wsName = cur ? cur.name : "";
  }
  return j;
}

/** 切工作区 = 换一条时间线：清掉前端状态，重新从磁盘载入。 */
async function switchWorkspace(id) {
  if (!id) return;
  state.busy = true;
  syncButtons();
  setStatus("正在切换工作区…", "busy");
  try {
    await post(`/api/workspaces/${encodeURIComponent(id)}/open`, {});
    const st = await fetch("/api/daily/state").then((r) => r.json());
    if (st && st.ok && st.day > 0) {
      state.running = true;
      state.last = st;
      $("feeds").innerHTML = "";
      for (const [sid, q] of Object.entries(st.feed)) addRow(sid, q);
      render(st);
      buildScenarios(st.feed);
      setStatus(`已切到工作区，第 ${st.day} 天。`, "ok");
    } else {
      state.running = false;
      state.last = null;
      state.cmp = null;
      state.cmpMode = false;
      Plotly.purge($("chart-gas"));
      Plotly.purge($("chart-ph"));
      Plotly.purge($("chart-vfa"));
      Plotly.purge($("chart-load"));
      setStatus("这个工作区还没有会话。配置好配比后点「启动会话」。", "");
    }
    await loadWorkspaces();
    if (state.wsName) $("ws-hint").textContent = `当前：${state.wsName}。切换会载入它自己的装置状态与曲线。`;
  } catch (e) {
    setStatus("切换失败：" + e.message, "err");
  } finally {
    state.busy = false;
    syncButtons();
  }
}

async function createWorkspace() {
  const name = prompt("新工作区名称", `工作区 ${new Date().toLocaleDateString("zh-CN")}`);
  if (!name) return;
  try {
    const j = await post("/api/workspaces", { name });
    await loadWorkspaces();
    await switchWorkspace(j.id);
  } catch (e) {
    setStatus("新建失败：" + e.message, "err");
  }
}

/** 跳到第 N 天：后端从初值按历史投料重放，前端只负责展示。 */
async function jumpToDay() {
  if (!state.running) return;
  const day = parseInt($("goto-day").value, 10);
  if (isNaN(day) || day < 0) {
    setStatus("填一个不小于 0 的天数", "err");
    return;
  }
  state.busy = true;
  syncButtons();
  setStatus(`正在回放到第 ${day} 天…（每天约 0.05 秒）`, "busy");
  try {
    const j = await post("/api/daily/goto", { day });
    state.last = j;
    state.cmp = null;
    state.cmpMode = false;
    render(j);
    writeFeed(j.feed, readAdjustable());
    buildScenarios(j.feed);
    setStatus(`已回到第 ${j.day} 天（罐龄 ${j.t_days.toFixed(2)} 天）。这之后的数据已丢弃。`, "ok");
  } catch (e) {
    setStatus("跳转失败：" + e.message, "err");
  } finally {
    state.busy = false;
    syncButtons();
  }
}

async function start() {
  state.busy = true;
  syncButtons();
  setStatus("正在建立装置…", "busy");
  try {
    const ids = [...new Set([...$("feeds").querySelectorAll("select")].map((s) => s.value))];
    const cur = readFeed();
    const j = await post("/api/daily/start", {
      substrate_ids: ids,
      flows: ids.map((id) => cur[id] || 1),
      v_liq: parseFloat($("vliq").value) || 1050,
      t_c: parseFloat($("tad").value) || 42,
    });
    state.running = true;
    state.last = j;
    render(j);
    buildScenarios(j.feed);
    await loadWorkspaces();
    if (state.wsName) $("ws-hint").textContent = `当前：${state.wsName}。切换会载入它自己的装置状态与曲线。`;
    setStatus("会话已启动。手动加料，或点「按推荐持续推进」。", "ok");
  } catch (e) {
    setStatus("启动失败：" + e.message, "err");
  } finally {
    state.busy = false;
    syncButtons();
  }
}

async function advance(days = 1, feed = null) {
  if (!state.running) return null;
  const f = feed || readFeed();
  if (!Object.keys(f).length) {
    setStatus("今天一种料都没加", "err");
    return null;
  }
  state.busy = true;
  syncButtons();
  try {
    let j = null;
    for (let i = 0; i < days; i++) {
      setStatus(`推进中… 第 ${i + 1}/${days} 天`, "busy");
      j = await post("/api/daily/advance", { feed: f });
      state.last = j;
      render(j);
    }
    return j;
  } finally {
    state.busy = false;
    syncButtons();
  }
}

async function onRecommendOnly() {
  state.busy = true;
  syncButtons();
  setStatus("正在从当前状态算推荐…（约 2 秒）", "busy");
  try {
    const j = await post("/api/daily/recommend", { adjustable: readAdjustable(), limits: readLimits() });
    state.reco = j;
    // 推荐结果由后端写进会话快照，这里必须重新拉一次，否则面板显示的还是上一次
    const st = await fetch("/api/daily/state").then((r) => r.json());
    if (st && st.ok) render(st);
    else render(state.last);
    const r = j.recommended;
    setStatus(
      `推荐：甲烷 ${r.q_ch4.toFixed(0)}（当前 ${j.current.q_ch4.toFixed(0)}，${j.gain_pct >= 0 ? "+" : ""}${j.gain_pct}%）｜` +
        `总量 ${r.total} ｜ OLR ${r.olr} ｜ 卡点 ${j.binding}`,
      "ok"
    );
    buildScenarios(state.last ? state.last.feed : null);
  } catch (e) {
    setStatus("推荐失败：" + e.message, "err");
  } finally {
    state.busy = false;
    syncButtons();
  }
}

async function onAutoRecommended() {
  const days = Math.max(1, Math.min(60, parseInt($("reco-days").value, 10) || 3));
  state.busy = true;
  syncButtons();
  setStatus(`按推荐推进 ${days} 天…`, "busy");
  try {
    const j = await post("/api/daily/auto", {
      days,
      reopt_every: days,
      days_per_eval: 20,
      adjustable: readAdjustable(),
      limits: readLimits(),
    });
    state.last = j;
    render(j);
    writeFeed(j.feed, readAdjustable());
    const applied = j.applied || [];
    const last = applied[applied.length - 1];
    setStatus(
      `已按推荐推进到第 ${j.day} 天（重算 ${applied.length} 次）。` +
        (last ? `最近一次推荐总量 ${last.flows.reduce((a, b) => a + b, 0).toFixed(1)}，卡点 ${last.binding}。` : ""),
      "ok"
    );
    buildScenarios(j.feed);
  } catch (e) {
    setStatus("推进失败：" + e.message, "err");
  } finally {
    state.busy = false;
    syncButtons();
  }
}

/** 自动推进：每批 N 天，批开头算一次推荐；随时可停。 */
async function toggleLoop() {
  if (state.loop) {
    state.loop = false;
    syncButtons();
    setStatus(`已停在 ${state.last ? state.last.day : "?"} 天`, "");
    return;
  }
  state.loop = true;
  syncButtons();
  const adjustable = readAdjustable();
  while (state.loop && state.running) {
    try {
      const batch = Math.max(1, Math.min(30, parseInt($("reco-days").value, 10) || 3));
      const day = state.last ? state.last.day : 0;
      setStatus(`自动推进中：第 ${day + 1}–${day + batch} 天（先算一次推荐）…`, "busy");
      const j = await post("/api/daily/auto", {
        days: batch,
        reopt_every: batch,
        days_per_eval: 20,
        adjustable,
        limits: readLimits(),
      });
      state.last = j;
      render(j);
      writeFeed(j.feed, adjustable);
      const l = j.latest;
      setStatus(
        `自动推进 · 第 ${j.day} 天 ｜ 甲烷 ${l.q_ch4.toFixed(0)} ｜ pH ${l.ph.toFixed(2)} ｜ OLR ${l.olr.toFixed(2)}`,
        l.ph < 6.8 ? "err" : "ok"
      );
    } catch (e) {
      state.loop = false;
      setStatus("自动推进中断：" + e.message, "err");
      break;
    }
    await new Promise((r) => setTimeout(r, 0));
  }
  state.loop = false;
  syncButtons();
}

/* ---------------------------------------------------------------- 对比 */

function buildScenarios(currentFeed) {
  const feed = currentFeed || (state.last ? state.last.feed : readFeed());
  if (!feed) return;
  const ids = Object.keys(feed);
  const base = ids.map((id) => feed[id] || 0);
  const list = [
    { label: "保守 −15%", flows: base.map((v) => +(v * 0.85).toFixed(2)), on: true },
    { label: "当前", flows: base.map((v) => +v.toFixed(2)), on: true },
    { label: "激进 +15%", flows: base.map((v) => +(v * 1.15).toFixed(2)), on: true },
  ];
  if (state.reco) {
    list.push({ label: "推荐", flows: state.reco.recommended.flows.map((v) => +v.toFixed(2)), on: true });
  }
  state.scenarios = list;
  renderScenarioList();
}

function shortName(id) {
  const it = state.subs.find((x) => x.id === id);
  const raw = it ? it.name : id;
  return raw.split(/[\s(（]/)[0].slice(0, 10);
}

function renderScenarioList() {
  const ids = state.last ? Object.keys(state.last.feed) : [];
  $("cmp-list").innerHTML = state.scenarios
    .map((s, i) => {
      const detail = ids.map((id, k) => `${shortName(id)} ${(s.flows[k] || 0).toFixed(1)}`).join(" · ");
      return `<label class="cmp-row">
        <input type="checkbox" data-i="${i}" ${s.on ? "checked" : ""} />
        <span class="dotc" style="background:${COLORS[i % COLORS.length]}"></span>
        <span class="cmp-name">${s.label}</span>
        <span class="cmp-detail">${detail}</span>
      </label>`;
    })
    .join("");
  $("cmp-list").querySelectorAll("input[type=checkbox]").forEach((cb) =>
    cb.addEventListener("change", () => {
      state.scenarios[parseInt(cb.dataset.i, 10)].on = cb.checked;
    })
  );
}

async function onCompare() {
  const chosen = state.scenarios.filter((s) => s.on);
  if (chosen.length < 2) {
    setStatus("至少勾选两个方案", "err");
    return;
  }
  const days = Math.max(5, Math.min(120, parseInt($("cmp-days").value, 10) || 30));
  state.busy = true;
  syncButtons();
  setStatus(`对比 ${chosen.length} 个方案，各跑 ${days} 天…`, "busy");
  try {
    const j = await post("/api/daily/compare", {
      days,
      scenarios: chosen.map((s) => ({ label: s.label, flows: s.flows })),
    });
    state.cmp = j;
    state.cmpMode = true;
    render(state.last);
    const best = [...j.scenarios].sort((a, b) => b.mean_ch4 - a.mean_ch4)[0];
    setStatus(
      `${j.scenarios.length} 个方案跑完 ${days} 天。末段甲烷最高：${best.label} ${best.mean_ch4.toFixed(0)} m³/d（OLR ${best.olr}，最低 pH ${best.min_ph}）。已切到对比视图。`,
      "ok"
    );
  } catch (e) {
    setStatus("对比失败：" + e.message, "err");
  } finally {
    state.busy = false;
    syncButtons();
  }
}

/** 对比视图：所有方案、所有指标画在同一坐标系里，靠颜色区分。 */
function drawCompare() {
  const j = state.cmp;
  if (!j) return;
  const sc = j.scenarios;
  const line = (i, key, opts) =>
    CHART.line(sc[i].series.day, sc[i].series[key], Object.assign({
      name: sc[i].label,
      color: COLORS[i % COLORS.length],
    }, opts || {}));

  Plotly.react(
    $("chart-gas"),
    [
      ...sc.map((_, i) => line(i, "q_gas", { unit: "m³/d" })),
      ...sc.map((_, i) => Object.assign(line(i, "q_ch4", { dash: "dot", unit: "m³/d" }), { showlegend: false })),
    ],
    LAYOUT("对比 · 产气（实线=沼气，点线=甲烷）", "m³/d"),
    CONFIG
  );
  Plotly.react($("chart-ph"), sc.map((_, i) => line(i, "ph")), LAYOUT("对比 · pH", "pH"), CONFIG);
  Plotly.react($("chart-vfa"), sc.map((_, i) => line(i, "vfa_tac")), LAYOUT("对比 · VFA/TAC", "-"), CONFIG);
  Plotly.react(
    $("chart-load"),
    [
      ...sc.map((_, i) => line(i, "olr", { unit: "kg VS/(m³·d)" })),
      // HRT 是几十天、OLR 是几公斤，共用一根轴会把 OLR 压成一条平线
      ...sc.map((_, i) => Object.assign(line(i, "hrt", { dash: "dot", yaxis: "y2", unit: "d" }), { showlegend: false })),
    ],
    LAYOUT("对比 · 负荷（实线=OLR，点线=HRT）", "kg VS/(m³·d)", { y2Title: "d" }),
    CONFIG
  );
}

/* ---------------------------------------------------------------- 渲染 */

function renderAlerts(r) {
  const box = $("alerts");
  const list = r.alerts || [];
  if (state.cmpMode) {
    box.className = "banner warn";
    box.textContent = "对比视图：下面的四条曲线是各方案在同一坐标系上的对照，点「回到实时视图」恢复当前运行。";
    return;
  }
  if (!list.length) {
    box.className = "banner";
    box.textContent = `运行状态：${r.alert_summary || "正常"}`;
    return;
  }
  const serious = list.some((a) => a.level === "serious");
  box.className = "banner " + (serious ? "serious" : "warn");
  const items = list
    .map((a) => `<div>${a.level === "serious" ? "⛔" : "⚠"} <b>${a.text}</b> —— ${a.advice}</div>`)
    .join("");
  box.innerHTML = `<div style="margin-bottom:4px">运行状态：${r.alert_summary}</div>${items}`;
}

function renderReco(reco, names) {
  const box = $("recpanel");
  if (!reco) {
    box.textContent = "AI 推荐：还没有计算。点左侧「只算推荐」或「自动推进」。";
    return;
  }
  const ids = reco.ids || [];
  const label = (i) => (reco.names && reco.names[i]) || (names && names[ids[i]]) || ids[i];
  const free = reco.adjustable || [];
  const rows = ids
    .map((id, i) => {
      const cur = reco.current[i];
      const rec = reco.recommended[i];
      const delta = cur > 0 ? ((rec - cur) / cur) * 100 : 0;
      const cls = delta > 0.5 ? "up" : delta < -0.5 ? "down" : "";
      const locked = free.length && !free[i];
      return `<tr>
        <td>${label(i)}${locked ? ' <span style="color:#7f8fa6">（锁定）</span>' : ""}</td>
        <td>${cur.toFixed(2)}</td>
        <td><b>${rec.toFixed(2)}</b></td>
        <td class="${cls}">${delta >= 0 ? "+" : ""}${delta.toFixed(0)}%</td>
      </tr>`;
    })
    .join("");
  const bad = (reco.violations || []).length > 0;
  const stuck = bad && Math.abs(reco.gain_pct) < 0.5;
  const advice = stuck
    ? `<div class="head down">当前已在约束边上，没有"既增气又不越限"的解：建议先把总负荷降 10–15% 回到安全区，再谈增气。</div>`
    : "";
  box.innerHTML = `
    <div class="head">
      第 ${reco.day} 天的推荐 ｜ 预测甲烷 <b>${reco.q_ch4_rec.toFixed(0)}</b> m³/d
      （当前 ${reco.q_ch4_now.toFixed(0)}，<span class="${reco.gain_pct >= 0 ? "up" : "down"}">${reco.gain_pct >= 0 ? "+" : ""}${reco.gain_pct}%</span>）
      ｜ 卡点：${reco.binding || "—"}
      ${bad ? `<span class="down"> ｜ ⚠ 推荐方案仍越限：${reco.violations.join("；")}</span>` : ""}
    </div>
    ${advice}
    <table>
      <thead><tr><th>原料</th><th>当前 m³/d</th><th>推荐 m³/d</th><th>变化</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function drawLive(s) {
  Plotly.react(
    $("chart-gas"),
    [
      CHART.line(s.day, s.q_gas, { name: "沼气", color: CHART.COLOR.gas, unit: "m³/d" }),
      CHART.line(s.day, s.q_ch4, { name: "甲烷", color: CHART.COLOR.ch4, unit: "m³/d" }),
    ],
    LAYOUT("产气速率", "m³/d"),
    CONFIG
  );
  Plotly.react(
    $("chart-ph"),
    [
      CHART.line(s.day, s.ph, { name: "pH", color: CHART.COLOR.ph }),
      CHART.limit(s.day, 6.8, { name: "酸化线 6.8" }),
    ],
    LAYOUT("pH（低于 6.8 视为酸化风险）", "pH"),
    CONFIG
  );
  Plotly.react(
    $("chart-vfa"),
    [
      CHART.line(
        s.day,
        s.vfa.map((v, i) => (s.tac[i] > 0 ? v / s.tac[i] : null)),
        { name: "VFA/TAC", color: CHART.COLOR.vfa }
      ),
      CHART.limit(s.day, 0.4, { name: "酸化风险 0.4" }),
    ],
    LAYOUT("VFA/TAC（>0.4 酸化风险）", "-"),
    CONFIG
  );
  Plotly.react(
    $("chart-load"),
    [
      CHART.line(s.day, s.olr, { name: "OLR", color: CHART.COLOR.olr, unit: "kg VS/(m³·d)" }),
      CHART.line(s.day, s.hrt, { name: "HRT", color: CHART.COLOR.hrt, dash: "dot", yaxis: "y2", unit: "d" }),
    ],
    {
      ...LAYOUT("有机负荷与停留时间", "kg VS/(m³·d)", { y2Title: "d" }),
      yaxis2: { overlaying: "y", side: "right", title: { text: "d", font: { size: 10 } }, gridcolor: "rgba(0,0,0,0)" },
    },
    CONFIG
  );
}

function render(r) {
  if (r) {
    state.last = r;
    const l = r.latest;
    if (state.wsName) {
      $("ws-hint").textContent = `工作区：${state.wsName}（第 ${r.day} 天）`;
    }
    renderAlerts(r);
    renderReco(r.recommendation, r.feed_names);
    $("k-day").textContent = r.day;
    $("k-gas").textContent = l.q_gas === null ? "—" : l.q_gas.toFixed(0);
    $("k-ch4").textContent = l.q_ch4 === null ? "—" : l.q_ch4.toFixed(0);
    $("k-ph").textContent = l.ph === null ? "—" : l.ph.toFixed(2);
    $("k-vfa").textContent = l.vfa_tac === null || l.vfa_tac === undefined ? "—" : l.vfa_tac.toFixed(3);
  }
  if (state.cmpMode && state.cmp) drawCompare();
  else if (state.last) drawLive(state.last.series);
}

$("start").addEventListener("click", start);
$("advance").addEventListener("click", async () => {
  try {
    const j = await advance(1);
    if (j) {
      const l = j.latest;
      setStatus(`第 ${j.day} 天 ｜ 沼气 ${l.q_gas.toFixed(0)} ｜ 甲烷 ${l.q_ch4.toFixed(0)} ｜ pH ${l.ph.toFixed(2)} ｜ OLR ${l.olr.toFixed(2)}`, l.ph < 6.8 ? "err" : "ok");
    }
  } catch (e) {
    setStatus("推进失败：" + e.message, "err");
  }
});
$("auto7").addEventListener("click", async () => {
  try {
    const j = await advance(7);
    if (j) setStatus(`用当前配比连跑到第 ${j.day} 天。`, "ok");
  } catch (e) {
    setStatus("推进失败：" + e.message, "err");
  }
});
$("btn-rec").addEventListener("click", onRecommendOnly);
$("btn-auto").addEventListener("click", onAutoRecommended);
$("btn-cmp").addEventListener("click", onCompare);
$("btn-loop").addEventListener("click", toggleLoop);
$("btn-cmp-mode").addEventListener("click", () => {
  if (!state.cmp) return;
  state.cmpMode = !state.cmpMode;
  render(state.last);
  syncButtons();
});
$("btn-export").addEventListener("click", () => window.open("/api/daily/export.csv", "_blank"));
$("btn-goto").addEventListener("click", jumpToDay);
$("add-row").addEventListener("click", () => addRow());
$("ws-select").addEventListener("change", (e) => switchWorkspace(e.target.value));
$("ws-new").addEventListener("click", createWorkspace);
$("ws-manage").addEventListener("click", () => window.open("/workspaces", "_blank"));
$("edit-sub").addEventListener("click", openSubEditor);
$("sub-save").addEventListener("click", saveSubstrate);
$("sub-cancel").addEventListener("click", () => {
  $("sub-modal").hidden = true;
});

(async function boot() {
  try {
    const res = await fetch("/api/substrates");
    const j = await res.json();
    state.subs = j.items;
    try {
      await loadWorkspaces();
      const hint = $("ws-hint");
      hint.textContent = state.wsName
        ? `当前：${state.wsName}。切换会载入它自己的装置状态与曲线。`
        : "还没有工作区——启动会话时会自动建一个。";
    } catch (e) {
      /* 工作区读取失败不影响下面 */
    }
    const st = await fetch("/api/daily/state").then((r) => r.json());
    if (st && st.ok && st.day > 0) {
      state.running = true;
      state.last = st;
      const free = (st.recommendation && st.recommendation.adjustable) || null;
      for (const [id, q] of Object.entries(st.feed)) addRow(id, q, free ? free[Object.keys(st.feed).indexOf(id)] : true);
      render(st);
      buildScenarios(st.feed);
      setStatus(`已恢复：第 ${st.day} 天。`, "ok");
    } else {
      addRow("maize_silage_milk_ripeness", 11.4);
      addRow("swine_manure", 6.1);
      addRow("cattle_manure", 5.0);
    }
    syncButtons();
  } catch (e) {
    setStatus("底物库读取失败：" + e.message, "err");
  }
})();
