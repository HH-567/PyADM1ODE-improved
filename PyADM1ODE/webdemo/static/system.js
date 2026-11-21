/* PyADM1ODE · 组件系统 / 三通道
 * The plant is drawn as a graph; each connection carries one of three channel
 * kinds (material / gas / energy+heat) and animates at the real flow rate.
 * Values come from plant.simulate() per tick — nothing is decorative.
 */
"use strict";

const canvas = document.getElementById("stage");
const ctx = canvas.getContext("2d");

const PAD = { l: 80, r: 80, t: 112, b: 96 };
const NODE_W = 118;
const NODE_H = 52;

const S = {
  ready: false,
  data: null,
  idx: 0,
  playing: true,
  speed: 1,
  phase: 0,
  lastTime: 0,
  edgePhase: {},
};

const log1p = Math.log1p;

function fmt(x, d = 1) {
  if (x === null || x === undefined || !isFinite(x)) return "—";
  return Number(x).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
}

async function load() {
  let data;
  try {
    const res = await fetch("/api/system?days=120");
    data = await res.json();
    if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
  } catch (err) {
    document.getElementById("loading").innerHTML = '<div style="color:#f87171">加载失败：' + err.message + "</div>";
    return;
  }

  S.data = data;
  for (const e of data.edges) S.edgePhase[e.id] = Math.random();

  document.getElementById("meta-line").textContent =
    `${data.summary.points} TICKS · ${data.summary.days} D · ${data.nodes.length} 组件 · ${data.edges.length} 连接 · 总产气 ${fmt(data.summary.total_gas_nm3, 0)} Nm³`;
  document.getElementById("v-nsteps") && (document.getElementById("v-nsteps").textContent = "");

  const scrub = document.getElementById("scrub");
  scrub.max = String(data.t.length - 1);
  scrub.value = "0";

  buildLegend();
  document.getElementById("loading").classList.add("hide");
  setTimeout(() => {
    const el = document.getElementById("loading");
    if (el) el.style.display = "none";
  }, 650);
  S.ready = true;
  requestAnimationFrame(frame);
}

function buildLegend() {
  const el = document.getElementById("legend");
  const order = ["material", "gas", "energy", "heat"];
  el.innerHTML = order
    .map((k) => {
      const c = S.data.channels[k];
      return `<div class="row"><span class="sw" style="background:${c.color}"></span>${c.label}</div>`;
    })
    .join("");
}

/* ---------------------------------------------------------------- frame */

function frame(ts) {
  const dt = S.lastTime ? Math.min(0.06, (ts - S.lastTime) / 1000) : 0.016;
  S.lastTime = ts;

  if (S.ready && S.playing) {
    S.idx += S.speed * 6 * dt; // 6 ticks per second at 1x
    if (S.idx >= S.data.t.length - 1) {
      S.idx = S.data.t.length - 1;
      setPlaying(false);
    }
    document.getElementById("scrub").value = String(Math.floor(S.idx));
  }

  S.phase += dt;
  draw();
  updateHud();
  requestAnimationFrame(frame);
}

function setPlaying(on) {
  S.playing = on;
  document.getElementById("play").textContent = on ? "❚❚" : "▶";
}

/* ---------------------------------------------------------------- draw */

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function nodePos(n, w, h) {
  return {
    x: PAD.l + n.x * (w - PAD.l - PAD.r),
    y: PAD.t + n.y * (h - PAD.t - PAD.b),
  };
}

function draw() {
  resize();
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  const d = S.data;
  const i = Math.min(d.t.length - 1, Math.floor(S.idx));

  // backdrop
  const bg = ctx.createRadialGradient(w / 2, h * 0.45, 0, w / 2, h * 0.45, Math.max(w, h) * 0.8);
  bg.addColorStop(0, "#0a1522");
  bg.addColorStop(0.55, "#050b12");
  bg.addColorStop(1, "#030507");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  drawGrid(w, h);

  const pos = {};
  for (const n of d.nodes) pos[n.id] = nodePos(n, w, h);

  // edges under nodes
  for (const e of d.edges) drawEdge(e, pos[e.from], pos[e.to], i);

  for (const n of d.nodes) drawNode(n, pos[n.id], i);

  drawScrim(w, h);
}

function drawGrid(w, h) {
  ctx.save();
  ctx.strokeStyle = "rgba(190,215,240,0.045)";
  ctx.lineWidth = 1;
  const step = 64;
  for (let x = PAD.l % step; x < w; x += step) {
    ctx.beginPath();
    ctx.moveTo(x, PAD.t - 20);
    ctx.lineTo(x, h - PAD.b + 20);
    ctx.stroke();
  }
  for (let y = PAD.t % step; y < h; y += step) {
    ctx.beginPath();
    ctx.moveTo(PAD.l - 20, y);
    ctx.lineTo(w - PAD.r + 20, y);
    ctx.stroke();
  }
  ctx.restore();
}

function edgeGeometry(a, b, bow) {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  // perpendicular offset
  const px = -dy / len;
  const py = dx / len;
  return { cx: mx + px * bow * len, cy: my + py * bow * len };
}

function drawEdge(e, a, b, i) {
  const ch = S.data.channels[e.channel];
  const value = S.data.edgeValues[e.id][i];
  const maxV = Math.max(...S.data.edgeValues[e.id].map((v) => Math.abs(v)), 1e-9);
  // log mapping so a 20 kW line still visibly flows next to a 2 000 m³/d one
  const rel = Math.log1p(Math.max(value, 0)) / Math.log1p(maxV);

  const g = edgeGeometry(a, b, e.bow);
  ctx.save();
  ctx.lineCap = "round";

  // pipe
  ctx.strokeStyle = hexA(ch.color, 0.13);
  ctx.lineWidth = 1.2 + 2.4 * rel;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.quadraticCurveTo(g.cx, g.cy, b.x, b.y);
  ctx.stroke();

  // flowing dashes
  const speed = 0.06 + 0.55 * rel;
  const off = -(S.phase * speed * 60 + S.edgePhase[e.id] * 100) % 40;
  ctx.setLineDash([5, 15]);
  ctx.lineDashOffset = off;
  ctx.strokeStyle = hexA(ch.color, 0.55 + 0.35 * rel);
  ctx.lineWidth = 1.6 + 1.6 * rel;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.quadraticCurveTo(g.cx, g.cy, b.x, b.y);
  ctx.stroke();
  ctx.setLineDash([]);

  // value label at the curve midpoint
  const mid = {
    x: 0.25 * a.x + 0.5 * g.cx + 0.25 * b.x,
    y: 0.25 * a.y + 0.5 * g.cy + 0.25 * b.y,
  };
  ctx.font = '9.5px "Cascadia Mono", Consolas, monospace';
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const label = `${fmt(value, value >= 100 ? 0 : 1)} ${e.unit}`;
  const tw = ctx.measureText(label).width;
  ctx.fillStyle = "rgba(3,6,10,0.82)";
  ctx.fillRect(mid.x - tw / 2 - 4, mid.y - 7, tw + 8, 14);
  ctx.fillStyle = hexA(ch.color, 0.95);
  ctx.fillText(label, mid.x, mid.y);
  ctx.restore();
}

function drawNode(n, p, i) {
  const x = p.x - NODE_W / 2;
  const y = p.y - NODE_H / 2;

  ctx.save();
  // plate
  ctx.fillStyle = "rgba(10,17,26,0.92)";
  ctx.strokeStyle = "rgba(190,215,240,0.28)";
  ctx.lineWidth = 1;
  roundRect(x, y, NODE_W, NODE_H, 3);
  ctx.fill();
  ctx.stroke();

  // kind tick
  const tick = { digester: "#9fe8ff", storage: "#c8e6b0", chp: "#ffd98a", heating: "#ff9e7a", source: "#8fa6c0", sink: "#8fa6c0" }[n.kind] || "#8fa6c0";
  ctx.fillStyle = tick;
  ctx.fillRect(x, y, 2.5, NODE_H);

  // label
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.font = '11px "Microsoft YaHei", sans-serif';
  ctx.fillStyle = "#dfe9f5";
  ctx.fillText(n.label, x + 12, y + 18);

  // live readout
  ctx.font = '10px "Cascadia Mono", Consolas, monospace';
  ctx.fillStyle = "rgba(190,215,240,0.75)";
  ctx.fillText(nodeReadout(n.id, i), x + 12, y + 36);
  ctx.restore();
}

function nodeReadout(id, i) {
  const vals = S.data.nodeValues[id];
  if (!vals) return "—";
  if (vals.Q_feed) return `${fmt(vals.Q_feed[i], 1)} m³/d`;
  if (vals.Q_gas_supplied_m3_per_day) return `→ CHP ${fmt(vals.Q_gas_supplied_m3_per_day[i], 0)} m³/d`;
  if (vals.Q_gas) return `${fmt(vals.Q_gas[i], 0)} m³/d · pH ${fmt(vals.pH[i], 2)}`;
  if (vals.P_el && vals.Q_gas_consumed) {
    return `${fmt(vals.P_el[i], 1)} kW · ${fmt(vals.Q_gas_consumed[i], 0)} m³/d`;
  }
  if (vals.P_el) return `${fmt(vals.P_el[i], 1)} kW`;
  if (vals.Q_heat_supplied) return `${fmt(vals.Q_heat_supplied[i], 1)} kW`;
  return "—";
}

function roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function drawScrim(w, h) {
  const top = ctx.createLinearGradient(0, 0, 0, 120);
  top.addColorStop(0, "rgba(3,6,10,0.92)");
  top.addColorStop(1, "rgba(3,6,10,0)");
  ctx.fillStyle = top;
  ctx.fillRect(0, 0, w, 120);

  const bot = ctx.createLinearGradient(0, h - 110, 0, h);
  bot.addColorStop(0, "rgba(3,6,10,0)");
  bot.addColorStop(1, "rgba(3,6,10,0.92)");
  ctx.fillStyle = bot;
  ctx.fillRect(0, h - 110, w, 110);
}

function hexA(hex, a) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${a})`;
}

/* ---------------------------------------------------------------- hud */

let hudTick = 0;

function updateHud() {
  if (performance.now() - hudTick < 100) return;
  hudTick = performance.now();
  const d = S.data;
  const i = Math.min(d.t.length - 1, Math.floor(S.idx));

  const gas1 = d.edgeValues["e_gas1"][i];
  const gas2 = d.edgeValues["e_gas2"][i];
  const pEl = d.edgeValues["e_el"][i];

  document.getElementById("kpis").innerHTML = `
    <div><span class="k">时间</span><span class="v">${fmt(d.t[i], 1)}</span><span class="u">d</span></div>
    <div><span class="k">总产气</span><span class="v">${fmt(gas1 + gas2, 0)}</span><span class="u">m³/d</span></div>
    <div><span class="k">发电</span><span class="v">${fmt(pEl, 1)}</span><span class="u">kW</span></div>`;
  document.getElementById("clock").textContent = fmt(d.t[i], 1) + " d";
}

/* ---------------------------------------------------------------- controls */

function bind() {
  document.getElementById("play").addEventListener("click", () => {
    if (S.idx >= S.data.t.length - 1) S.idx = 0;
    setPlaying(!S.playing);
  });
  document.getElementById("speed").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-s]");
    if (!b) return;
    S.speed = parseFloat(b.dataset.s);
    [...document.querySelectorAll("#speed button")].forEach((x) => x.classList.toggle("on", x === b));
  });
  document.getElementById("scrub").addEventListener("input", (e) => {
    S.idx = parseFloat(e.target.value);
  });
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space") {
      e.preventDefault();
      if (S.idx >= S.data.t.length - 1) S.idx = 0;
      setPlaying(!S.playing);
    }
  });
  window.addEventListener("resize", () => {
    if (S.ready) draw();
  });
}

bind();
load();
