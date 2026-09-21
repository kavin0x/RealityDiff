const $ = (id) => document.getElementById(id);

let currentId = null;
let browseSha = null;
let lastLog = [];
let claimsCache = [];
let overlayTimer = null;
let watchingClaims = new Set();
let listStatus = localStorage.getItem("rd-list-status") || "all";

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

function setWatchStatus(text) {
  const el = $("watch-status");
  if (!text) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.classList.remove("hidden");
  el.textContent = text;
}

function parseRoute() {
  const raw = location.hash.replace(/^#/, "");
  if (!raw) return { id: null, sha: null };
  const [id, sha] = raw.split("/");
  return { id: id || null, sha: sha || null };
}

function setRoute(id, sha) {
  currentId = id;
  browseSha = sha || null;
  location.hash = sha ? `${id}/${sha}` : id;
}

function weightPercent(weight) {
  const n = Number(weight);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n * 100)));
}

function evidenceItem(item) {
  const li = document.createElement("li");
  const notes = item.notes ? `<span class="notes">${escapeHtml(item.notes)}</span>` : "";
  const pct = weightPercent(item.weight);
  li.innerHTML = `<div>${escapeHtml(item.statement)}</div>
    <span class="weight" aria-hidden="true"><span class="weight-fill" style="width:${pct}%"></span></span>
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
  if (!currentId && !parseRoute().id && claimsCache[0]) selectClaim(claimsCache[0].id);
}

function matchesClaim(claim, query, status) {
  const q = String(query || "").toLowerCase();
  const textOk = !q || (claim.title || "").toLowerCase().includes(q) || String(claim.id || "").includes(q);
  if (!textOk) return false;
  if (status === "open") return !claim.closed;
  if (status === "closed") return Boolean(claim.closed);
  return true;
}

function visibleClaims() {
  const q = $("claim-filter") ? $("claim-filter").value : "";
  return claimsCache.filter((c) => matchesClaim(c, q, listStatus));
}

function renderClaimList() {
  const q = ($("claim-filter").value || "").toLowerCase();
  const ul = $("claim-list");
  ul.innerHTML = "";
  document.querySelectorAll("#status-filter [data-status]").forEach((btn) => {
    const status = btn.dataset.status;
    btn.classList.toggle("on", status === listStatus);
    btn.setAttribute("aria-selected", status === listStatus ? "true" : "false");
    const badge = btn.querySelector(".seg-n");
    if (badge) badge.textContent = String(claimsCache.filter((c) => matchesClaim(c, q, status)).length);
  });
  const rows = claimsCache.filter((c) => matchesClaim(c, q, listStatus));
  const heads = $("heads-count");
  if (heads) heads.textContent = String(rows.length);
  if (!rows.length) {
    const li = document.createElement("li");
    li.className = "empty-item";
    li.textContent = claimsCache.length ? "No claims match." : "No claims yet.";
    ul.appendChild(li);
    return;
  }
  rows.forEach((c) => {
    const li = document.createElement("li");
    li.tabIndex = 0;
    li.dataset.id = c.id;
    if (c.id === currentId) li.classList.add("active");
    if (c.closed) li.classList.add("closed");
    if (c.outcome_verdict) li.classList.add(c.outcome_verdict);
    const conf = c.confidence == null ? 0 : Number(c.confidence);
    const label = c.closed ? "closed · " + escapeHtml(c.outcome_verdict || "") : (c.confidence == null ? "—" : conf.toFixed(1) + "%");
    const watched = c.last_watched_at ? ` · watched ${escapeHtml(relativeTime(c.last_watched_at))}` : "";
    li.innerHTML = `<div>${escapeHtml(c.title)}</div>
      <div class="claim-meta"><span class="meter" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, conf))}%"></span></span>
      <span>${label}</span>
      <span>${c.commit_count} commits${watched}</span></div>`;
    const spark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    spark.setAttribute("class", "mini-spark");
    spark.setAttribute("viewBox", "0 0 120 28");
    spark.setAttribute("aria-hidden", "true");
    renderSpark(spark, c.spark || []);
    li.appendChild(spark);
    const open = () => {
      selectClaim(c.id);
      focusStageOnNarrow();
    };
    li.onclick = open;
    li.onkeydown = (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        open();
      }
    };
    ul.appendChild(li);
  });
}

async function selectClaim(id, sha) {
  currentId = id;
  browseSha = sha || null;
  if (location.hash.replace(/^#/, "") !== (sha ? `${id}/${sha}` : id)) {
    location.hash = sha ? `${id}/${sha}` : id;
  }
  $("empty").classList.add("hidden");
  $("claim-view").classList.remove("hidden");
  let data;
  let head;
  let state;
  let browsing = Boolean(sha);
  if (sha) {
    data = await api(`/api/claims/${id}/at/${sha}`, { timeoutMs: 15000 });
    head = data.commit;
    state = head?.state;
    $("browse-banner").classList.remove("hidden");
    $("browse-sha").textContent = (head?.id || sha).slice(0, 12);
  } else {
    data = await api(`/api/claims/${id}`, { timeoutMs: 15000 });
    head = data.head;
    state = head?.state || data.working;
    $("browse-banner").classList.add("hidden");
  }
  $("claim-id").textContent = data.claim.id;
  $("claim-title").textContent = state?.statement || data.claim.title;
  $("claim-refined").textContent = state?.refined_statement || "";
  $("claim-reason").textContent = head?.reason || "";
  const conf = state?.confidence ?? 0;
  $("conf-value").textContent = `${Number(conf).toFixed(1)}%`;
  $("conf-fill").style.width = `${conf}%`;
  const card = $("confidence-card");
  if (card) {
    card.classList.remove("low", "mid", "high");
    card.classList.add(Number(conf) >= 67 ? "high" : Number(conf) <= 33 ? "low" : "mid");
  }
  const closed = isClosedClaim(data.claim, state);
  const verdict = data.claim.outcome_verdict || state?.outcome?.verdict || "";
  renderClosedState({
    closed,
    verdict,
    outcome: state?.outcome,
    watching: Boolean(data.claim.watching),
    browsing,
  });
  $("dirty-chip").classList.toggle("hidden", browsing || !data.dirty);
  const interval = String(data.claim.watch_interval_seconds || 300);
  if ([...$("watch-interval").options].some((opt) => opt.value === interval)) {
    $("watch-interval").value = interval;
  }
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
  const citations = state?.citations || collectCitations(state);
  fillList($("citations"), citations, (c) => {
    const li = document.createElement("li");
    li.innerHTML = `<a class="src" href="${escapeAttr(c.url)}" target="_blank" rel="noreferrer">${escapeHtml(c.title || c.url)}</a>`;
    return li;
  }, $("count-citations"));
  $("summary").textContent = state?.summary || "";
  lastLog = await api(`/api/claims/${id}/log`, { timeoutMs: 15000 });
  const spark = [...lastLog].reverse().map((c) => c.new_confidence);
  renderSpark($("spark"), spark);
  const viewed = lastLog.find((c) => c.id === head?.id) || lastLog[0];
  const prev = viewed?.previous_confidence;
  const deltaEl = $("conf-delta");
  if (prev == null) {
    deltaEl.textContent = browsing ? "initial commit" : "initial HEAD";
    deltaEl.className = "conf-delta";
  } else {
    const d = Math.round((conf - prev) * 10) / 10;
    deltaEl.textContent = `${d >= 0 ? "+" : ""}${d} from previous commit`;
    deltaEl.className = "conf-delta " + (d > 0 ? "up" : d < 0 ? "down" : "");
  }
  renderLog(lastLog, head?.id);
  await refreshList();
}

function isClosedClaim(claim, state) {
  if (claim && (claim.closed === true || claim.closed === 1 || claim.closed === "true")) return true;
  const outcome = state && state.outcome;
  if (outcome && typeof outcome === "object" && outcome.verdict) return true;
  return false;
}

function renderClosedState({ closed, verdict, outcome, watching, browsing }) {
  const view = $("claim-view");
  if (view) view.classList.toggle("is-closed", Boolean(closed));
  const label = $("conf-label");
  if (label) label.textContent = closed ? "final" : "confidence";
  const watchingChip = $("watching-chip");
  const closedChip = $("closed-chip");
  const closedText = `closed · ${verdict || "settled"}`;
  if (closed) {
    if (closedChip) {
      closedChip.className = `chip closed ${escapeAttr(verdict)}`;
      closedChip.textContent = closedText;
      if (watchingChip) watchingChip.classList.add("hidden");
    } else if (watchingChip) {
      watchingChip.className = `chip closed ${escapeAttr(verdict)}`;
      watchingChip.textContent = closedText;
      watchingChip.classList.remove("hidden");
    }
  } else {
    if (closedChip) {
      closedChip.className = "chip closed hidden";
      closedChip.textContent = "closed";
    }
    if (watchingChip) {
      watchingChip.classList.remove("hidden");
      watchingChip.className = watching ? "chip" : "chip off";
      watchingChip.textContent = watching ? "watching" : "paused";
    }
  }
  const watchBtn = $("watch-btn");
  if (watchBtn) watchBtn.disabled = closed || browsing;
  const toggle = $("watching-toggle");
  if (toggle) {
    toggle.checked = Boolean(watching);
    toggle.disabled = closed || browsing;
  }
  const interval = $("watch-interval");
  if (interval) interval.disabled = closed || browsing;
  renderOutcome(outcome, closed);
}

function outcomeSummaryText(summary, sourceUrl) {
  let text = String(summary || "").trim();
  if (sourceUrl) text = text.split(sourceUrl).join(" ");
  text = text.replace(/https?:\/\/\S+/g, " ");
  text = text.replace(/Primary source:\s*/gi, " ");
  text = text.replace(/\s+/g, " ").replace(/\s+([.,;:])/g, "$1").trim();
  return text || "This claim is closed.";
}

function renderOutcome(outcome, closed) {
  const banner = $("outcome-banner");
  const source = $("outcome-source");
  if (!banner) return;
  if (!closed && !outcome) {
    banner.className = "outcome-banner hidden";
    if (source) source.classList.add("hidden");
    return;
  }
  const verdict = (outcome && outcome.verdict) || "";
  banner.className = `outcome-banner ${escapeAttr(verdict)}`;
  const verdictEl = $("outcome-verdict");
  const summaryEl = $("outcome-summary");
  if (verdictEl) verdictEl.textContent = verdict || "settled";
  if (summaryEl) {
    summaryEl.textContent = outcomeSummaryText(
      outcome && outcome.summary,
      outcome && outcome.source_url,
    );
  }
  if (!source) return;
  if (outcome && outcome.source_url) {
    source.href = outcome.source_url;
    source.textContent = outcome.source_title || outcome.source_url;
    source.classList.remove("hidden");
  } else {
    source.removeAttribute("href");
    source.textContent = "";
    source.classList.add("hidden");
  }
}

function collectCitations(state) {
  if (!state) return [];
  const seen = new Set();
  const out = [];
  for (const item of [...(state.evidence_for || []), ...(state.evidence_against || [])]) {
    if (item.source_url && !seen.has(item.source_url)) {
      seen.add(item.source_url);
      out.push({ url: item.source_url, title: item.source_title || item.source_url });
    }
  }
  return out;
}

function fillList(ul, items, render, countEl) {
  ul.innerHTML = "";
  if (countEl) {
    countEl.textContent = String(items.length);
    document.querySelectorAll(`[data-count-for="${countEl.id}"]`).forEach((el) => {
      el.textContent = String(items.length);
    });
  }
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "empty-item";
    li.textContent = "none yet";
    ul.appendChild(li);
    return;
  }
  items.forEach((item) => ul.appendChild(render(item)));
}

function renderLog(log, activeSha) {
  $("log-meta").textContent = `${log.length} commits`;
  const ol = $("commit-log");
  ol.innerHTML = "";
  const chronological = [...log].reverse();
  chronological.forEach((c, idx) => {
    const newestFirstIndex = log.length - 1 - idx;
    const li = document.createElement("li");
    if (activeSha && c.id === activeSha) li.classList.add("active");
    const prev = c.previous_confidence;
    let arrow = "";
    if (prev != null) {
      const d = c.new_confidence - prev;
      const cls = d > 0 ? "up" : d < 0 ? "down" : "";
      arrow = ` <span class="arrow ${cls}">${prev.toFixed(1)}% → ${c.new_confidence.toFixed(1)}%</span>`;
    }
    li.innerHTML = `<div>
        <span class="sha">${c.id.slice(0, 12)}${c.id === log[0]?.id ? " (HEAD)" : ""}</span>
        <span class="msg">${escapeHtml(c.message)}${arrow}</span>
        <span class="why">${escapeHtml((c.reason || "").slice(0, 220))}${(c.reason || "").length > 220 ? "…" : ""}</span>
        <span class="status">${escapeHtml(c.author)} · ${relativeTime(c.created_at)}</span>
        <div class="commit-actions">
          <button type="button" class="ghost browse-commit">browse from this commit</button>
          <button type="button" class="ghost diff-commit">diff</button>
        </div>
      </div>`;
    li.querySelector(".browse-commit").onclick = (ev) => {
      ev.stopPropagation();
      selectClaim(currentId, c.id);
    };
    li.querySelector(".diff-commit").onclick = async (ev) => {
      ev.stopPropagation();
      if (newestFirstIndex + 1 >= log.length) {
        showDetail("root commit", formatDiffHtml(await api(`/api/claims/${currentId}/diff?b=${c.id}`)));
        return;
      }
      const parent = log[newestFirstIndex + 1];
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
  $("inspector-backdrop").classList.remove("hidden");
  $("detail-title").textContent = title;
  $("detail-body").innerHTML = html;
  const close = $("detail-close");
  if (close) close.focus();
}

function hideDetail() {
  $("detail").classList.add("hidden");
  $("inspector-backdrop").classList.add("hidden");
}

function typingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT" || el.isContentEditable;
}

function focusStageOnNarrow() {
  if (window.innerWidth > 1100) return;
  const app = $("app");
  app.classList.remove("show-rail", "show-log");
  document.querySelectorAll(".pane-switch button").forEach((btn) => {
    btn.classList.toggle("on", btn.id === "show-stage");
  });
}

async function moveClaim(delta) {
  const rows = visibleClaims();
  if (!rows.length) return;
  let idx = rows.findIndex((c) => c.id === currentId);
  if (idx < 0) idx = delta > 0 ? -1 : 0;
  const next = rows[(idx + delta + rows.length) % rows.length];
  await selectClaim(next.id);
  document.querySelector(`#claim-list li[data-id="${CSS.escape(next.id)}"]`)?.scrollIntoView({ block: "nearest" });
}

function restorePaneSizes() {
  const rail = Number(localStorage.getItem("rd-rail-w") || 300);
  const log = Number(localStorage.getItem("rd-log-w") || 360);
  document.documentElement.style.setProperty("--rail-w", `${Math.max(220, rail)}px`);
  document.documentElement.style.setProperty("--log-w", `${Math.max(260, log)}px`);
}

function enableResize() {
  document.querySelectorAll(".gutter").forEach((gutter) => {
    gutter.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      gutter.classList.add("dragging");
      const side = gutter.dataset.side;
      const startX = ev.clientX;
      const startRail = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--rail-w")) || 300;
      const startLog = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--log-w")) || 360;
      const onMove = (move) => {
        const dx = move.clientX - startX;
        if (side === "rail") {
          const next = Math.min(480, Math.max(220, startRail + dx));
          document.documentElement.style.setProperty("--rail-w", `${next}px`);
          localStorage.setItem("rd-rail-w", String(next));
        } else {
          const next = Math.min(560, Math.max(260, startLog - dx));
          document.documentElement.style.setProperty("--log-w", `${next}px`);
          localStorage.setItem("rd-log-w", String(next));
        }
      };
      const onUp = () => {
        gutter.classList.remove("dragging");
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    });
  });
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
  const current = claimsCache.find((c) => c.id === currentId);
  if (current?.closed) {
    toast("Claim already closed.");
    return;
  }
  if (watchingClaims.has(currentId)) {
    toast("Already watching this claim — you can keep browsing.");
    return;
  }
  const watchingId = currentId;
  const browseAtStart = browseSha;
  const btn = $("watch-btn");
  watchingClaims.add(watchingId);
  setBusy(btn, true);
  setWatchStatus("claim.watch() is searching. The rest of the workspace stays usable.");
  try {
    const result = await api(`/api/claims/${watchingId}/watch`, { method: "POST" });
    if (result.in_progress) {
      toast(result.detail || "Watch already running.");
      return;
    }
    if (currentId === watchingId) {
      await selectClaim(watchingId, browseAtStart);
    }
    if (result.changed && result.diff) {
      showDetail(result.closed ? "claim closed" : "watch diff", formatDiffHtml(result.diff));
    }
    if (result.closed) {
      toast(result.detail || "Claim closed with a final outcome.");
    } else if (result.changed) {
      toast(result.detail || "Belief updated.");
    } else {
      toast(result.detail || "No meaningful change — dropped.");
    }
  } catch (err) {
    toast(err.message);
  } finally {
    watchingClaims.delete(watchingId);
    setBusy(btn, false);
    setWatchStatus("");
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
  if (browseSha) {
    if (!window.confirm(`Revert live HEAD toward browsed commit ${browseSha.slice(0, 12)}?`)) return;
    const commit = await api(`/api/claims/${currentId}/revert`, {
      method: "POST",
      body: JSON.stringify({ sha: browseSha }),
    });
    await selectClaim(currentId);
    toast("Reverted toward " + (commit.parent_id || "").slice(0, 12));
    return;
  }
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

async function saveWatchConfig() {
  if (!currentId) return;
  await api(`/api/claims/${currentId}/watching`, {
    method: "POST",
    body: JSON.stringify({
      watching: $("watching-toggle").checked,
      interval_seconds: Number($("watch-interval").value),
    }),
  });
}

$("watching-toggle").onchange = async () => {
  try {
    await saveWatchConfig();
    await selectClaim(currentId, browseSha);
  } catch (err) {
    toast(err.message);
  }
};

$("watch-interval").onchange = async () => {
  try {
    await saveWatchConfig();
    toast("Watch interval saved for this claim.");
  } catch (err) {
    toast(err.message);
  }
};

$("service-btn").onclick = async () => {
  try {
    const status = await api("/api/service", { timeoutMs: 8000 });
    if (status.installed === "true") {
      if (!window.confirm("Uninstall the background watch service?")) return;
      await api("/api/service", { method: "DELETE", timeoutMs: 15000 });
      $("service-btn").textContent = "install timer service";
      toast("Background timer removed.");
      return;
    }
    const interval = Number($("watch-interval").value || 300);
    await api("/api/service", {
      method: "POST",
      timeoutMs: 15000,
      body: JSON.stringify({ interval_seconds: interval }),
    });
    $("service-btn").textContent = "remove timer service";
    toast(`Installed a system timer that runs claim.watch() every ${interval}s.`);
  } catch (err) {
    toast(err.message);
  }
};

$("copy-id").onclick = async () => {
  if (!currentId) return;
  await navigator.clipboard.writeText(currentId);
  const btn = $("copy-id");
  btn.textContent = "copied";
  toast("Copied " + currentId);
  setTimeout(() => { btn.textContent = "copy"; }, 1200);
};

$("browse-head").onclick = () => {
  if (currentId) selectClaim(currentId);
};

$("detail-close").onclick = () => hideDetail();
$("inspector-backdrop").onclick = () => hideDetail();
$("claim-filter").addEventListener("input", renderClaimList);
document.querySelectorAll("#status-filter [data-status]").forEach((btn) => {
  btn.addEventListener("click", () => {
    listStatus = btn.dataset.status || "all";
    localStorage.setItem("rd-list-status", listStatus);
    renderClaimList();
  });
});

function showPane(which) {
  const app = $("app");
  app.classList.toggle("show-rail", which === "claims");
  app.classList.toggle("show-log", which === "history");
  document.querySelectorAll(".pane-switch button").forEach((btn) => {
    btn.classList.toggle("on", btn.id === `show-${which === "claims" ? "claims" : which === "history" ? "log" : "stage"}`);
  });
}
$("show-claims").onclick = () => showPane("claims");
$("show-stage").onclick = () => showPane("belief");
$("show-log").onclick = () => showPane("history");

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    if (!$("detail").classList.contains("hidden")) {
      hideDetail();
      ev.preventDefault();
    }
    return;
  }
  if (typingTarget(ev.target)) {
    return;
  }
  if (ev.key === "/") {
    ev.preventDefault();
    if (window.innerWidth <= 1100) showPane("claims");
    $("claim-filter").focus();
    return;
  }
  if (ev.key === "j") { ev.preventDefault(); moveClaim(1); return; }
  if (ev.key === "k") { ev.preventDefault(); moveClaim(-1); return; }
  if (ev.key === "w" && currentId) { ev.preventDefault(); $("watch-btn").click(); return; }
  if (ev.key === "d" && currentId) { ev.preventDefault(); $("diff-btn").click(); return; }
  if (ev.key === "b" && currentId) { ev.preventDefault(); $("blame-btn").click(); return; }
});

window.addEventListener("hashchange", () => {
  const { id, sha } = parseRoute();
  if (id && (id !== currentId || sha !== browseSha)) selectClaim(id, sha).catch((err) => toast(err.message));
});

restorePaneSizes();
enableResize();

api("/api/service", { timeoutMs: 8000 }).then((status) => {
  if (status.installed === "true") $("service-btn").textContent = "remove timer service";
}).catch(() => {});

refreshList()
  .then(() => {
    const { id, sha } = parseRoute();
    if (id) return selectClaim(id, sha);
  })
  .catch((err) => toast(err.message));
