/* PyADM1ODE · 三通道分步
 *
 * Three integrators solve the same ADM1 problem. Each column replays that
 * solver's own step sequence — same simulation time, different step counts and
 * different step sizes. That contrast is the point of the page.
 *
 * Visual language follows HALOFLUX (Ryder191, noncommercial): monochrome light
 * on black, dense micro-panel band, streaky beams. Code is original.
 */
"use strict";

const canvas = document.getElementById("stage");
const ctx = canvas.getContext("2d");

const NSTATE = 41;
const DAYS_PER_SEC = 6; // playback speed at 1x
const TRANSIENT_STEPS = 60;

const S = {
  ready: false,
  channels: [],
  tMin: 0,
  tMax: 1,
  t: 0,
  playing: true,
  speed: 1,
  beat: 0,
  rot: 0,
  events: [],
  strips: [],
};

const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);

/** The 41 ADM1da state names, same order as the engine view. */
const STATE_NAMES = [
  "S_su", "S_aa", "S_fa", "S_va", "S_bu", "S_pro", "S_ac", "S_h2", "S_ch4", "S_co2", "S_nh4", "S_I",
  "XPS_ch", "XPS_pr", "XPS_li", "XPF_ch", "XPF_pr", "XPF_li", "XS_ch", "XS_pr", "XS_li", "X_I",
  "X_su", "X_aa", "X_fa", "X_c4", "X_pro", "X_ac", "X_h2",
  "S_cat", "S_an", "S_va-", "S_bu-", "S_pr-", "S_ac-", "S_hco3", "S_nh3",
  "p_H2", "p_CH4", "p_CO2", "p_TOT",
];

/** Compact value for the card: 3 significant digits either side of 1. */
function fmtState(v) {
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 1) return v.toFixed(2);
  if (a >= 0.001) return v.toFixed(3);
  return v.toExponential(1);
}

function quantile(sortedArr, q) {
  if (!sortedArr.length) return 0;
  const pos = (sortedArr.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  return base + 1 < sortedArr.length
    ? sortedArr[base] + rest * (sortedArr[base + 1] - sortedArr[base])
    : sortedArr[base];
}

function fmt(x, d = 1) {
  if (x === null || x === undefined || !isFinite(x)) return "—";
  return Number(x).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
}

/* ---------------------------------------------------------------- load */

async function load() {
  const body = {
    backend: "scipy",
    duration: 150,
    dt: 1.0,
    temperature_c: 42.0,
    v_liq: 1050.0,
    v_gas: 150.0,
    k_l_a: 200.0,
    feed: [
      { substrate_id: "maize_silage_milk_ripeness", q: 11.4 },
      { substrate_id: "swine_manure", q: 6.1 },
      { substrate_id: "cattle_manure", q: 5.0 },
    ],
    temp_step_day: 40,
    temp_step_delta_c: -5,
    max_points: 900,
  };

  let data;
  try {
    const res = await fetch("/api/traces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    data = await res.json();
    if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
  } catch (err) {
    document.getElementById("loading").innerHTML =
      '<div style="color:#f87171">记录失败：' + err.message + "</div>";
    return;
  }

  S.channels = data.channels.map((c) => {
    const n = c.n_steps;
    const ch = {
      label: c.label,
      n,
      nRaw: c.n_raw_steps,
      t: Float32Array.from(c.t),
      h: Float32Array.from(c.h),
      gas: Float32Array.from(c.gas),
      ch4: Float32Array.from(c.ch4),
      ph: Float32Array.from(c.ph),
      y: Float32Array.from(c.y),
      du: Float32Array.from(c.du),
      yLo: new Float32Array(NSTATE),
      yHi: new Float32Array(NSTATE),
      duLo: new Float32Array(NSTATE),
      duHi: new Float32Array(NSTATE),
      ptr: 0,
    };
    computeBounds(ch);
    return ch;
  });
  S.events = data.events || [];
  S.tMin = S.channels[0].t[0];
  S.tMax = S.channels[0].t[S.channels[0].n - 1];

  buildStrips();
  buildChannelHeaders();

  document.getElementById("meta-line").textContent =
    `同一 ADM1da 方程 · 同容差 rtol=1e-6 · 三条通道各自选步 · ${fmt(S.tMax, 0)} 天`;
  document.getElementById("scrub").value = "0";

  document.getElementById("loading").classList.add("hide");
  setTimeout(() => {
    const el = document.getElementById("loading");
    if (el) el.style.display = "none";
  }, 650);
  S.ready = true;

  const atT = parseFloat(new URLSearchParams(location.search).get("t"));
  if (isFinite(atT)) {
    S.t = Math.min(S.tMax, Math.max(S.tMin, atT));
    setPlaying(false);
  }

  requestAnimationFrame(frame);
}

function computeBounds(ch) {
  for (let c = 0; c < NSTATE; c++) {
    const ys = new Float32Array(ch.n);
    const ds = new Float32Array(ch.n);
    for (let i = 0; i < ch.n; i++) {
      ys[i] = ch.y[i * NSTATE + c];
      ds[i] = Math.abs(ch.du[i * NSTATE + c]);
    }
    ys.sort();
    ds.sort();
    ch.yLo[c] = quantile(ys, 0.05);
    ch.yHi[c] = Math.max(quantile(ys, 0.95), ch.yLo[c] + 1e-9);
    ch.duLo[c] = 0;
    ch.duHi[c] = Math.max(quantile(ds, 0.95), quantile(ds, 0.5) * 1.2 + 1e-9);
  }

  // Step-size scale for the lamp band, anchored on the median: one 1e-6
  // start-up step would otherwise stretch the log range over six decades and
  // make every lamp the same colour.
  const hs = [];
  for (let i = 0; i < ch.n; i++) if (ch.h[i] > 0) hs.push(ch.h[i]);
  hs.sort((a, b) => a - b);
  ch.hMed = hs.length ? hs[hs.length >> 1] : 1e-2;
  let duSum95 = 0;
  {
    const sums = [];
    for (let i = 0; i < ch.n; i++) {
      let acc = 0;
      for (let c = 0; c < NSTATE; c++) acc += Math.abs(ch.du[i * NSTATE + c]);
      sums.push(acc);
    }
    sums.sort((a, b) => a - b);
    duSum95 = sums.length ? sums[Math.floor(sums.length * 0.95)] : 1;
  }
  ch.duSum95 = Math.max(duSum95, 1e-9);
  ch.waves = [];
}

/* Cumulative-steps curves, baked once per channel.
 * x is *simulation time* and y is *steps taken*, so the three curves line up
 * and their slopes say directly which solver is working hardest where.
 * The y axis is shared across channels, so a solver that takes twice the steps
 * climbs twice as fast. */
function buildStrips() {
  const W = 800;
  const H = 60;
  const span = Math.max(1e-9, S.tMax - S.tMin);
  const maxSteps = Math.max(...S.channels.map((c) => c.nRaw), 1);

  S.strips = S.channels.map((ch) => {
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    const g = c.getContext("2d");

    g.strokeStyle = "rgba(190,215,240,0.10)";
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(0, H - 1);
    g.lineTo(W, H - 1);
    g.stroke();

    g.beginPath();
    for (let i = 0; i < ch.n; i++) {
      const x = ((ch.t[i] - S.tMin) / span) * (W - 1);
      const steps = (i / Math.max(1, ch.n - 1)) * ch.nRaw;
      const y = H - 3 - (steps / maxSteps) * (H - 10);
      if (i === 0) g.moveTo(x, y);
      else g.lineTo(x, y);
    }
    g.strokeStyle = "rgba(223,233,245,0.9)";
    g.lineWidth = 1.4;
    g.stroke();

    // fill under the curve, faintly
    g.lineTo(W - 1, H - 1);
    g.lineTo(0, H - 1);
    g.closePath();
    g.fillStyle = "rgba(223,233,245,0.10)";
    g.fill();

    return c;
  });
}

function buildChannelHeaders() {
  const el = document.getElementById("heads");
  el.innerHTML = S.channels
    .map(
      (c, i) =>
        `<div class="head"><div class="nm">${c.label}</div>` +
        `<div class="kv">` +
        `<span>GAS<b id="h-gas-${i}">—</b>m³/d</span>` +
        `<span>pH<b id="h-ph-${i}">—</b></span>` +
        `<span>CH₄<b id="h-ch4-${i}">—</b>%</span>` +
        `</div>` +
        `<div class="st"><span id="h-steps-${i}">—</span> / ${c.n} 回放 · 实算 ${c.nRaw} 步` +
        ` · h <span id="h-h-${i}">—</span></div></div>`
    )
    .join("");
}

/* ---------------------------------------------------------------- playback */

let lastTime = 0;

function frame(ts) {
  const dt = lastTime ? Math.min(0.06, (ts - lastTime) / 1000) : 0.016;
  lastTime = ts;

  if (S.ready && S.playing) {
    S.t += S.speed * DAYS_PER_SEC * dt;
    if (S.t >= S.tMax) {
      S.t = S.tMax;
      setPlaying(false);
    }
    const span = Math.max(1, S.tMax - S.tMin);
    document.getElementById("scrub").value = String(
      Math.round(((S.t - S.tMin) / span) * 1000)
    );
  }

  for (const ch of S.channels) advance(ch, S.t);

  const lead = S.channels[0];
  const li = lead ? lead.ptr : 0;
  if (li !== S.lastIdx) {
    S.beat = Math.max(S.beat, 0.45);
    S.lastIdx = li;
  }
  S.beat *= Math.exp(-dt * 5.0);
  S.rot += dt * (0.05 + 0.9 * S.beat);

  for (const ch of S.channels) {
    if (!ch.waves || !ch.waves.length) continue;
    for (const w of ch.waves) {
      w.r += w.v * dt;
      w.life -= dt;
    }
    ch.waves = ch.waves.filter((w) => w.life > 0);
  }

  draw(ts);
  updateHud();
  requestAnimationFrame(frame);
}

S.lastIdx = -1;

function advance(ch, t) {
  let p = ch.ptr;
  while (p + 1 < ch.n && ch.t[p + 1] <= t) p++;
  while (p > 0 && ch.t[p] > t) p--;
  if (p !== ch.ptr) spawnWave(ch, p);
  ch.ptr = p;
}

/** One shockwave per step: speed from the step size, amplitude from |dy/dt|. */
function spawnWave(ch, idx) {
  const h = Math.max(ch.h[idx], 1e-12);
  const lo = Math.log10(ch.hMed * 4);
  const hi = Math.log10(ch.hMed / 12);
  const u = clamp01((lo - Math.log10(h)) / Math.max(1e-6, lo - hi));
  let e = 0;
  for (let c = 0; c < NSTATE; c++) e += Math.abs(ch.du[idx * NSTATE + c]);
  e = clamp01(e / Math.max(ch.duSum95, 1e-9));
  if (!ch.waves) ch.waves = [];
  ch.waves.push({
    r: 0,
    v: 0.35 + 0.9 * u,
    amp: 0.25 + 0.8 * e,
    life: 0.55 + 0.35 * (1 - u),
  });
  if (ch.waves.length > 10) ch.waves.shift();
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

function draw(ts) {
  resize();
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;

  const bg = ctx.createRadialGradient(w / 2, h * 0.44, 0, w / 2, h * 0.44, Math.max(w, h) * 0.8);
  bg.addColorStop(0, "#0a1522");
  bg.addColorStop(0.5, "#050b12");
  bg.addColorStop(1, "#030507");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  const colW = w / 3;
  S.channels.forEach((ch, i) => {
    const cx = colW * (i + 0.5);
    const cy = h * 0.44;
    const R = Math.min(colW * 0.40, h * 0.30);
    drawChannel(ch, cx, cy, R, colW, h, i);
  });

  drawScrim(w, h);
  drawColumns(w, h);
}

function drawColumns(w, h) {
  ctx.save();
  ctx.strokeStyle = "rgba(190,215,240,0.10)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 3; i++) {
    const x = (w / 3) * i;
    ctx.beginPath();
    ctx.moveTo(x, 96);
    ctx.lineTo(x, h - 110);
    ctx.stroke();
  }
  ctx.restore();
}

function drawChannel(ch, cx, cy, R, colW, h, index) {
  const rIn = R * 0.60;
  const bandW = R * 0.24;
  const idx = ch.ptr;

  // beams
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  for (let k = 0; k < 60; k++) {
    const a = ((k * 2.39996) % (Math.PI * 2)) + S.rot * 0.3;
    const r1 = rIn + bandW + 6;
    const r2 = r1 + R * (0.05 + 0.30 * frac(k * 3.1)) * (0.7 + 0.5 * S.beat);
    ctx.strokeStyle = `rgba(200,224,248,${0.02 + 0.03 * frac(k * 1.7)})`;
    ctx.lineWidth = 0.6;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
    ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
    ctx.stroke();
  }
  for (let c = 0; c < NSTATE; c++) {
    const v = clamp01((Math.abs(ch.du[idx * NSTATE + c]) - ch.duLo[c]) / (ch.duHi[c] - ch.duLo[c]));
    if (v <= 0.01) continue;
    const base = -Math.PI / 2 + S.rot + (c * Math.PI * 2) / NSTATE;
    for (let k = 0; k < 3; k++) {
      const a = base + (frac(c * 7.1 + k * 3.3) - 0.5) * 0.03;
      const len = R * 0.42 * Math.pow(v, 0.55) * (0.3 + 1.3 * frac(c * 3.7 + k * 9.1)) * (0.8 + 0.5 * S.beat);
      const r1 = rIn + bandW + 4 + k * 2;
      const r2 = r1 + len;
      ctx.strokeStyle =
        k === 0 && v > 0.4
          ? `rgba(255,255,255,${0.10 + 0.4 * v})`
          : `rgba(190,220,250,${0.04 + 0.14 * v})`;
      ctx.lineWidth = k === 0 ? 1.2 + 1.4 * v : 0.6;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
      ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
      ctx.stroke();
    }
  }
  ctx.restore();

  // micro-panel ring
  const seg = (Math.PI * 2) / NSTATE;
  const rMid = rIn + bandW / 2;
  const pad = seg * 0.18;
  for (let c = 0; c < NSTATE; c++) {
    const a0 = -Math.PI / 2 + S.rot + c * seg + pad;
    const a1 = a0 + seg - pad * 2;
    const v = clamp01((ch.y[idx * NSTATE + c] - ch.yLo[c]) / (ch.yHi[c] - ch.yLo[c]));

    ctx.strokeStyle = `rgba(190,215,240,${0.05 + 0.09 * v})`;
    ctx.lineWidth = bandW;
    ctx.beginPath();
    ctx.arc(cx, cy, rMid, a0, a1);
    ctx.stroke();

    ctx.strokeStyle = `rgba(210,232,255,${0.16 + 0.22 * v})`;
    ctx.lineWidth = 0.7;
    for (const re of [rIn + 1, rIn + bandW - 1]) {
      ctx.beginPath();
      ctx.arc(cx, cy, re, a0, a1);
      ctx.stroke();
    }

    // Three bars = this state's |dy/dt| now, one step ago, two steps ago.
    // They used to be hash noise scaled by the value, which looked like
    // structure without encoding any.
    for (let row = 0; row < 3; row++) {
      const s = Math.max(0, idx - row);
      const d = Math.abs(ch.du[s * NSTATE + c]);
      const vv = clamp01((d - ch.duLo[c]) / Math.max(1e-12, ch.duHi[c] - ch.duLo[c]));
      const r = rIn + bandW * (0.22 + (row * 0.56) / 2);
      const len = (a1 - a0) * (0.18 + 0.78 * Math.pow(vv, 0.7));
      ctx.strokeStyle = `rgba(232,244,255,${0.1 + 0.55 * vv})`;
      ctx.lineWidth = row === 0 ? 1.4 : 0.7;
      ctx.beginPath();
      ctx.arc(cx, cy, r, a0 + (a1 - a0) * 0.06, a0 + (a1 - a0) * 0.06 + len);
      ctx.stroke();
    }
  }

  // Cards: opaque plate + state name + value + a short |dy/dt| strip.
  // Drawn on a dark plate for the same reason /engine does: text over the
  // band is unreadable.
  {
    const cardW = ((Math.PI * 2 * rMid) / NSTATE) * 0.9;
    const cardH = bandW * 0.94;
    ctx.save();
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let c = 0; c < NSTATE; c++) {
      const a = -Math.PI / 2 + S.rot + (c + 0.5) * seg;
      const yv = ch.y[idx * NSTATE + c];
      ctx.save();
      ctx.translate(cx + Math.cos(a) * rMid, cy + Math.sin(a) * rMid);
      ctx.rotate(a + Math.PI / 2);

      ctx.fillStyle = "rgba(7, 9, 13, 0.93)";
      ctx.fillRect(-cardW / 2, -cardH / 2, cardW, cardH);
      ctx.fillStyle = "rgba(120, 190, 230, 0.55)";
      ctx.fillRect(-cardW / 2, -cardH / 2, 1.2, cardH);

      ctx.font = '6px "Cascadia Mono",Consolas,monospace';
      ctx.fillStyle = "rgba(150,168,190,0.95)";
      ctx.fillText(STATE_NAMES[c], 0, -cardH / 2 + 8);

      ctx.font = 'bold 7px "Cascadia Mono",Consolas,monospace';
      ctx.fillStyle = "rgba(240,246,252,0.96)";
      ctx.fillText(fmtState(yv), 0, -cardH / 2 + 19);

      // five blocks: this state's |dy/dt| over the last five steps
      const bw = (cardW - 4) / 5;
      for (let k = 0; k < 5; k++) {
        const s = Math.max(0, idx - (4 - k));
        const d = Math.abs(ch.du[s * NSTATE + c]);
        const vv = clamp01((d - ch.duLo[c]) / Math.max(1e-12, ch.duHi[c] - ch.duLo[c]));
        ctx.fillStyle = `rgba(220,232,246,${0.12 + 0.68 * vv})`;
        ctx.fillRect(-cardW / 2 + 2 + k * bw, cardH / 2 - 8, Math.max(1, bw - 0.8), 5);
      }
      ctx.restore();
    }
    ctx.restore();
  }

  // rim ticks
  ctx.save();
  const rOut = rIn + bandW;

  // 60 lamps: one per recent step, colour = that step's size
  {
    const nL = 60;
    const lampR = rOut + 15;
    const lo = Math.log10(ch.hMed * 4);
    const hi = Math.log10(ch.hMed / 12);
    for (let k = 0; k < nL; k++) {
      const s = Math.max(0, idx - k);
      const hh = Math.max(ch.h[s], 1e-12);
      const u = clamp01((lo - Math.log10(hh)) / Math.max(1e-6, lo - hi));
      const rr = Math.round(120 + 135 * (1 - u));
      const gg = Math.round(200 - 60 * (1 - u));
      const bb = Math.round(245 - 150 * (1 - u));
      const a = -Math.PI / 2 + S.rot - (k / nL) * Math.PI * 2;
      ctx.save();
      ctx.translate(cx + Math.cos(a) * lampR, cy + Math.sin(a) * lampR);
      ctx.rotate(a + Math.PI / 2);
      ctx.fillStyle = "rgba(4,6,10,0.85)";
      ctx.fillRect(-3.5, -3.5, 7, 7);
      ctx.fillStyle = `rgba(${rr},${gg},${bb},${0.3 + 0.6 * (1 - k / nL)})`;
      ctx.fillRect(-2.5, -2.5, 5, 5);
      ctx.restore();
    }
  }

  // shockwaves: expand outward, fade
  if (ch.waves && ch.waves.length) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const w of ch.waves) {
      const rr = rOut + 10 + w.r * R * 1.1;
      const fade = Math.min(1, w.life / 0.6);
      if (fade <= 0.02) continue;
      ctx.strokeStyle = `rgba(255,255,255,${0.5 * w.amp * fade})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, rr, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
  }
  for (let k = 0; k < 120; k++) {
    const a = (k / 120) * Math.PI * 2 + S.rot;
    const long = k % 5 === 0;
    ctx.strokeStyle = long ? "rgba(200,225,255,0.35)" : "rgba(200,225,255,0.12)";
    ctx.lineWidth = 0.6;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * (rOut + 1), cy + Math.sin(a) * (rOut + 1));
    ctx.lineTo(cx + Math.cos(a) * (rOut + (long ? 8 : 4)), cy + Math.sin(a) * (rOut + (long ? 8 : 4)));
    ctx.stroke();
  }
  ctx.restore();

  // core
  const gas = ch.gas[idx];
  const inten = clamp01(gas / 2200);
  const rc = R * 0.17 * (1 + 0.1 * S.beat);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  const gg = ctx.createRadialGradient(cx, cy, 0, cx, cy, rc * 5);
  gg.addColorStop(0, `rgba(255,255,255,${0.8 + 0.2 * inten})`);
  gg.addColorStop(0.08, "rgba(235,248,255,0.5)");
  gg.addColorStop(0.3, "rgba(150,200,235,0.16)");
  gg.addColorStop(1, "rgba(90,140,190,0)");
  ctx.fillStyle = gg;
  ctx.beginPath();
  ctx.arc(cx, cy, rc * 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "rgba(8,14,20,0.9)";
  ctx.font = '600 15px "Cascadia Mono", Consolas, monospace';
  ctx.fillText(fmt(gas, 0), cx, cy + 1);
  ctx.font = '7.5px "Cascadia Mono", Consolas, monospace';
  ctx.fillStyle = "rgba(8,14,20,0.55)";
  ctx.fillText("M3/D", cx, cy + 12);
  ctx.restore();

  // step-size strip
  const stripW = colW * 0.78;
  const stripH = 46;
  const sx = cx - stripW / 2;
  const sy = h - 190;
  ctx.save();
  ctx.strokeStyle = "rgba(190,215,240,0.14)";
  ctx.lineWidth = 1;
  ctx.strokeRect(sx, sy, stripW, stripH);
  if (S.strips[index]) {
    ctx.globalAlpha = 0.85;
    ctx.drawImage(S.strips[index], sx + 1, sy + 1, stripW - 2, stripH - 2);
    ctx.globalAlpha = 1;
  }
  const cursorX =
    sx + 1 + (stripW - 2) * ((S.t - S.tMin) / Math.max(1e-9, S.tMax - S.tMin));
  ctx.strokeStyle = "rgba(255,255,255,0.8)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cursorX, sy);
  ctx.lineTo(cursorX, sy + stripH);
  ctx.stroke();
  ctx.font = '8px "Cascadia Mono", Consolas, monospace';
  ctx.fillStyle = "rgba(190,215,240,0.55)";
  ctx.textAlign = "left";
  ctx.fillText("累计步数 / 时间", sx + 2, sy - 4);
  ctx.textAlign = "right";
  ctx.fillText(String(ch.nRaw), sx + stripW - 2, sy - 4);
  ctx.restore();
}

function frac(seed) {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

function drawScrim(w, h) {
  const top = ctx.createLinearGradient(0, 0, 0, 128);
  top.addColorStop(0, "rgba(3,6,10,0.94)");
  top.addColorStop(1, "rgba(3,6,10,0)");
  ctx.fillStyle = top;
  ctx.fillRect(0, 0, w, 128);

  const bot = ctx.createLinearGradient(0, h - 150, 0, h);
  bot.addColorStop(0, "rgba(3,6,10,0)");
  bot.addColorStop(1, "rgba(3,6,10,0.95)");
  ctx.fillStyle = bot;
  ctx.fillRect(0, h - 150, w, 150);
}

/* ---------------------------------------------------------------- hud */

let hudTick = 0;

function updateHud() {
  if (performance.now() - hudTick < 90) return;
  hudTick = performance.now();

  S.channels.forEach((ch, i) => {
    const e = document.getElementById("h-steps-" + i);
    if (e) e.textContent = String(ch.ptr + 1);
    const h = document.getElementById("h-h-" + i);
    if (h) h.textContent = expo(ch.h[ch.ptr]);
    const g = document.getElementById("h-gas-" + i);
    if (g) g.textContent = fmt(ch.gas[ch.ptr], 0);
    const ph = document.getElementById("h-ph-" + i);
    if (ph) ph.textContent = fmt(ch.ph[ch.ptr], 2);
    const c4 = document.getElementById("h-ch4-" + i);
    if (c4) {
      const gv = ch.gas[ch.ptr];
      c4.textContent = gv > 0 ? fmt((ch.ch4[ch.ptr] / gv) * 100, 1) : "—";
    }
  });

  document.getElementById("v-t").textContent = fmt(S.t, 1);
  document.getElementById("clock").textContent = fmt(S.t, 1) + " d";

  const note = document.getElementById("note");
  let msg = "";
  const lead = S.channels[0];
  if (lead && lead.ptr < TRANSIENT_STEPS) {
    msg = "启动瞬态：t=0 气相压力初值偏离平衡，求解器用极小步长爬过去";
  } else {
    for (const ev of S.events) {
      if (ev.kind === "temp_step" && S.t >= ev.t && S.t <= ev.t + 3.0) {
        msg = `温度阶跃 ${ev.delta_c > 0 ? "+" : ""}${ev.delta_c} °C`;
      }
    }
  }
  if (msg) {
    note.textContent = msg;
    note.classList.add("show");
  } else {
    note.classList.remove("show");
  }
}

function expo(v) {
  if (!v) return "0";
  if (v >= 0.01) return v.toFixed(3);
  return v.toExponential(1);
}

/* ---------------------------------------------------------------- controls */

function bind() {
  document.getElementById("play").addEventListener("click", () => {
    if (S.t >= S.tMax) S.t = S.tMin;
    setPlaying(!S.playing);
  });
  document.getElementById("speed").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-s]");
    if (!b) return;
    S.speed = parseFloat(b.dataset.s);
    [...document.querySelectorAll("#speed button")].forEach((x) => x.classList.toggle("on", x === b));
  });
  document.getElementById("scrub").addEventListener("input", (e) => {
    const f = parseFloat(e.target.value) / 1000;
    S.t = S.tMin + f * (S.tMax - S.tMin);
    for (const ch of S.channels) advance(ch, S.t);
  });
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space") {
      e.preventDefault();
      if (S.t >= S.tMax) S.t = S.tMin;
      setPlaying(!S.playing);
    }
  });
  window.addEventListener("resize", () => {
    if (S.ready) draw(performance.now());
  });
}

bind();
load();
