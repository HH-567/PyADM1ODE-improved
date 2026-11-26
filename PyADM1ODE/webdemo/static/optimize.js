/* 加料推荐 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { subs: [], busy: false };

function setStatus(t, c) {
  const el = $("status");
  el.textContent = t;
  el.className = "status" + (c ? " " + c : "");
}

function addRow(id, amount) {
  const row = document.createElement("div");
  row.className = "feed-row";
  row.innerHTML = `
    <select>${state.subs.map((s) => `<option value="${s.id}" ${s.id === id ? "selected" : ""}>${s.name}</option>`).join("")}</select>
    <input type="number" step="0.5" min="0" value="${amount}" />`;
  $("feeds").appendChild(row);
}

function readPlan() {
  const ids = [];
  const flows = [];
  for (const row of $("feeds").querySelectorAll(".feed-row")) {
    const id = row.querySelector("select").value;
    const q = parseFloat(row.querySelector("input").value) || 0;
    if (q > 0 && !ids.includes(id)) {
      ids.push(id);
      flows.push(q);
    }
  }
  return { ids, flows };
}

async function run() {
  const { ids, flows } = readPlan();
  if (!ids.length) {
    setStatus("至少要给一种料一个正数", "err");
    return;
  }
  state.busy = true;
  $("run").disabled = true;
  setStatus("正在评估多组配料……（每次评估要跑几十天，约 2–10 秒）", "busy");
  try {
    const res = await fetch("/api/optimize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        substrate_ids: ids,
        flows,
        days: parseInt($("days").value, 10) || 30,
        limits: {
          ph_min: parseFloat($("phlo").value),
          vfa_tac_max: parseFloat($("vfa").value),
          olr_max: parseFloat($("olr").value),
          hrt_min: parseFloat($("hrt").value),
        },
      }),
    });
    const j = await res.json();
    if (!res.ok || !j.ok) throw new Error(j.detail || `HTTP ${res.status}`);
    render(j);
    setStatus(`评估了 ${j.evals.length} 组配料，用时 ${j.elapsed_s} 秒。`, "ok");
  } catch (e) {
    setStatus("计算失败：" + e.message, "err");
  } finally {
    state.busy = false;
    $("run").disabled = false;
  }
}

function render(j) {
  const c = j.current;
  const r = j.recommended;
  $("k-cur").textContent = c.q_ch4.toFixed(0);
  $("k-new").textContent = r.q_ch4.toFixed(0);
  $("k-gain").textContent = (j.gain_pct >= 0 ? "+" : "") + j.gain_pct.toFixed(1) + "%";
  $("k-total").textContent = r.total.toFixed(1);
  $("k-olr").textContent = r.olr.toFixed(2);
  $("k-time").textContent = j.elapsed_s + " s";

  const banner = $("binding");
  if (r.violations.length) {
    banner.className = "banner warn";
    banner.textContent = "推荐方案仍不满足：" + r.violations.join("；") + "。建议同时降低总量或换料。";
  } else {
    banner.className = "banner";
    banner.textContent = `卡住继续加料的是：${j.binding}。再往上加会先突破这一条。`;
  }

  const names = j.substrates.map((s) => s.name);
  const rows = j.substrates
    .map((s, i) => {
      const before = c.flows[i] === undefined ? 0 : c.flows[i];
      const after = r.flows[i];
      const delta = before > 0 ? ((after - before) / before) * 100 : 0;
      return `<tr><td>${s.name}</td><td>${before.toFixed(2)}</td><td>${after.toFixed(2)}</td>
        <td style="color:${delta >= 0 ? "#6fe3d4" : "#ffb45a"}">${delta >= 0 ? "+" : ""}${delta.toFixed(0)}%</td></tr>`;
    })
    .join("");

  $("table").innerHTML = `
    <thead><tr><th>底物</th><th>当前 m³/d</th><th>推荐 m³/d</th><th>变化</th></tr></thead>
    <tbody>${rows}</tbody>
    <tfoot>
      <tr><th>合计</th><th>${c.total}</th><th>${r.total}</th><th></th></tr>
      <tr><td>甲烷 m³/d</td><td>${c.q_ch4}</td><td>${r.q_ch4}</td><td>${j.gain_pct}%</td></tr>
      <tr><td>pH</td><td>${c.ph}</td><td>${r.ph}</td><td></td></tr>
      <tr><td>VFA/TAC</td><td>${c.vfa_tac}</td><td>${r.vfa_tac}</td><td></td></tr>
      <tr><td>OLR</td><td>${c.olr}</td><td>${r.olr}</td><td></td></tr>
      <tr><td>HRT d</td><td>${c.hrt}</td><td>${r.hrt}</td><td></td></tr>
      <tr><td colspan="4" style="color:var(--muted); white-space:normal">${j.note}</td></tr>
    </tfoot>`;
}

$("run").addEventListener("click", run);

(async function boot() {
  const res = await fetch("/api/substrates");
  const j = await res.json();
  state.subs = j.items;
  addRow("maize_silage_milk_ripeness", 11.4);
  addRow("swine_manure", 6.1);
  addRow("cattle_manure", 5.0);
})();
