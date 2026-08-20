const $ = (id) => document.getElementById(id);

let currentId = null;
let lastLog = [];

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 4200);
}

function setBusy(btn, busy, label) {
  btn.disabled = busy;
  if (label) btn.dataset.prev = btn.dataset.prev || btn.textContent;
  if (busy) btn.textContent = "…";
  else if (btn.dataset.prev) btn.textContent = btn.dataset.prev;
}

function evidenceItem(item) {
  const li = document.createElement("li");
  li.innerHTML = `<div>${escapeHtml(item.statement)}</div>
    <span class="w">weight ${Number(item.weight).toFixed(2)}</span>
    ${item.source_url ? `<a class="src" href="${item.source_url}" target="_blank" rel="noreferrer">${escapeHtml(item.source_title || item.source_url)}</a>` : ""}`;
  return li;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

async function refreshList() {
  const claims = await api("/api/claims");
  const ul = $("claim-list");
  ul.innerHTML = "";
  claims.forEach((c) => {
    const li = document.createElement("li");
    if (c.id === currentId) li.classList.add("active");
    li.innerHTML = `<div>${escapeHtml(c.title)}</div>
      <div class="claim-meta">${c.confidence == null ? "—" : c.confidence.toFixed(1) + "%"} · ${c.commit_count} commits</div>`;
    li.onclick = () => selectClaim(c.id);
    ul.appendChild(li);
  });
  if (!currentId && claims[0]) selectClaim(claims[0].id);
}

async function selectClaim(id) {
  currentId = id;
  $("empty").classList.add("hidden");
  $("claim-view").classList.remove("hidden");
  const data = await api(`/api/claims/${id}`);
  const head = data.head;
  const state = head?.state || data.working;
  $("claim-id").textContent = id;
  $("claim-title").textContent = state?.statement || data.claim.title;
  $("claim-refined").textContent = state?.refined_statement || "";
  $("claim-reason").textContent = head?.reason || "";
  const conf = state?.confidence ?? 0;
  $("conf-value").textContent = `${conf.toFixed(1)}%`;
  $("conf-fill").style.width = `${conf}%`;
  fillList($("ev-for"), state?.evidence_for || [], evidenceItem);
  fillList($("ev-against"), state?.evidence_against || [], evidenceItem);
  fillList($("unknowns"), state?.unknowns || [], (u) => {
    const li = document.createElement("li");
    li.innerHTML = `<div>${escapeHtml(u.question)}</div><span class="w">${escapeHtml(u.why_it_matters)}</span>`;
    return li;
  });
  fillList($("predictions"), state?.predictions || [], (p) => {
    const li = document.createElement("li");
    li.innerHTML = `<div>${escapeHtml(p.statement)}</div><span class="w">${p.status}${p.due ? " · " + p.due : ""}</span>`;
    return li;
  });
  $("summary").textContent = state?.summary || "";
  lastLog = await api(`/api/claims/${id}/log`);
  renderLog(lastLog);
  await refreshList();
}

function fillList(ul, items, render) {
  ul.innerHTML = "";
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
    const arrow = prev == null ? "" : ` ${prev.toFixed(1)}% → ${c.new_confidence.toFixed(1)}%`;
    li.innerHTML = `<span class="sha">${c.id.slice(0, 12)}</span>
      <span class="msg">${escapeHtml(c.message)}${arrow}</span>
      <span class="why">${escapeHtml(c.reason)}</span>
      <span class="status">${escapeHtml(c.author)} · ${c.created_at}</span>`;
    li.onclick = async () => {
      if (i + 1 >= log.length) {
        showDetail("diff", JSON.stringify(await api(`/api/claims/${currentId}/diff?b=${c.id}`), null, 2));
        return;
      }
      const parent = log[i + 1];
      const diff = await api(`/api/claims/${currentId}/diff?a=${parent.id}&b=${c.id}`);
      showDetail("diff " + parent.id.slice(0, 8) + ".." + c.id.slice(0, 8), formatDiff(diff));
    };
    ol.appendChild(li);
  });
}

function formatDiff(diff) {
  const lines = [
    `OLD ${diff.previous_confidence ?? "∅"}%`,
    `NEW ${diff.new_confidence ?? "∅"}%  (${diff.confidence_delta >= 0 ? "+" : ""}${diff.confidence_delta})`,
    "",
    "Reason:",
    diff.reason || "(none)",
    "",
    ...diff.changes.map((ch) => {
      if (ch.kind === "added") return `+ ${ch.path}`;
      if (ch.kind === "removed") return `- ${ch.path}`;
      return `~ ${ch.path}`;
    }),
  ];
  return lines.join("\n");
}

function showDetail(title, body) {
  $("detail").classList.remove("hidden");
  $("detail-title").textContent = title;
  $("detail-body").textContent = body;
}

$("open-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("open-btn");
  setBusy(btn, true);
  try {
    const statement = $("claim-input").value.trim();
    toast("Surveying the live web… this takes a bit.");
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
  }
});

$("watch-btn").onclick = async () => {
  const btn = $("watch-btn");
  setBusy(btn, true);
  toast("Watching the internet…");
  try {
    const result = await api(`/api/claims/${currentId}/watch`, { method: "POST" });
    await selectClaim(currentId);
    if (result.changed) {
      showDetail("watch diff", formatDiff(result.diff));
      toast(result.detail || "Belief updated.");
    } else {
      toast(result.detail || "No new evidence.");
    }
  } catch (err) {
    toast(err.message);
  } finally {
    setBusy(btn, false);
  }
};

$("diff-btn").onclick = async () => {
  if (lastLog.length < 2) {
    toast("Need at least two commits.");
    return;
  }
  const [head, parent] = lastLog;
  const diff = await api(`/api/claims/${currentId}/diff?a=${parent.id}&b=${head.id}`);
  showDetail("claim.diff()", formatDiff(diff));
};

$("blame-btn").onclick = async () => {
  const blame = await api(`/api/claims/${currentId}/blame`);
  const body = blame.entries.map((e) =>
    `${e.path}\n  introduced ${e.introduced_in.slice(0, 12)} (${e.author})\n  last ${e.last_changed_in.slice(0, 12)} (${e.last_author})`
  ).join("\n\n");
  showDetail("claim.blame()", body || "empty");
};

$("revert-btn").onclick = async () => {
  if (lastLog.length < 2) {
    toast("Nothing to revert to.");
    return;
  }
  const target = lastLog[1];
  const commit = await api(`/api/claims/${currentId}/revert`, {
    method: "POST",
    body: JSON.stringify({ sha: target.id }),
  });
  await selectClaim(currentId);
  toast("Reverted to " + commit.parent_id.slice(0, 12));
};

refreshList().catch((err) => toast(err.message));
