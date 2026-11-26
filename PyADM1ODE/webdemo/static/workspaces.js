/* 工作区管理 */
"use strict";

const $ = (id) => document.getElementById(id);
let state = { items: [], active: null };

function setStatus(t, c) {
  const el = $("status");
  el.textContent = t;
  el.className = "status" + (c ? " " + c : "");
}

async function req(url, method = "GET", body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok || j.ok === false) throw new Error(j.detail || `HTTP ${res.status}`);
  return j;
}

function ago(ts) {
  if (!ts) return "—";
  const mins = Math.round((Date.now() / 1000 - ts) / 60);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.round(hours / 24)} 天前`;
}

function render() {
  const a = state.active || {};
  $("k-total").textContent = state.items.length;
  $("k-active").textContent = a.name || "—";
  $("active-info").textContent = a.id
    ? `${a.name}（${a.id}）\n第 ${(a.summary && a.summary.day) || 0} 天 · 甲烷 ${((a.summary && a.summary.q_ch4) || 0).toFixed(0)} m³/d\n更新于 ${ago(a.updated)}`
    : "还没有工作区，先建一个。";
  $("active-info").style.whiteSpace = "pre-line";

  const rows = state.items
    .map(
      (w) => `<tr class="${w.active ? "on" : ""}">
        <td>${w.name} ${w.active ? '<span class="badge-on">当前</span>' : ""}</td>
        <td style="color:var(--muted)">${w.id}</td>
        <td>第 ${(w.summary && w.summary.day) || 0} 天</td>
        <td>${((w.summary && w.summary.q_ch4) || 0).toFixed(0)}</td>
        <td style="color:var(--muted)">${ago(w.updated)}</td>
        <td>
          <button class="btn-mini" data-open="${w.id}">打开</button>
          <button class="btn-mini" data-rename="${w.id}">改名</button>
          <button class="btn-mini danger" data-del="${w.id}">删除</button>
        </td>
      </tr>`
    )
    .join("");
  $("table").innerHTML = `<thead><tr><th>名称</th><th>目录</th><th>进度</th><th>甲烷 m³/d</th><th>更新</th><th>操作</th></tr></thead><tbody>${rows}</tbody>`;

  $("table").querySelectorAll("[data-open]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await req(`/api/workspaces/${encodeURIComponent(b.dataset.open)}/open`, "POST");
        await load();
        setStatus("已切换工作区。打开「按天推进」会接着这个进度跑。", "ok");
      } catch (e) {
        setStatus("切换失败：" + e.message, "err");
      }
    })
  );
  $("table").querySelectorAll("[data-rename]").forEach((b) =>
    b.addEventListener("click", async () => {
      const cur = state.items.find((x) => x.id === b.dataset.rename);
      const name = prompt("新名称", cur ? cur.name : "");
      if (!name) return;
      try {
        await req(`/api/workspaces/${encodeURIComponent(b.dataset.rename)}/rename`, "POST", { name });
        await load();
        setStatus("已改名。", "ok");
      } catch (e) {
        setStatus("改名失败：" + e.message, "err");
      }
    })
  );
  $("table").querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm(`删除工作区 ${b.dataset.del}？里面的装置状态和曲线会一起删掉。`)) return;
      try {
        await req(`/api/workspaces/${encodeURIComponent(b.dataset.del)}`, "DELETE");
        await load();
        setStatus("已删除。", "ok");
      } catch (e) {
        setStatus("删除失败：" + e.message, "err");
      }
    })
  );
}

async function load() {
  const j = await req("/api/workspaces");
  state.items = j.items;
  state.active = j.active;
  $("k-store").textContent = `${j.items.length} 个目录`;
  render();
}

$("create").addEventListener("click", async () => {
  const name = $("name").value.trim();
  if (!name) {
    setStatus("先给工作区起个名字", "err");
    return;
  }
  try {
    const j = await req("/api/workspaces", "POST", { name, note: $("note").value.trim() });
    $("name").value = "";
    $("note").value = "";
    await load();
    setStatus(`已创建并切换到「${j.name}」。`, "ok");
  } catch (e) {
    setStatus("创建失败：" + e.message, "err");
  }
});

load().catch((e) => setStatus("载入失败：" + e.message, "err"));
