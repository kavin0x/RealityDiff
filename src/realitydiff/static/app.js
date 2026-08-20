const $ = (id) => document.getElementById(id);

let currentId = null;
let lastLog = [];
let claimsCache = [];
let overlayTimer = null;

function errorMessage(data, fallback) {
  if (!data) return fallback;
  const detail = data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || item.message || JSON.stringify(item)).join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return fallback;
}

async function api(path, opts = {}) {
  const { timeoutMs = 240000, ...fetchOpts } = opts;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...fetchOpts,
      signal: controller.signal,
    });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    if (!res.ok) throw new Error(errorMessage(data, res.statusText));
    return data;
  } catch (err) {
    if (err.name === "AbortError") throw new Error("Request timed out. Try again.");
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

function toast(msg) {
  const el = $("toast");
  el.textContent = String(msg || "").slice(0, 400);
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 4800);
}

function setBusy(btn, busy) {
  btn.disabled = busy;
  btn.dataset.prev = btn.dataset.prev || btn.textContent;
  btn.textContent = busy ? "…" : btn.dataset.prev;
}

function showOverlay(title, sub) {
  $("overlay-title").textContent = title;
  $("overlay-sub").innerHTML = `${escapeHtml(sub)} <span id="elapsed">0s</span>`;
  $("overlay").classList.remove("hidden");
  const started = Date.now();
  clearInterval(overlayTimer);
  overlayTimer = setInterval(() => {
    const el = $("elapsed");
    if (el) el.textContent = `${Math.round((Date.now() - started) / 1000)}s`;
  }, 250);
}

function hideOverlay() {
  clearInterval(overlayTimer);
  $("overlay").classList.add("hidden");
}

function evidenceItem(item) {
  const li = document.createElement("li");
  const notes = item.notes ? `<span class="notes">${escapeHtml(item.notes)}</span>` : "";
  li.innerHTML = `<div>${escapeHtml(item.statement)}</div>
    <span class="w">weight ${Number(item.weight).toFixed(2)}</span>
    ${item.source_url ? `<a class="src" href="${escapeAttr(item.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.source_title || item.source_url)}</a>` : ""}
    ${notes}`;
  return li;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/`/g, "");
}

function relativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const delta = Date.now() - then;
  const mins = Math.round(delta / 60000);
  if (Math.abs(mins) < 1) return "just now";
  if (Math.abs(mins) < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (Math.abs(hours) < 48) return `${hours}h ago`;
  return new Date(iso).toISOString().slice(0, 16).replace("T", " ") + "Z";
}

function sparkPath(values, w = 120, h = 28) {
  if (!values.length) return "";
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 100);
  const span = Math.max(1, max - min);
  return values.map((v, i) => {
    const x = values.length === 1 ? w / 2 : (i / (values.length - 1)) * w;
    const y = h - ((v - min) / span) * (h - 4) - 2;
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
}

function renderSpark(el, values) {
  const d = sparkPath(values);
  el.innerHTML = d
    ? `<path d="${d}" fill="none" stroke="#b6e388" stroke-width="1.5"/>`
    : "";
}

async function refreshList() {
  claimsCache = await api("/api/claims", { timeoutMs: 15000 });
  renderClaimList();
  if (!currentId && claimsCache[0]) selectClaim(claimsCache[0].id);
}

function renderClaimList() {
  const q = ($("claim-filter").value || "").toLowerCase();
  const ul = $("claim-list");
  ul.innerHTML = "";
  claimsCache.filter((c) => !q || (c.title || "").toLowerCase().includes(q) || c.id.includes(q)).forEach((c) => {
    const li = document.createElement("li");
    if (c.id === currentId) li.classList.add("active");
    li.innerHTML = `<div>${escapeHtml(c.title)}</div>
      <div class="claim-meta"><span>${c.confidence == null ? "—" : c.confidence.toFixed(1) + "%"}</span>
      <span>${c.commit_count} commits</span></div>`;
    li.onclick = () => selectClaim(c.id);
    ul.appendChild(li);
  });
}

async function selectClaim(id) {
  currentId = id;
  location.hash = id;
  $("empty").classList.add("hidden");
  $("claim-view").classList.remove("hidden");
  const data = await api(`/api/claims/${id}`, { timeoutMs: 15000 });
  const head = data.head;
  const state = head?.state || data.working;
  $("claim-id").textContent = data.claim.id;
  $("claim-title").textContent = state?.statement || data.claim.title;
  $("claim-refined").textContent = state?.refined_statement || "";
  $("claim-reason").textContent = head?.reason || "";
  const conf = state?.confidence ?? 0;
  $("conf-value").textContent = `${Number(conf).toFixed(1)}%`;
  $("conf-fill").style.width = `${conf}%`;
  $("watching-toggle").checked = Boolean(data.claim.watching);
  $("watching-chip").textContent = data.claim.watching ? "watching" : "paused";
  $("watching-chip").classList.toggle("off", !data.claim.watching);
  $("dirty-chip").classList.toggle("hidden", !data.dirty);
  fillList($("ev-for"), state?.evidence_for || [], evidenceItem, $("count-for"));
  fillList($("ev-against"), state?.evidence_against || [], evidenceItem, $("count-against"));
  fillList($("unknowns"), state?.unknowns || [], (u) => {
    const li = document.createElement("li");
    li.innerHTML = `<div>${escapeHtml(u.question)}</div><span class="w">${escapeHtml(u.why_it_matters)}</span>`;
    return li;
  }, $("count-unknowns"));
  fillList($("predictions"), state?.predictions || [], (p) => {
    const li = document.createElement("li");
    li.innerHTML = `<div>${escapeHtml(p.statement)} <span class="pred-status ${escapeAttr(p.status)}">${escapeHtml(p.status)}</span></div>
      <span class="w">${p.due ? escapeHtml(p.due) : "no due date"}${p.how_to_falsify ? " · " + escapeHtml(p.how_to_falsify) : ""}</span>`;
    return li;
  }, $("count-predictions"));
  $("summary").textContent = state?.summary || "";
  lastLog = await api(`/api/claims/${id}/log`, { timeoutMs: 15000 });
  const spark = [...lastLog].reverse().map((c) => c.new_confidence);
  renderSpark($("spark"), spark);
  const prev = lastLog[1]?.new_confidence;
  const deltaEl = $("conf-delta");
  if (prev == null) {
    deltaEl.textContent = "initial HEAD";
    deltaEl.className = "conf-delta";
  } else {
    const d = Math.round((conf - prev) * 10) / 10;
    deltaEl.textContent = `${d >= 0 ? "+" : ""}${d} from previous commit`;
    deltaEl.className = "conf-delta " + (d > 0 ? "up" : d < 0 ? "down" : "");
  }
  renderLog(lastLog);
  await refreshList();
}

function fillList(ul, items, render, countEl) {
  ul.innerHTML = "";
  if (countEl) countEl.textContent = String(items.length);
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "status";
    li.textContent = "none yet";
    ul.appendChild(li);
    return;
  }
  items.forEach((item) => ul.appendChild(render(item)));
}

function renderLog(log) {
  $("log-meta").textContent = `${log.length} commits`;
  const ol = $("commit-log");
  ol.innerHTML = "";
  log.forEach((c, i) => {
    const li = document.createElement("li");
    const prev = c.previous_confidence;
    let arrow = "";
    if (prev != null) {
      const d = c.new_confidence - prev;
      const cls = d > 0 ? "up" : d < 0 ? "down" : "";
      arrow = ` <span class="arrow ${cls}">${prev.toFixed(1)}% → ${c.new_confidence.toFixed(1)}%</span>`;
    }
    li.innerHTML = `<span class="sha">${c.id.slice(0, 12)}</span>
      <span class="msg">${escapeHtml(c.message)}${arrow}</span>
      <span class="why">${escapeHtml((c.reason || "").slice(0, 220))}${(c.reason || "").length > 220 ? "…" : ""}</span>
      <span class="status">${escapeHtml(c.author)} · ${relativeTime(c.created_at)}</span>`;
    li.onclick = async () => {
      if (i + 1 >= log.length) {
        showDetail("root commit", formatDiffHtml(await api(`/api/claims/${currentId}/diff?b=${c.id}`)));
        return;
      }
      const parent = log[i + 1];
      const diff = await api(`/api/claims/${currentId}/diff?a=${parent.id}&b=${c.id}`);
      showDetail(`diff ${parent.id.slice(0, 8)}..${c.id.slice(0, 8)}`, formatDiffHtml(diff));
    };
    ol.appendChild(li);
  });
}

function snip(value) {
  if (value == null) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return escapeHtml(text.slice(0, 500)) + (text.length > 500 ? "…" : "");
}

function formatDiffHtml(diff) {
  const delta = diff.confidence_delta ?? 0;
  const hunks = (diff.changes || []).map((ch) => {
    const kind = ch.kind === "removed" ? "removed" : ch.kind;
    const body = kind === "added"
      ? snip(ch.after)
      : kind === "removed"
        ? snip(ch.before)
        : `− ${snip(ch.before)}\n+ ${snip(ch.after)}`;
    return `<div class="hunk ${kind}"><div class="path">${kind === "added" ? "+" : kind === "removed" ? "−" : "~"} ${escapeHtml(ch.path)}</div><div class="snip">${body}</div></div>`;
  }).join("");
  return `<div class="hunk changed"><div class="path">confidence ${diff.previous_confidence ?? "∅"}% → ${diff.new_confidence ?? "∅"}% (${delta >= 0 ? "+" : ""}${delta})</div><div class="snip">${escapeHtml(diff.reason || "(none)")}</div></div>${hunks || "<p class='status'>no field changes</p>"}`;
}

function formatBlameHtml(blame) {
  if (!blame.entries?.length) return "<p class='status'>empty</p>";
  return blame.entries.map((e) =>
    `<div class="blame-row"><div class="path">${escapeHtml(e.path)}</div>
     <div class="status">introduced ${escapeHtml(e.introduced_in.slice(0, 12))} (${escapeHtml(e.author)})</div>
     <div class="status">last ${escapeHtml(e.last_changed_in.slice(0, 12))} (${escapeHtml(e.last_author)})</div></div>`
  ).join("");
}

function showDetail(title, html) {
  $("detail").classList.remove("hidden");
  $("detail-title").textContent = title;
  $("detail-body").innerHTML = html;
}

$("open-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("open-btn");
  setBusy(btn, true);
  showOverlay("Surveying the internet", "Grok is searching the web and X…");
  try {
    const statement = $("claim-input").value.trim();
    const created = await api("/api/claims", {
      method: "POST",
      body: JSON.stringify({ statement }),
    });
    $("claim-input").value = "";
    await selectClaim(created.claim.id);
    toast("Initial belief committed.");
  } catch (err) {
    toast(err.message);
  } finally {
    setBusy(btn, false);
    hideOverlay();
  }
});

$("watch-btn").onclick = async () => {
  if (!currentId) return;
  const btn = $("watch-btn");
  setBusy(btn, true);
  showOverlay("Watching the internet", "Looking for new evidence…");
  try {
    const result = await api(`/api/claims/${currentId}/watch`, { method: "POST" });
    await selectClaim(currentId);
    if (result.changed) {
      showDetail("watch diff", formatDiffHtml(result.diff));
      toast(result.detail || "Belief updated.");
    } else {
      toast(result.detail || "No new evidence.");
    }
  } catch (err) {
    toast(err.message);
  } finally {
    setBusy(btn, false);
    hideOverlay();
  }
};

$("diff-btn").onclick = async () => {
  if (!currentId) return;
  if (lastLog.length < 2) {
    toast("Need at least two commits.");
    return;
  }
  const [head, parent] = lastLog;
  const diff = await api(`/api/claims/${currentId}/diff?a=${parent.id}&b=${head.id}`);
  showDetail("claim.diff()", formatDiffHtml(diff));
};

$("blame-btn").onclick = async () => {
  if (!currentId) return;
  const blame = await api(`/api/claims/${currentId}/blame`);
  showDetail("claim.blame()", formatBlameHtml(blame));
};

$("revert-btn").onclick = async () => {
  if (!currentId) return;
  if (lastLog.length < 2) {
    toast("Nothing to revert to.");
    return;
  }
  const target = lastLog[1];
  if (!window.confirm(`Revert HEAD to ${target.id.slice(0, 12)}? This writes a new commit.`)) return;
  const commit = await api(`/api/claims/${currentId}/revert`, {
    method: "POST",
    body: JSON.stringify({ sha: target.id }),
  });
  await selectClaim(currentId);
  toast("Reverted toward " + (commit.parent_id || "").slice(0, 12));
};

$("watching-toggle").onchange = async () => {
  if (!currentId) return;
  try {
    await api(`/api/claims/${currentId}/watching`, {
      method: "POST",
      body: JSON.stringify({ watching: $("watching-toggle").checked }),
    });
    await selectClaim(currentId);
  } catch (err) {
    toast(err.message);
  }
};

$("copy-id").onclick = async () => {
  if (!currentId) return;
  await navigator.clipboard.writeText(currentId);
  toast("Copied " + currentId);
};

$("detail-close").onclick = () => $("detail").classList.add("hidden");
$("claim-filter").addEventListener("input", renderClaimList);

window.addEventListener("hashchange", () => {
  const id = location.hash.replace(/^#/, "");
  if (id && id !== currentId) selectClaim(id).catch((err) => toast(err.message));
});

refreshList()
  .then(() => {
    const id = location.hash.replace(/^#/, "");
    if (id) return selectClaim(id);
  })
  .catch((err) => toast(err.message));
