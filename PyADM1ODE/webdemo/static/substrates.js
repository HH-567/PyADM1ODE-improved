/* 底物库：自定义底物（模板 + 覆盖字段） */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { items: [], fields: [], editing: null };

function setStatus(text, cls) {
  const el = $("status");
  el.textContent = text;
  el.className = "status" + (cls ? " " + cls : "");
}

async function post(url, body, method = "POST") {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: method === "DELETE" ? undefined : JSON.stringify(body || {}),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok || j.ok === false) throw new Error(j.detail || `HTTP ${res.status}`);
  return j;
}

function buildFields(fields) {
  $("fields").innerHTML = fields
    .map(
      (f) => `<div class="field">
        <label for="f-${f.key}">${f.label} <span class="unit">${f.unit || ""}</span></label>
        <input id="f-${f.key}" type="number" step="any" placeholder="留空沿用模板" />
      </div>`
    )
    .join("");
}

function fillForm(item) {
  state.editing = item ? item.id : null;
  $("form-title").textContent = item ? `编辑：${item.name}` : "新增底物";
  $("k-editing").textContent = item ? item.name : "新底物";
  $("name").value = item ? item.name : "";
  $("note").value = item && item.note ? item.note : "";
  $("based_on").value = item && item.based_on ? item.based_on : "maize_silage_milk_ripeness";
  for (const f of state.fields) {
    const el = $("f-" + f.key);
    const v = item && item.values ? item.values[f.key] : undefined;
    el.value = v === undefined || v === null ? "" : v;
  }
}

function renderTable() {
  const head = ["名称", "ID", "类型", "TS", "蛋白", "脂肪", "纤维", "灰分", "操作"];
  const rows = state.items
    .map((it) => {
      const v = it.values || {};
      const num = (x) => (x === undefined || x === null ? "—" : Number(x).toFixed(3));
      return `<tr>
        <td>${it.name}</td>
        <td style="color:var(--muted)">${it.id}</td>
        <td><span class="tag ${it.custom ? "custom" : ""}">${it.custom ? "自定义" : "内置"}</span></td>
        <td>${num(v.TS)}</td>
        <td>${num(v.fRP)}</td>
        <td>${num(v.fRFe)}</td>
        <td>${num(v.fRF)}</td>
        <td>${num(v.fRA)}</td>
        <td>
          <button class="btn-mini" data-edit="${it.id}">编辑</button>
          ${it.custom ? `<button class="btn-mini danger" data-del="${it.id}">删除</button>` : ""}
        </td>
      </tr>`;
    })
    .join("");
  $("table").innerHTML = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows}</tbody>`;
  $("table").querySelectorAll("[data-edit]").forEach((b) =>
    b.addEventListener("click", () => {
      const it = state.items.find((x) => x.id === b.dataset.edit);
      fillForm(it);
    })
  );
  $("table").querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await post("/api/substrates/" + encodeURIComponent(b.dataset.del), null, "DELETE");
        await load();
        setStatus("已删除 " + b.dataset.del, "ok");
      } catch (e) {
        setStatus("删除失败：" + e.message, "err");
      }
    })
  );
}

async function load() {
  const res = await fetch("/api/substrates");
  const j = await res.json();
  if (!res.ok || !j.ok) throw new Error(j.detail || "读取失败");
  state.items = j.items;
  if (!state.fields.length) {
    state.fields = j.fields;
    buildFields(j.fields);
    $("based_on").innerHTML = j.items
      .filter((x) => !x.custom)
      .map((x) => `<option value="${x.id}">${x.name}</option>`)
      .join("");
  }
  $("k-total").textContent = j.items.length;
  $("k-custom").textContent = j.items.filter((x) => x.custom).length;
  renderTable();
}

async function save() {
  const values = {};
  for (const f of state.fields) {
    const raw = $("f-" + f.key).value;
    if (raw !== "") values[f.key] = Number(raw);
  }
  const payload = {
    id: state.editing,
    name: $("name").value.trim(),
    based_on: $("based_on").value,
    note: $("note").value.trim(),
    values,
  };
  if (!payload.name) {
    setStatus("请填底物名称", "err");
    return;
  }
  try {
    $("save").disabled = true;
    const j = await post("/api/substrates", payload);
    await load();
    fillForm(null);
    setStatus(`已保存：${j.item.name}（${j.item.id}）`, "ok");
  } catch (e) {
    setStatus("保存失败：" + e.message, "err");
  } finally {
    $("save").disabled = false;
  }
}

$("save").addEventListener("click", save);
$("reset").addEventListener("click", () => fillForm(null));

load()
  .then(() => setStatus("已载入底物库。内置底物不可改，选中后另存为自定义即可。", "ok"))
  .catch((e) => setStatus("载入失败：" + e.message, "err"));
