/* PyADM1ODE · 求解引擎
 *
 * Original implementation of the visual language: a woven light core, a ring of
 * micro data cards, layered outward beams, a beat clock and a moving camera —
 * all driven by the real ADM1 solver trace.
 *
 * Every beat is one accepted ODE step; every beam length is |dy/dt| of the
 * state it points at; every number printed on a card is a recorded value.
 */
"use strict";

/* ------------------------------------------------------------------ basics */

const TAU = Math.PI * 2;
const NSTATE = 41;

const GROUPS = [
  { a: 0, b: 12, rgb: [126, 231, 255], name: "溶解" },
  { a: 12, b: 22, rgb: [196, 181, 253], name: "颗粒" },
  { a: 22, b: 29, rgb: [158, 247, 182], name: "菌体" },
  { a: 29, b: 37, rgb: [255, 217, 138], name: "电荷" },
  { a: 37, b: 41, rgb: [188, 216, 255], name: "气相" },
];

const NAME = [
  "S_su", "S_aa", "S_fa", "S_va", "S_bu", "S_pro", "S_ac", "S_h2", "S_ch4", "S_co2", "S_nh4", "S_I",
  "XPS_ch", "XPS_pr", "XPS_li", "XPF_ch", "XPF_pr", "XPF_li", "XS_ch", "XS_pr", "XS_li", "X_I",
  "X_su", "X_aa", "X_fa", "X_c4", "X_pro", "X_ac", "X_h2",
  "S_cat", "S_an", "S_va-", "S_bu-", "S_pr-", "S_ac-", "S_hco3", "S_nh3",
  "p_H2", "p_CH4", "p_CO2", "p_TOT",
];

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, t) => a + (b - a) * t;
const easeIO = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const hash = (x) => {
  const s = Math.sin(x * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
};
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

function mk(w, h, dpr) {
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(w * (dpr || 1)));
  c.height = Math.max(1, Math.round(h * (dpr || 1)));
  return c;
}

/* -------------------------------------------------------------------- data */

const D = {
  ready: false, n: 0, i: 0, clock: 2.0, tMax: 1,
  t: null, h: null, gas: null, ch4: null, ph: null, y: null, du: null,
  du95: null, label: "", events: [],
};

const DAYS_PER_SEC = 7;

async function loadData() {
  const res = await fetch("/api/traces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      backend: "scipy", duration: 150, dt: 1.0, temperature_c: 42.0,
      v_liq: 1050.0, v_gas: 150.0, k_l_a: 200.0,
      feed: [
        { substrate_id: "maize_silage_milk_ripeness", q: 11.4 },
        { substrate_id: "swine_manure", q: 6.1 },
        { substrate_id: "cattle_manure", q: 5.0 },
      ],
      temp_step_day: 40, temp_step_delta_c: -5, max_points: 700,
    }),
  });
  const j = await res.json();
  if (!j.ok) throw new Error(j.detail || "API");

  const c = j.channels[0];
  D.n = c.n_steps;
  D.t = Float32Array.from(c.t);
  D.h = Float32Array.from(c.h);
  D.gas = Float32Array.from(c.gas);
  D.ch4 = Float32Array.from(c.ch4);
  D.ph = Float32Array.from(c.ph);
  D.y = Float32Array.from(c.y);
  D.du = Float32Array.from(c.du);
  D.label = c.label;
  D.events = (j.events || []).map((e) => ({ ...e, fired: false }));
  D.tMax = D.t[D.n - 1];

  D.du95 = new Float32Array(NSTATE);
  for (let k = 0; k < NSTATE; k++) {
    const v = new Float32Array(D.n);
    for (let s = 0; s < D.n; s++) v[s] = Math.abs(D.du[s * NSTATE + k]);
    v.sort();
    D.du95[k] = Math.max(v[(D.n * 0.95) | 0], 1e-9);
  }

  // Total |dy/dt| per step, normalised by its own p95: summing the per-state
  // p95s would give a baseline almost no step ever reaches, which flattens the
  // amplitude to a constant.
  const duSums = new Float32Array(D.n);
  for (let s = 0; s < D.n; s++) {
    let acc = 0;
    for (let c = 0; c < NSTATE; c++) acc += Math.abs(D.du[s * NSTATE + c]);
    duSums[s] = acc;
  }
  const duSorted = Float32Array.from(duSums).sort();
  D.du95Sum = Math.max(duSorted[(D.n * 0.95) | 0], 1e-9);

  // Step-size range anchored on the median, not on min/max: the start-up
  // transient takes steps ~1e-6 wide and would otherwise stretch the log range
  // over six decades, pinning every interesting step at the same colour.
  const hs = [];
  for (let s = 0; s < D.n; s++) {
    const h = D.h[s];
    if (h > 0 && Number.isFinite(h)) hs.push(h);
  }
  hs.sort((a, b) => a - b);
  const hMed = hs.length ? hs[hs.length >> 1] : 1e-2;
  D.hMin = hMed / 12;
  D.hMax = hMed * 4;

  D.i = 0;
  while (D.i + 1 < D.n && D.t[D.i + 1] <= D.clock) D.i++;
  sliderEl.max = String(Math.max(1, D.n - 1));
  sliderEl.value = String(D.i);

  // ranges for the KPI bars: normalising against invented constants (2400, 6.6/8.0)
  // made the bars look meaningful while encoding nothing
  D.gasMax = 1e-9;
  D.phMin = Infinity;
  D.phMax = -Infinity;
  for (let s = 0; s < D.n; s++) {
    if (D.gas[s] > D.gasMax) D.gasMax = D.gas[s];
    if (Number.isFinite(D.ph[s])) {
      if (D.ph[s] < D.phMin) D.phMin = D.ph[s];
      if (D.ph[s] > D.phMax) D.phMax = D.ph[s];
    }
  }
  if (!Number.isFinite(D.phMin)) {
    D.phMin = 6.5;
    D.phMax = 8.5;
  }
  D.ready = true;
}

const at = (c) => D.y[D.i * NSTATE + c];
const rate = (c) => Math.abs(D.du[D.i * NSTATE + c]);

/* ------------------------------------------------------------------ canvas */

const stage = document.getElementById("stage");
const ctx = stage.getContext("2d");
let W = 0, H = 0, DPR = 1;

const glow = mk(2, 2);
const gx = glow.getContext("2d");
const web = mk(2, 2);
let webReady = false;

/* The weave is baked to a bitmap for cheap redraws, but the filament geometry
 * is kept as well: light pulses travel along these curves, so they have to
 * exist as paths, not pixels. */
const webLines = [];
const sparks = [];
let sparkBudget = 0;

const G = { cx: 0, cy: 0, R: 0, RO: 0, band: 0, beamMax: 0 };

function resize() {
  DPR = Math.min(window.devicePixelRatio || 1, 2);
  const w = stage.clientWidth || window.innerWidth;
  const h = stage.clientHeight || window.innerHeight;
  W = Math.max(320, w);
  H = Math.max(240, h);
  stage.width = Math.round(W * DPR);
  stage.height = Math.round(H * DPR);

  const m = Math.min(W, H);
  G.cx = W / 2;
  G.cy = H / 2;
  G.R = m * 0.225;
  G.band = clamp(m * 0.085, 34, 74);
  G.RO = G.R + G.band;
  G.beamMax = m * 0.40;

  glow.width = Math.max(2, Math.round(W * 0.5));
  glow.height = Math.max(2, Math.round(H * 0.5));

  buildWeb();
  buildCards();
  // a resize wipes every card canvas — repaint them immediately, otherwise the
  // ring shows a partially filled band until the round-robin catches up
  if (D.ready) {
    for (let i = 0; i < NSTATE; i++) paintCard(i);
    for (let i = 0; i < NTILE; i++) paintTile(i);
  }
}

/* --------------------------------------------------------------- the weave */

function buildWeb() {
  const size = Math.max(8, Math.ceil(G.R * 2.3));
  const SS = 2; // supersampled: the camera pushes in to ~4x
  web.width = size * SS;
  web.height = size * SS;
  const x = web.getContext("2d");
  x.setTransform(SS, 0, 0, SS, 0, 0);
  x.clearRect(0, 0, size, size);
  const c = size / 2;
  x.lineCap = "round";
  webLines.length = 0;
  sparks.length = 0;

  for (let k = 0; k < 760; k++) {
    const a0 = hash(k * 1.7) * TAU;
    const a1 = a0 + (hash(k * 3.1) - 0.5) * 1.9;
    const r0 = G.R * (0.12 + 0.86 * hash(k * 5.3));
    const r1 = G.R * (0.12 + 0.86 * hash(k * 7.9));
    const x1 = c + Math.cos(a0) * r0;
    const y1 = c + Math.sin(a0) * r0;
    const x2 = c + Math.cos(a1) * r1;
    const y2 = c + Math.sin(a1) * r1;
    const mx = (x1 + x2) / 2 + (hash(k * 11.3) - 0.5) * G.R * 0.5;
    const my = (y1 + y2) / 2 + (hash(k * 13.7) - 0.5) * G.R * 0.5;

    x.strokeStyle = `rgba(226,238,250,${0.02 + Math.pow(hash(k * 2.9), 2.4) * 0.5})`;
    x.lineWidth = 0.4 + hash(k * 4.1) * 1.5;
    x.beginPath();
    x.moveTo(x1, y1);
    x.quadraticCurveTo(mx, my, x2, y2);
    x.stroke();

    if (k % 2 === 0) webLines.push({ x1: x1 - c, y1: y1 - c, mx: mx - c, my: my - c, x2: x2 - c, y2: y2 - c });
  }

  for (let k = 0; k < 90; k++) {
    const a = hash(k * 9.1) * TAU;
    const r = G.R * (0.15 + 0.8 * hash(k * 8.3));
    x.fillStyle = `rgba(255,255,255,${0.25 + hash(k * 3.7) * 0.55})`;
    x.beginPath();
    x.arc(c + Math.cos(a) * r, c + Math.sin(a) * r, 0.6 + hash(k * 5.7) * 1.6, 0, TAU);
    x.fill();
  }
  webReady = true;
}

/* ------------------------------------------------------- light along wires */

function qpt(l, u) {
  const v = 1 - u;
  return {
    x: v * v * l.x1 + 2 * v * u * l.mx + u * u * l.x2,
    y: v * v * l.y1 + 2 * v * u * l.my + u * u * l.y2,
  };
}

function spawnSpark() {
  if (!webLines.length) return;
  const i = (Math.random() * webLines.length) | 0;
  sparks.push({
    l: i,
    u: 0,
    sp: 0.35 + Math.random() * 0.95,
    w: 0.05 + Math.random() * 0.14,
    a: 0.45 + Math.random() * 0.5,
    lw: 0.7 + Math.random() * 1.6,
    head: 0.5 + Math.random() * 1.3,
  });
  if (sparks.length > 240) sparks.shift();
}

function updateSparks(dt) {
  for (const s of sparks) s.u += s.sp * dt;
  for (let i = sparks.length - 1; i >= 0; i--) if (sparks[i].u > 1) sparks.splice(i, 1);

  sparkBudget += dt;
  const rate = 34 + 120 * B.env; // pulses ride the beat
  while (sparkBudget > 1 / rate && sparks.length < 240) {
    sparkBudget -= 1 / rate;
    spawnSpark();
  }
}

/** Drawn in the already-rotated weave space (translated to the core). */
function drawSparks() {
  if (!sparks.length) return;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  for (const s of sparks) {
    const l = webLines[s.l];
    if (!l) continue;
    const fade = Math.min(1, s.u * 7) * Math.min(1, (1 - s.u) * 5);
    if (fade <= 0.01) continue;
    const u0 = Math.max(0, s.u - s.w);
    const u1 = s.u;

    ctx.strokeStyle = `rgba(236,248,255,${s.a * fade})`;
    ctx.lineWidth = s.lw;
    ctx.beginPath();
    for (let k = 0; k <= 5; k++) {
      const p = qpt(l, u0 + (u1 - u0) * (k / 5));
      k ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y);
    }
    ctx.stroke();

    const hp = qpt(l, u1);
    const halo = ctx.createRadialGradient(hp.x, hp.y, 0, hp.x, hp.y, s.head * 4.5);
    halo.addColorStop(0, `rgba(255,255,255,${0.45 * fade})`);
    halo.addColorStop(1, "rgba(200,230,255,0)");
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(hp.x, hp.y, s.head * 4.5, 0, TAU);
    ctx.fill();

    ctx.fillStyle = `rgba(255,255,255,${0.95 * fade})`;
    ctx.beginPath();
    ctx.arc(hp.x, hp.y, s.head, 0, TAU);
    ctx.fill();
  }
  ctx.restore();
}

/* -------------------------------------------------------------- the cards */

const cards = [];
let cardW = 44, cardH = 58;

/* A second, denser band of small tiles sits inside the text cards. They carry
 * no text on purpose — at this radius text would be illegible — but each bar
 * is still a real state magnitude, so the grain of the picture is data. */
const NTILE = 120;
const tiles = [];
let tileW = 6, tileH = 14;

/* 60 status lamps ride the outside of the ring: one lamp per recent solver
 * step, coloured by how small that step was. A stretch where the solver is
 * fighting (tiny h) burns amber/red; cruising (large h) sits cool and dim. */
const NLAMP = 60;
const lampBuf = new Float32Array(NLAMP);
let lampHead = 0, lampCount = 0, lastStepI = -1;

function buildCards() {
  cardW = Math.max(16, ((TAU * (G.R + G.band / 2)) / NSTATE) * 0.86);
  cardH = G.band * 0.56;
  cards.length = 0;
  for (let i = 0; i < NSTATE; i++) cards.push({ cv: mk(cardW, cardH, 3), flash: 0 });

  tileW = Math.max(3, ((TAU * (G.R + G.band * 0.24)) / NTILE) * 0.82);
  tileH = G.band * 0.30;
  tiles.length = 0;
  for (let i = 0; i < NTILE; i++) tiles.push({ cv: mk(tileW, tileH, 3), state: i % NSTATE });
}

function pushLamp(h) {
  lampBuf[lampHead] = h;
  lampHead = (lampHead + 1) % NLAMP;
  if (lampCount < NLAMP) lampCount++;
}

/** Fill the lamp tape with the steps that precede the current position, so the
 *  ring already shows recent solver behaviour on the first frame. */
function seedLamps() {
  if (!D.ready) return;
  lampCount = 0;
  lampHead = 0;
  const from = Math.max(0, D.i - NLAMP + 1);
  for (let s = from; s <= D.i; s++) pushLamp(D.h[s]);
  lastStepI = D.i;
}

/** 60 lamps outside the ring: recency along the arc, colour = step size. */
function drawLamps() {
  const r = G.RO + 13;
  const sz = clamp(G.band * 0.17, 6, 11);
  let mx = 1e-9;
  for (let i = 0; i < lampCount; i++) mx = Math.max(mx, lampBuf[i]);
  const lmax = Math.log(mx / 1e-8) || 1;

  ctx.save();
  for (let i = 0; i < NLAMP; i++) {
    const idx = (lampHead - 1 - i + NLAMP * 2) % NLAMP;
    const a = -Math.PI / 2 + B.rot - (i / NLAMP) * TAU;
    ctx.save();
    ctx.translate(G.cx + Math.cos(a) * r, G.cy + Math.sin(a) * r);
    ctx.rotate(a + Math.PI / 2);

    ctx.fillStyle = "rgba(4,6,10,0.85)";
    ctx.fillRect(-sz / 2 - 1.5, -sz / 2 - 1.5, sz + 3, sz + 3);

    if (i < lampCount) {
      const u = clamp(Math.log(lampBuf[idx] / 1e-8) / lmax, 0, 1);
      const rr = Math.round(120 + 135 * (1 - u));
      const gg = Math.round(200 - 60 * (1 - u));
      const bb = Math.round(245 - 150 * (1 - u));
      ctx.fillStyle = `rgba(${rr},${gg},${bb},${0.32 + 0.6 * (1 - i / NLAMP)})`;
      ctx.fillRect(-sz / 2, -sz / 2, sz, sz);
      if (i < 3) {
        ctx.globalCompositeOperation = "lighter";
        ctx.fillStyle = `rgba(${rr},${gg},${bb},${0.45 * (1 - i / 3)})`;
        ctx.fillRect(-sz / 2 - 2, -sz / 2 - 2, sz + 4, sz + 4);
      }
    } else {
      ctx.fillStyle = "rgba(150,170,190,0.10)";
      ctx.fillRect(-sz / 2, -sz / 2, sz, sz);
    }
    ctx.restore();
  }
  ctx.restore();
}

function paintTile(i) {
  if (!D.ready) return;
  const t = tiles[i];
  if (!t) return;
  const c = t.cv.getContext("2d");
  const w = tileW, h = tileH;
  c.setTransform(3, 0, 0, 3, 0, 0);
  c.clearRect(0, 0, w, h);
  const g = GROUPS.find((q) => t.state >= q.a && t.state < q.b) || GROUPS[0];

  c.fillStyle = "rgba(200,220,240,0.09)";
  c.fillRect(0, 0, w, h);
  // three bars = this state at the last three steps, not a hash pattern
  for (let k = 0; k < 3; k++) {
    const si = Math.max(0, D.i - (2 - k));
    const mag = Math.abs(D.y[si * NSTATE + t.state]);
    const fk = clamp(Math.log10(1 + mag * 10) / 3, 0, 1);
    const bh = (0.12 + 0.88 * fk) * h;
    c.fillStyle = rgba(g.rgb, 0.35 + 0.5 * fk);
    c.fillRect(0.5 + k * (w / 3), h - bh, Math.max(1, w / 3 - 1), bh);
  }
  c.fillStyle = rgba(g.rgb, 0.85);
  c.fillRect(0, 0, w, 1);
}

function cardValue(i) {
  const v = at(i);
  const a = Math.abs(v);
  return a >= 1 ? v.toFixed(2) : a >= 1e-3 ? v.toFixed(3) : v.toExponential(1);
}

function paintCard(idx) {
  if (!D.ready) return;
  const card = cards[idx];
  if (!card) return;
  const c = card.cv.getContext("2d");
  const w = cardW, h = cardH;
  c.setTransform(3, 0, 0, 3, 0, 0);
  c.clearRect(0, 0, w, h);

  const g = GROUPS.find((x) => idx >= x.a && idx < x.b) || GROUPS[0];
  c.fillStyle = "rgba(8,10,14,0.6)";
  c.fillRect(0, 0, w, h);
  c.fillStyle = rgba(g.rgb, 0.6);
  c.fillRect(0, 0, 1.6, h);

  c.textBaseline = "top";
  c.font = '6px "Cascadia Mono",Consolas,monospace';
  c.fillStyle = "rgba(150,160,175,0.9)";
  c.fillText(String(idx).padStart(2, "0"), 3, 2);

  c.fillStyle = rgba(g.rgb, 0.95);
  c.font = '6.5px "Cascadia Mono",Consolas,monospace';
  c.fillText(NAME[idx], 12, 2);

  c.fillStyle = "rgba(244,245,247,0.95)";
  c.font = 'bold 8px "Cascadia Mono",Consolas,monospace';
  c.fillText(cardValue(idx), 3, 11);

  // 15 blocks = this state's last 15 steps, brightness = value within that
  // window. It used to be a hash pattern: it looked like structure and encoded
  // nothing, which is the same lie as a checklist nobody evaluates.
  const rows = 3, n = 5, LEN = rows * n;
  const top = 24, rowH = (h - top - 7) / rows;
  const lw = (w - 6) / n - 1;
  const vals = [];
  let vmn = Infinity, vmx = -Infinity;
  for (let s = 0; s < LEN; s++) {
    const si = Math.max(0, D.i - (LEN - 1 - s));
    const v = D.y[si * NSTATE + idx];
    vals.push(v);
    if (v < vmn) vmn = v;
    if (v > vmx) vmx = v;
  }
  const vspan = vmx - vmn || 1;
  for (let k = 0; k < LEN; k++) {
    const r = (k / n) | 0, j = k % n;
    const f = (vals[k] - vmn) / vspan;
    c.fillStyle = `rgba(220,232,246,${0.10 + 0.72 * f})`;
    c.fillRect(3 + j * (lw + 1), top + r * rowH + 1, lw, Math.max(1, rowH - 3));
  }

  const len = 14;
  const i0 = Math.max(0, D.i - len);
  let mn = Infinity, mx = -Infinity;
  for (let s = i0; s <= D.i; s++) {
    const v = D.y[s * NSTATE + idx];
    if (v < mn) mn = v;
    if (v > mx) mx = v;
  }
  if (mx - mn > 1e-12) {
    c.strokeStyle = rgba(g.rgb, 0.85);
    c.lineWidth = 1;
    c.beginPath();
    for (let s = i0; s <= D.i; s++) {
      const v = D.y[s * NSTATE + idx];
      const px = 3 + ((s - i0) / Math.max(1, D.i - i0)) * (w - 6);
      const py = h - 4 - ((v - mn) / (mx - mn)) * 5;
      s === i0 ? c.moveTo(px, py) : c.lineTo(px, py);
    }
    c.stroke();
  }
}

/* ----------------------------------------------------------------- the beat */

const B = { env: 0, since: 9, n: 0, rot: 0, kick: 0, pulses: [], waves: [], tipClock: 0 };

/* Stepwise ring motion: sprint to a new angle, hold dead still, sprint again.
 * Always forward now. The angle is not random any more: it is proportional to
 * the number of solver steps taken since the last move, so one "notch" of the
 * ring = one chunk of computation. The hold shortens with every beat, so a busy
 * solver ticks the ring more often. */
const ROT = { from: 0, to: 0, t: 1, dur: 0.22, hold: 0.6 };
let stepsSinceMove = 0;

function startRotMove() {
  ROT.from = B.rot;
  const mag = 0.05 + Math.min(0.24, stepsSinceMove * 0.014);
  ROT.to = ROT.from + mag;          // unidirectional: always forward
  stepsSinceMove = 0;
  ROT.t = 0;
  ROT.dur = 0.14 + Math.random() * 0.13;
  ROT.hold = 0.55 + Math.random() * 0.75;
}

function updateRot(dt) {
  if (ROT.t < ROT.dur) {
    ROT.t = Math.min(ROT.dur, ROT.t + dt);
    B.rot = ROT.from + (ROT.to - ROT.from) * easeIO(ROT.t / ROT.dur);
  } else if (ROT.hold > 0) {
    ROT.hold -= dt;
  } else {
    startRotMove();
  }
}

function onBeat(idx) {
  B.since = 0;
  B.n++;
  B.kick = Math.min(1.6, B.kick + 0.85);
  const big = B.n % 4 === 0;
  B.pulses.push({
    c: big ? -Math.PI : -Math.PI,
    v: 0.9 + 1.4 * hash(B.n * 3.7),
    w: big ? 0.42 : 0.14 + 0.12 * hash(B.n * 5.1),
    amp: big ? 1 : 0.5 + 0.4 * hash(B.n * 7.3),
    life: 1.4 + hash(B.n * 2.3),
  });
  for (let k = 0; k < 3; k++) {
    const c = cards[(hash(B.n * 9.1 + k) * NSTATE) | 0];
    if (c) c.flash = 0.5 + 0.4 * hash(B.n + k);
  }
  // a beat sends a burst of light down the weave
  for (let k = 0; k < 10; k++) spawnSpark();
  // One shockwave per accepted step, and it carries that step's numbers:
  //   speed - inverse of the step size (a struggling solver shoots fast rings)
  //   amp   - total |dy/dt| over the 41 states, relative to the run's p95
  // so a row of fast bright rings IS a stretch where the solver was fighting.
  if (D.ready) {
    const s = clamp(Math.round(idx ?? D.i), 0, D.n - 1);
    const h = Math.max(D.h[s], 1e-9);
    const u = clamp(
      (Math.log10(D.hMax) - Math.log10(h)) / Math.max(1e-6, Math.log10(D.hMax) - Math.log10(D.hMin)),
      0,
      1
    );
    let e = 0;
    for (let c = 0; c < NSTATE; c++) e += Math.abs(D.du[s * NSTATE + c]);
    e = clamp(e / Math.max(D.du95Sum, 1e-9), 0, 1.5);
    // speed 380-1280 px/s: fast enough that a ring crosses the frame within its
    // 0.5-0.9 s life, so the expansion is actually visible before it fades
    spawnWave(G.RO + 6, 380 + 900 * u, 0.22 + 0.8 * e, 0.55 + 0.35 * (1 - u));
  }
  // a beat nudges the next hold shorter, but never below a readable pause:
  // the ring must still visibly stop, or the "spin / hold / spin" reads as drift
  if (ROT.hold > 0.3) ROT.hold *= 0.94;
}

function spawnWave(r0, speed, amp, life) {
  B.waves.push({ r: r0, v: speed, dir: 1, life: life || 1.1, amp });
  if (B.waves.length > 12) B.waves.shift();
}

function advanceClock(dt) {
  if (!D.ready) return 0;
  const before = D.i;
  D.clock += dt * DAYS_PER_SEC;
  if (D.clock >= D.tMax) D.clock = 2.0;
  let i = D.i;
  while (i + 1 < D.n && D.t[i + 1] <= D.clock) i++;
  while (i > 0 && D.t[i] > D.clock) i--;
  D.i = i;
  return Math.min(3, Math.max(0, i - before));
}

/* ---------------------------------------------------------------- camera */

const cam = { x: 0, y: 0, z: 1, r: 0 };
let shots = [], shotI = 0, shotT = 0, camFrom = null, camHold = true;
let forceShot = -1, freezeCam = false;

/* The camera tours once, then the view belongs to the user. Touching the stage
 * at any point ends the tour immediately - nobody should have to wait out a
 * 40-second fly-through to zoom in. */
let userCam = false;
let hintTimer = 0;

function handOver() {
  if (userCam) return;
  userCam = true;
  const el = document.getElementById("hint");
  if (!el) return;
  el.classList.add("show");
  clearTimeout(hintTimer);
  hintTimer = setTimeout(() => el.classList.remove("show"), 4500);
}

function clampCam() {
  const hw = W / (2 * cam.z), hh = H / (2 * cam.z);
  cam.x = clamp(cam.x, Math.min(W / 2, hw), Math.max(W / 2, W - hw));
  cam.y = clamp(cam.y, Math.min(H / 2, hh), Math.max(H / 2, H - hh));
}

function buildShots() {
  const P = (a, r) => ({ x: G.cx + Math.cos(a) * r, y: G.cy + Math.sin(a) * r });
  const mid = (G.R + G.RO) / 2;
  const edge = G.RO + 30;
  // x, y, zoom, roll (radians), hold (s)
  shots = [
    { x: G.cx, y: G.cy, z: 1, r: 0, hold: 3.6 },
    { ...P(-0.5, mid), z: 2.4, r: 0.02, hold: 2.8 },
    { ...P(0.1, G.R * 0.55), z: 3.6, r: -0.05, hold: 2.4 },   // push into the weave
    { ...P(0.06, edge), z: 1.7, r: 0.01, hold: 2.6 },
    { x: G.cx, y: G.cy, z: 1.12, r: 0, hold: 2.2 },
    { ...P(0.36, mid), z: 3.1, r: 0.06, hold: 2.4 },          // dutch angle on the band
    { ...P(-1.15, G.RO + 55), z: 1.45, r: -0.03, hold: 2.6 },
    { ...P(0.22, G.R * 0.75), z: 3.8, r: 0.04, hold: 2.0 },   // deepest push
    { x: G.cx, y: G.cy, z: 1, r: 0, hold: 3.0 },
    { ...P(-0.34, mid + 6), z: 2.1, r: -0.02, hold: 2.6 },
    { ...P(0.9, edge), z: 1.28, r: 0.02, hold: 2.4 },
  ];
  shotI = 0;
  shotT = 0;
  camHold = true;
  Object.assign(cam, shots[0]);
  if (forceShot >= 0) {
    shotI = Math.min(shots.length - 1, forceShot);
    Object.assign(cam, shots[shotI]);
  }
}

function updateCam(dt) {
  if (freezeCam || userCam) return;
  shotT += dt;
  const cur = shots[shotI];
  const isLast = shotI === shots.length - 1;
  if (camHold) {
    if (shotT > cur.hold) {
      if (isLast) {
        handOver(); // tour over
        return;
      }
      camHold = false;
      shotT = 0;
      camFrom = { x: cam.x, y: cam.y, z: cam.z, r: cam.r };
    }
  } else {
    const nxt = shots[(shotI + 1) % shots.length];
    const k = Math.min(1, shotT / 2.0);
    const e = easeIO(k);
    cam.x = lerp(camFrom.x, nxt.x, e);
    cam.y = lerp(camFrom.y, nxt.y, e);
    cam.z = Math.exp(lerp(Math.log(camFrom.z), Math.log(nxt.z), e));
    cam.r = lerp(camFrom.r, nxt.r || 0, e);
    if (k >= 1) {
      shotI++;
      camHold = true;
      shotT = 0;
    }
  }
  clampCam();
}

/** world px → device px, as one affine matrix (translate · rotate · scale). */
function camMatrix(s) {
  const z = cam.z * s;
  const c = Math.cos(cam.r), sn = Math.sin(cam.r);
  return {
    a: z * c,
    b: z * sn,
    c: -z * sn,
    d: z * c,
    e: s * (W / 2) - (c * cam.x - sn * cam.y) * z,
    f: s * (H / 2) - (sn * cam.x + c * cam.y) * z,
  };
}

function applyCam() {
  const m = camMatrix(DPR);
  ctx.setTransform(m.a, m.b, m.c, m.d, m.e, m.f);
}

const toScreen = (x, y) => {
  const dx = (x - cam.x) * cam.z, dy = (y - cam.y) * cam.z;
  const c = Math.cos(cam.r), sn = Math.sin(cam.r);
  return { x: dx * c - dy * sn + W / 2, y: dx * sn + dy * c + H / 2 };
};

/* ------------------------------------------------------------------ render */

function drawBackdrop() {
  ctx.globalCompositeOperation = "source-over";
  ctx.globalAlpha = 1;
  const g = ctx.createRadialGradient(G.cx, G.cy, 0, G.cx, G.cy, Math.max(W, H) * 0.8);
  g.addColorStop(0, "#0c1219");
  g.addColorStop(0.45, "#06090d");
  g.addColorStop(1, "#040507");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
}

function drawWeb(t) {
  if (!webReady) return;
  const s = (G.R * 2.3) / 2;
  for (let k = 0; k < 2; k++) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = k === 0 ? 0.42 : 0.22;
    ctx.translate(G.cx, G.cy);
    ctx.rotate((k ? -1 : 1) * B.rot * 0.6 * (k + 1) + (k ? 2.1 : 0));
    ctx.drawImage(web, -s, -s, s * 2, s * 2);
    if (k === 0) {
      ctx.globalAlpha = 1;
      drawSparks(); // shares the weave's rotation, so the light stays on the wire
    }
    ctx.restore();
  }
}

/* Beam layers, split by role:
 *  crisp  - thin, high-contrast streaks drawn straight onto the stage at full
 *           resolution (a 0.8px line inside a half-res buffer is just a smear)
 *  soft   - wide, low-alpha layers that exist only to bloom
 */
const BEAM_CRISP = [
  { n: 110, r0: 0, w: 0.8, a: 0.2, len: 0.34, sp: 0.62 },
  { n: 96, r0: 2, w: 1.1, a: 0.24, len: 0.44, sp: 0.55 },
  { n: 84, r0: 10, w: 1.6, a: 0.3, len: 0.62, sp: 0.4 },
];
const BEAM_SOFT = [
  { n: 64, r0: 22, w: 2.2, a: 0.3, len: 0.85, sp: 0.28 },
  { n: 44, r0: 38, w: 3.6, a: 0.2, len: 1.0, sp: 0.2 },
  { n: 30, r0: 54, w: 5.0, a: 0.13, len: 1.1, sp: 0.15 },
  { n: 24, r0: 60, w: 6.4, a: 0.09, len: 1.15, sp: 0.12 },
];

function beamPass(g, layers, t, withTips) {
  g.lineCap = "round";
  g.globalCompositeOperation = "lighter";
  for (let L = 0; L < layers.length; L++) {
    const ly = layers[L];
    const rot = B.rot * ly.sp + L * 0.7;
    for (let j = 0; j < ly.n; j++) {
      const a = (j / ly.n) * TAU + rot;
      const st = ((a / TAU) % 1 + 1) % 1;
      const sIdx = Math.min(NSTATE - 1, (st * NSTATE) | 0);
      const gain = Math.pow(clamp(rate(sIdx) / D.du95[sIdx], 0, 1), 0.55);
      if (gain < 0.02) continue;

      let pulse = 0;
      for (const p of B.pulses) {
        const d = Math.atan2(Math.sin(a - p.c), Math.cos(a - p.c));
        pulse += p.amp * Math.exp(-((d / p.w) ** 2)) * Math.min(1, p.life);
      }

      const len = G.beamMax * ly.len * gain * (0.35 + 0.5 * gain + 0.45 * B.env) * (1 + 0.6 * pulse);
      const r0 = G.RO + ly.r0;
      const r1 = Math.min(r0 + len, G.R + G.beamMax * 1.25);
      if (r1 - r0 < 2) continue;

      const x0 = G.cx + Math.cos(a) * r0, y0 = G.cy + Math.sin(a) * r0;
      const x1 = G.cx + Math.cos(a) * r1, y1 = G.cy + Math.sin(a) * r1;

      const grad = g.createLinearGradient(x0, y0, x1, y1);
      grad.addColorStop(0, `rgba(255,255,255,${ly.a * (0.5 + 0.8 * gain)})`);
      grad.addColorStop(0.45, `rgba(214,230,248,${ly.a * 0.45})`);
      grad.addColorStop(1, "rgba(180,205,235,0)");
      g.strokeStyle = grad;
      g.lineWidth = ly.w;
      g.beginPath();
      g.moveTo(x0, y0);
      g.lineTo(x1, y1);
      g.stroke();

      if (withTips && gain > 0.3) {
        // phase from a monotonic clock: putting gain inside `t * (0.6 + gain)`
        // made the tip jump whenever gain changed, because t keeps growing
        const ph = (B.tipClock * 0.35 + hash(j * 3.1 + L)) % 1;
        const s0 = r0 + (r1 - r0) * ph;
        const s1 = Math.min(r1, s0 + (r1 - r0) * 0.16);
        g.strokeStyle = `rgba(255,255,255,${0.4 * gain})`;
        g.lineWidth = Math.max(0.8, ly.w * 0.9);
        g.beginPath();
        g.moveTo(G.cx + Math.cos(a) * s0, G.cy + Math.sin(a) * s0);
        g.lineTo(G.cx + Math.cos(a) * s1, G.cy + Math.sin(a) * s1);
        g.stroke();
      }
    }
  }
}

function drawBeams(t) {
  // 1. crisp streaks straight onto the stage - these are what the eye reads
  ctx.save();
  beamPass(ctx, BEAM_CRISP, t, true);
  ctx.restore();

  // 2. wide layers into a half-res buffer, composited about the centre.
  //    Scaling from the origin (0,0) shifts the whole bloom by half the scale
  //    delta of the canvas, which shows up as drift the moment the camera moves.
  const gm = camMatrix(0.5);
  gx.setTransform(1, 0, 0, 1, 0, 0);
  gx.clearRect(0, 0, glow.width, glow.height);
  gx.setTransform(gm.a, gm.b, gm.c, gm.d, gm.e, gm.f);
  beamPass(gx, BEAM_SOFT, t, false);

  ctx.save();
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.globalCompositeOperation = "lighter";
  ctx.globalAlpha = 0.9;
  const k = 1.04;
  ctx.drawImage(glow, (W * (1 - k)) / 2, (H * (1 - k)) / 2, W * k, H * k);
  ctx.restore();

  applyCam();
}

function drawWaves() {
  if (!B.waves.length) return;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (const w of B.waves) {
    const fade = Math.min(1, w.life / 0.7) * Math.min(1, (w.r - G.R) / 30);
    if (fade <= 0.01) continue;
    const a = w.amp * fade;
    ctx.strokeStyle = `rgba(200,232,255,${0.18 * a})`;
    ctx.lineWidth = 7;
    ctx.beginPath();
    ctx.arc(G.cx, G.cy, w.r, 0, TAU);
    ctx.stroke();
    ctx.strokeStyle = `rgba(255,255,255,${0.95 * a})`;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(G.cx, G.cy, w.r, 0, TAU);
    ctx.stroke();
  }
  ctx.restore();
}

function drawCore(t) {
  const gas = D.gas[D.i];
  const inten = clamp(gas / D.gasMax, 0, 1.4);
  // no wall-clock breathing: the core only pulses when a step lands (B.env)
  const r = G.R * 0.34 * (1 + 0.18 * B.env + 0.1 * inten);

  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  const g = ctx.createRadialGradient(G.cx, G.cy, 0, G.cx, G.cy, r * 2.6);
  g.addColorStop(0, `rgba(255,255,255,${0.55 + 0.35 * Math.min(1, inten)})`);
  g.addColorStop(0.18, "rgba(226,240,255,0.28)");
  g.addColorStop(0.55, "rgba(140,185,225,0.07)");
  g.addColorStop(1, "rgba(90,130,180,0)");
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(G.cx, G.cy, r * 2.6, 0, TAU);
  ctx.fill();

  for (let k = 0; k < 3; k++) {
    const rr = r * (1.25 + 0.42 * k);
    ctx.strokeStyle = `rgba(210,236,255,${0.26 - k * 0.07})`;
    ctx.lineWidth = 0.9;
    ctx.setLineDash([rr * 0.1, rr * 0.16]);
    ctx.lineDashOffset = (k % 2 ? -1 : 1) * B.rot * 60 + k * 20;
    ctx.beginPath();
    ctx.arc(G.cx, G.cy, rr, 0, TAU);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.restore();

  ctx.save();
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "rgba(10,14,20,0.9)";
  ctx.font = 'bold 15px "Cascadia Mono",Consolas,monospace';
  ctx.fillText(gas.toFixed(0), G.cx, G.cy + 1);
  ctx.font = '7px "Cascadia Mono",Consolas,monospace';
  ctx.fillStyle = "rgba(10,14,20,0.6)";
  ctx.fillText("M³/D", G.cx, G.cy + 12);
  ctx.restore();
}

function drawRing(t) {
  ctx.save();

  // machined teeth outside the band
  for (let k = 0; k < 180; k++) {
    const a = (k / 180) * TAU + B.rot;
    const r0 = G.RO + 3, r1 = G.RO + (k % 3 === 0 ? 11 : 7);
    ctx.strokeStyle = k % 3 === 0 ? "rgba(210,230,248,0.42)" : "rgba(200,222,245,0.16)";
    ctx.lineWidth = 1.1;
    ctx.beginPath();
    ctx.moveTo(G.cx + Math.cos(a) * r0, G.cy + Math.sin(a) * r0);
    ctx.lineTo(G.cx + Math.cos(a) * r1, G.cy + Math.sin(a) * r1);
    ctx.stroke();
  }

  // fine ticks biting into the inner edge
  for (let k = 0; k < 150; k++) {
    const a = (k / 150) * TAU - B.rot * 0.6;
    const r1 = G.R + 1, r0 = G.R - (k % 5 === 0 ? 7 : 3);
    ctx.strokeStyle = "rgba(200,222,245,0.13)";
    ctx.lineWidth = 0.7;
    ctx.beginPath();
    ctx.moveTo(G.cx + Math.cos(a) * r0, G.cy + Math.sin(a) * r0);
    ctx.lineTo(G.cx + Math.cos(a) * r1, G.cy + Math.sin(a) * r1);
    ctx.stroke();
  }
  ctx.restore();

  drawLamps();

  // dense tile band (no text, pure glyph grain)
  const rTile = G.R + G.band * 0.24;
  for (let i = 0; i < NTILE; i++) {
    const tile = tiles[i];
    if (!tile) continue;
    const a = -Math.PI / 2 + B.rot + (i / NTILE) * TAU;
    ctx.save();
    ctx.translate(G.cx + Math.cos(a) * rTile, G.cy + Math.sin(a) * rTile);
    ctx.rotate(a + Math.PI / 2);
    ctx.drawImage(tile.cv, -tileW / 2, -tileH / 2, tileW, tileH);
    ctx.restore();
  }

  // text cards on the outer half of the band
  const rCard = G.R + G.band * 0.66;
  for (let i = 0; i < NSTATE; i++) {
    const card = cards[i];
    if (!card) continue;
    const a = -Math.PI / 2 + B.rot + (i / NSTATE) * TAU;
    ctx.save();
    ctx.translate(G.cx + Math.cos(a) * rCard, G.cy + Math.sin(a) * rCard);
    ctx.rotate(a + Math.PI / 2);
    ctx.drawImage(card.cv, -cardW / 2, -cardH / 2, cardW, cardH);
    if (card.flash > 0.01) {
      ctx.globalCompositeOperation = "lighter";
      ctx.fillStyle = `rgba(255,255,255,${card.flash * 0.35})`;
      ctx.fillRect(-cardW / 2, -cardH / 2, cardW, cardH);
    }
    ctx.restore();
  }

  // band edges + a slow bright sweep riding the rim
  ctx.save();
  ctx.strokeStyle = "rgba(210,232,255,0.22)";
  ctx.lineWidth = 0.9;
  for (const rr of [G.R, G.RO]) {
    ctx.beginPath();
    ctx.arc(G.cx, G.cy, rr, 0, TAU);
    ctx.stroke();
  }

  const sw = 0.4;
  const sa = B.rot * 0.45;
  const sg = ctx.createLinearGradient(
    G.cx + Math.cos(sa) * G.R,
    G.cy + Math.sin(sa) * G.R,
    G.cx + Math.cos(sa + sw) * G.RO,
    G.cy + Math.sin(sa + sw) * G.RO
  );
  sg.addColorStop(0, "rgba(255,255,255,0)");
  sg.addColorStop(0.5, "rgba(220,240,255,0.22)");
  sg.addColorStop(1, "rgba(255,255,255,0)");
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = sg;
  ctx.lineWidth = G.band * 0.7;
  ctx.beginPath();
  ctx.arc(G.cx, G.cy, G.R + G.band / 2, sa, sa + sw);
  ctx.stroke();
  ctx.restore();
}

/* --------------------------------------------------------------- the loop */

let last = 0, acc = 0, cardCursor = 0, tileCursor = 0, frameN = 0;

/* ------------------------------------------------------------- time control */

let playing = true;
let scrubbing = false;

const sliderEl = document.getElementById("timeSlider");
const playBtn = document.getElementById("playBtn");
const timeLabel = document.getElementById("timeLabel");

function syncPlayBtn() {
  playBtn.textContent = playing ? "❚❚" : "▶";
  playBtn.setAttribute("aria-label", playing ? "暂停" : "播放");
}

/** Jump to a step index: state, lamps, every card and tile, and the HUD. */
function seekToIndex(idx) {
  if (!D.ready) return;
  const i = Math.round(clamp(idx, 0, D.n - 1));
  D.i = i;
  D.clock = D.t[i];
  lastStepI = i;
  seedLamps();
  for (let k = 0; k < NSTATE; k++) paintCard(k);
  for (let k = 0; k < NTILE; k++) paintTile(k);
  const wasFrozen = frozen;
  frozen = false;
  hudT = 0;
  drawHud();
  frozen = wasFrozen;
}

function bindScrub() {
  sliderEl.addEventListener("pointerdown", () => {
    scrubbing = true;
    playing = false;
    syncPlayBtn();
  });
  sliderEl.addEventListener("input", () => seekToIndex(parseInt(sliderEl.value, 10)));
  const endScrub = () => {
    scrubbing = false;
  };
  sliderEl.addEventListener("change", endScrub);
  window.addEventListener("pointerup", endScrub);

  playBtn.addEventListener("click", () => {
    playing = !playing;
    syncPlayBtn();
  });

  window.addEventListener("keydown", (e) => {
    if (e.target && e.target.tagName === "INPUT" && e.code !== "Space") return;
    if (e.code === "Space") {
      e.preventDefault();
      playing = !playing;
      syncPlayBtn();
    } else if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
      if (!D.ready) return;
      e.preventDefault();
      playing = false;
      syncPlayBtn();
      seekToIndex(D.i + (e.code === "ArrowRight" ? 1 : -1));
    }
  });
  syncPlayBtn();
}

function frame(now) {
  try {
    step(now);
  } catch (e) {
    console.error(e);
    if (frameN < 3) {
      const el = document.getElementById("stBig");
      if (el) el.textContent = "ERR " + e.message;
      try {
        ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
        ctx.fillStyle = "#ff5555";
        ctx.font = "13px monospace";
        ctx.fillText("ERR " + e.message, 24, 60);
      } catch (_) {
        /* ignore */
      }
    }
  } finally {
    frameN++;
    requestAnimationFrame(frame);
  }
}

function step(now) {
  const dt = last ? Math.min(0.05, (now - last) / 1000) : 0.016;
  last = now;
  acc += dt;
  const t = now / 1000;

  if (!frozen) {
    if (D.ready) {
      const steps = playing ? advanceClock(dt) : 0;
      for (let k = 0; k < steps; k++) onBeat(D.i - (steps - 1 - k));

      // feed the lamp tape with every step that was crossed this frame
      if (D.i !== lastStepI) {
        if (lastStepI < 0 || D.i < lastStepI) {
          stepsSinceMove++;
          lastStepI = D.i;
          pushLamp(D.h[D.i]);
        } else {
          for (let s = lastStepI + 1; s <= D.i; s++) pushLamp(D.h[s]);
          stepsSinceMove += D.i - lastStepI;
          lastStepI = D.i;
        }
      }
    }
    B.since += dt;
    B.env = Math.exp(-B.since * 4.2);
    B.kick *= Math.exp(-dt * 3.2);
    B.tipClock += dt;
    updateRot(dt);

    for (const p of B.pulses) {
      p.c += p.v * dt;
      p.life -= dt;
    }
    B.pulses = B.pulses.filter((p) => p.life > 0);
    for (const c of cards) if (c.flash > 0) c.flash *= Math.exp(-dt * 5.5);
    updateSparks(dt);

    // shockwaves expand outward and fade; the old bounce-back was invented
    for (const w of B.waves) {
      w.r += w.dir * w.v * dt;
      w.life -= dt;
    }
    B.waves = B.waves.filter((w) => w.life > 0 && w.r > G.R + 2);

    // a real simulation event punches through the visuals once
    for (const ev of D.events) {
      if (D.clock < ev.t) ev.fired = false;
      if (D.ready && !ev.fired && D.clock >= ev.t) {
        ev.fired = true;
        onBeat();
        spawnWave(G.RO + 4, 330, 1.0);
        for (let k = 0; k < 40; k++) spawnSpark();
      }
    }
  }

  if (D.ready && acc > 0.05) {
    acc = 0;
    for (let k = 0; k < 6; k++) {
      paintCard(cardCursor);
      cardCursor = (cardCursor + 1) % NSTATE;
    }
    for (let k = 0; k < 12; k++) {
      paintTile(tileCursor);
      tileCursor = (tileCursor + 1) % NTILE;
    }
  }

  updateCam(dt);

  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.clearRect(0, 0, W, H);
  applyCam();
  drawBackdrop();

  if (D.ready) {
    drawWeb(t);
    drawBeams(t);
    drawWaves();
    drawRing(t);
    drawCore(t);
    drawCallout();
  }
  drawHud();
}

/* -------------------------------------------------------------------- HUD */

const coEl = document.getElementById("callout");

function drawCallout() {
  const a = -0.55 + B.rot * 0.2;
  const wr = G.RO + 34;
  const anchor = toScreen(G.cx + Math.cos(a) * wr, G.cy + Math.sin(a) * wr);
  const box = toScreen(G.cx + Math.cos(a) * wr + 130, G.cy + Math.sin(a) * wr - 46);
  const bx = clamp(box.x, 8, Math.max(8, W - 200));
  const by = clamp(box.y, 44, Math.max(44, H - 70));
  coEl.style.transform = `translate(${bx}px,${by}px)`;

  ctx.save();
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.strokeStyle = "rgba(230,233,238,0.5)";
  ctx.lineWidth = 0.8;
  ctx.beginPath();
  ctx.moveTo(anchor.x, anchor.y);
  ctx.lineTo(bx, by + 14);
  ctx.stroke();
  ctx.fillStyle = "#fff";
  ctx.fillRect(anchor.x - 1.5, anchor.y - 1.5, 3, 3);
  ctx.restore();
  applyCam();
}

let hudT = 0;
let frozen = false;

function drawHud() {
  if (frozen) return;
  if (performance.now() - hudT < 110) return;
  hudT = performance.now();
  if (!D.ready) {
    drawPanels();
    return;
  }

  const i = D.i;
  const ch4p = (D.ch4[i] / Math.max(D.gas[i], 1e-9)) * 100;
  document.getElementById("k0").textContent = D.t[i].toFixed(1);
  document.getElementById("k1").textContent = String(i + 1);
  document.getElementById("k2").textContent = D.gas[i].toFixed(0);
  document.getElementById("k3").textContent = D.ph[i].toFixed(2);
  document.getElementById("k4").textContent = ch4p.toFixed(1);

  const frac = [
    D.clock / D.tMax,
    (i + 1) / D.n,
    D.gas[i] / D.gasMax,
    (D.ph[i] - D.phMin) / Math.max(1e-6, D.phMax - D.phMin),
    ch4p / 100,
  ];
  document.querySelectorAll(".kpi i").forEach((el, k) => {
    el.style.setProperty("--w", Math.round(clamp(frac[k] || 0, 0, 1) * 100) + "%");
  });

  document.getElementById("stBig").textContent = `${i + 1} / ${D.n} STEPS · ${D.t[i].toFixed(2)} d`;

  // keep the scrubber in sync unless the user is dragging it
  if (!scrubbing) sliderEl.value = String(i);
  timeLabel.textContent = `${D.t[i].toFixed(2)} / ${D.t[D.n - 1].toFixed(1)} d`;

  const evEl = document.getElementById("event");
  const act = D.events.find((e) => D.clock >= e.t - 0.2 && D.clock <= e.t + 3.5);
  if (act) {
    evEl.classList.add("show");
    document.getElementById("evK").textContent =
      act.kind === "temp_step" ? "温度阶跃 / TEMP STEP" : act.kind;
    document.getElementById("evV").textContent =
      `${act.delta_c > 0 ? "+" : ""}${act.delta_c} °C · t = ${act.t.toFixed(0)} d`;
  } else {
    evEl.classList.remove("show");
  }

  // The callout used to cycle five hard-coded "checks" (质量守恒 / 电荷平衡 / …).
  // None of them was ever evaluated — it read like a live verification panel but
  // was a fixed string list, i.e. it claimed something false. It now prints the
  // two numbers this frame actually depends on.
  const hNow = D.h[i];
  let duNow = 0;
  for (let c = 0; c < NSTATE; c++) duNow += Math.abs(D.du[i * NSTATE + c]);
  document.getElementById("coK").textContent = `第 ${i + 1} 步 / STEP`;
  document.getElementById("coV").textContent =
    `h = ${hNow.toExponential(2)} d ｜ Σ|dy/dt| = ${duNow.toFixed(2)}`;

  drawPanels();
}

/* ----------------------------------------------------------- mini panels */

const PC = {
  pStates: document.getElementById("pStates"),
  pRates: document.getElementById("pRates"),
  pGas: document.getElementById("pGas"),
  pStep: document.getElementById("pStep"),
};

function panelCtx(el) {
  if (!el) return null;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = el.clientWidth || 200, h = el.clientHeight || 80;
  if (el.width !== Math.round(w * dpr) || el.height !== Math.round(h * dpr)) {
    el.width = Math.round(w * dpr);
    el.height = Math.round(h * dpr);
  }
  const x = el.getContext("2d");
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { x, w, h };
}

function drawPanels() {
  if (!D.ready) return;

  {
    const p = panelCtx(PC.pStates);
    if (p) {
      const { x, w, h } = p;
      x.clearRect(0, 0, w, h);
      const bw = w / NSTATE;
      let mx = 1e-9;
      for (let c = 0; c < NSTATE; c++) mx = Math.max(mx, Math.abs(at(c)));
      for (let c = 0; c < NSTATE; c++) {
        const g = GROUPS.find((q) => c >= q.a && c < q.b);
        const f = Math.min(1, Math.log10(1 + (Math.abs(at(c)) / mx) * 9));
        x.fillStyle = "rgba(255,255,255,0.05)";
        x.fillRect(c * bw + 0.5, 0, bw - 1, h);
        x.fillStyle = rgba(g ? g.rgb : [220, 220, 220], 0.35 + 0.6 * f);
        x.fillRect(c * bw + 0.5, h - f * h, bw - 1, f * h);
      }
    }
  }

  {
    const p = panelCtx(PC.pRates);
    if (p) {
      const { x, w, h } = p;
      x.clearRect(0, 0, w, h);
      const rows = [];
      for (let c = 0; c < NSTATE; c++) rows.push([c, rate(c)]);
      rows.sort((a, b) => b[1] - a[1]);
      const top = rows.slice(0, 8);
      const mx = Math.max(top[0][1], 1e-12);
      x.font = '8px "Cascadia Mono",Consolas,monospace';
      x.textBaseline = "middle";
      top.forEach(([c, v], k) => {
        const y = (k + 0.5) * (h / top.length);
        x.fillStyle = "rgba(150,160,175,0.85)";
        x.fillText(NAME[c], 0, y);
        x.fillStyle = "rgba(240,245,250,0.9)";
        x.fillRect(40, y - 2, Math.max(1, (v / mx) * (w - 78)), 4);
        x.fillStyle = "rgba(150,160,175,0.6)";
        x.fillText(v.toExponential(1), w - 36, y);
      });
    }
  }

  {
    const p = panelCtx(PC.pGas);
    if (p) {
      const { x, w, h } = p;
      x.clearRect(0, 0, w, h);
      let gmx = 1e-9;
      for (let k = 0; k < D.n; k++) gmx = Math.max(gmx, D.gas[k]);
      x.strokeStyle = "rgba(245,248,252,0.9)";
      x.lineWidth = 1;
      x.beginPath();
      for (let k = 0; k < D.n; k++) {
        const px = (k / (D.n - 1)) * w;
        const py = h - 2 - (D.gas[k] / gmx) * (h - 6);
        k ? x.lineTo(px, py) : x.moveTo(px, py);
      }
      x.stroke();
      x.strokeStyle = "rgba(240,166,198,0.9)";
      x.beginPath();
      // A temperature step zeroes the gas flux for a dozen micro-steps (the
      // overflow law's driving force goes negative before the state catches up).
      // The CH4 *share* is undefined there, so break the line instead of
      // plotting 0/1e-9 = 0 — that drew a false plunge to zero.
      let pen = false;
      let gapAt = -1;
      for (let k = 0; k < D.n; k++) {
        const g = D.gas[k];
        if (!(g > 1e-6)) {
          if (pen) gapAt = k; // mark where the trace lost its denominator
          pen = false;
          continue;
        }
        const pr = D.ch4[k] / g;
        const px = (k / (D.n - 1)) * w;
        const py = h - 2 - pr * (h - 6);
        if (pen) x.lineTo(px, py);
        else x.moveTo(px, py);
        pen = true;
      }
      x.stroke();
      if (gapAt >= 0) {
        // a hairline at the gap, so the break reads as "undefined here"
        const gx2 = (gapAt / (D.n - 1)) * w;
        x.strokeStyle = "rgba(240,166,198,0.35)";
        x.lineWidth = 1;
        x.setLineDash([2, 3]);
        x.beginPath();
        x.moveTo(gx2, 2);
        x.lineTo(gx2, h - 2);
        x.stroke();
        x.setLineDash([]);
      }
      const cx = (D.i / (D.n - 1)) * w;
      x.strokeStyle = "rgba(255,255,255,0.35)";
      x.beginPath();
      x.moveTo(cx, 0);
      x.lineTo(cx, h);
      x.stroke();
    }
  }

  {
    const p = panelCtx(PC.pStep);
    if (p) {
      const { x, w, h } = p;
      x.clearRect(0, 0, w, h);
      let mx = 1e-12;
      for (let k = 0; k < D.n; k++) mx = Math.max(mx, D.h[k]);
      x.strokeStyle = "rgba(245,248,252,0.9)";
      x.lineWidth = 1;
      x.beginPath();
      for (let k = 0; k < D.n; k++) {
        const f = D.h[k] > 0 ? Math.log10(D.h[k] / 1e-8) / Math.log10(mx / 1e-8) : 0;
        const px = (k / (D.n - 1)) * w;
        const py = h - 2 - clamp(f, 0, 1) * (h - 6);
        k ? x.lineTo(px, py) : x.moveTo(px, py);
      }
      x.stroke();
      const cx = (D.i / (D.n - 1)) * w;
      x.strokeStyle = "rgba(255,255,255,0.35)";
      x.beginPath();
      x.moveTo(cx, 0);
      x.lineTo(cx, h);
      x.stroke();
    }
  }
}

/* ------------------------------------------------------------ user camera */

function zoomAt(px, py, factor) {
  const z0 = cam.z;
  const z1 = clamp(z0 * factor, 0.55, 9);
  if (z1 === z0) return;
  const c = Math.cos(cam.r), s = Math.sin(cam.r);
  const dx = px - W / 2, dy = py - H / 2;
  // keep the point under the cursor fixed: invert the camera transform
  const wx = cam.x + (c * dx + s * dy) / z0;
  const wy = cam.y + (-s * dx + c * dy) / z0;
  cam.z = z1;
  cam.x = wx - (c * dx + s * dy) / z1;
  cam.y = wy - (-s * dx + c * dy) / z1;
  clampCam();
}

function bindView() {
  stage.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      handOver();
      const r = stage.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0012));
    },
    { passive: false }
  );

  let drag = null;
  stage.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    handOver();
    drag = { x: e.clientX, y: e.clientY };
    stage.classList.add("grabbing");
    try {
      stage.setPointerCapture(e.pointerId);
    } catch (_) {
      /* ignore */
    }
  });
  stage.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX;
    drag.y = e.clientY;
    const c = Math.cos(cam.r), s = Math.sin(cam.r);
    cam.x -= (c * dx + s * dy) / cam.z;
    cam.y -= (-s * dx + c * dy) / cam.z;
    clampCam();
  });
  const endDrag = (e) => {
    drag = null;
    stage.classList.remove("grabbing");
    try {
      stage.releasePointerCapture(e.pointerId);
    } catch (_) {
      /* ignore */
    }
  };
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);

  stage.addEventListener("dblclick", () => {
    handOver();
    cam.x = G.cx;
    cam.y = G.cy;
    cam.z = 1;
    cam.r = 0;
  });
}

/* ------------------------------------------------------------------- boot */

window.addEventListener("resize", () => {
  resize();
  buildShots();
  last = 0;
});

(async function boot() {
  window.addEventListener("error", (e) => {
    const el = document.getElementById("stBig");
    if (el) el.textContent = "ERR " + e.message;
  });

  resize();
  const q = new URLSearchParams(location.search);
  if (q.has("shot")) {
    forceShot = Math.max(0, parseInt(q.get("shot"), 10) || 0);
    freezeCam = true;
  }
  buildShots();
  bindView();
  bindScrub();
  requestAnimationFrame(frame);

  try {
    await loadData();
    for (let i = 0; i < NSTATE; i++) paintCard(i);
    for (let i = 0; i < NTILE; i++) paintTile(i);
    document.title = "PyADM1ODE · " + D.label;
    seedLamps();

    // ?t=40 jumps the clock (demo / screenshot aid)
    const at = parseFloat(q.get("t"));
    if (isFinite(at)) {
      D.clock = clamp(at, 2.0, D.tMax - 0.5);
      let i = 0;
      while (i + 1 < D.n && D.t[i + 1] <= D.clock) i++;
      D.i = i;
      seedLamps();
      drawHud();
    }
  } catch (e) {
    document.getElementById("stBig").textContent = "trace load failed";
    console.error(e);
  }

  if (q.has("still")) {
    // freeze the clock, keep repainting: a stopped rAF loop can be captured as
    // an empty canvas by headless browsers. Force one HUD pass first so the
    // numbers are on screen before the freeze. ?freeze=<ms> lets a screenshot
    // accumulate more animation history before it locks.
    const fz = parseFloat(q.get("freeze"));
    const delay = Number.isFinite(fz) ? fz : 700;
    setTimeout(() => {
      hudT = 0;
      frozen = false;
      drawHud();
      frozen = true;
    }, delay);
  }
})();
