import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from realitydiff.api import create_app
from realitydiff.service import RealityDiff
from realitydiff.store import BeliefStore
from tests.test_reasoner import INIT, ScriptedModel


def _client(tmp_path):
    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=ScriptedModel([INIT]))
    return TestClient(create_app(engine, start_watcher=False), base_url="http://127.0.0.1:8000")


def test_ui_keeps_terminal_style_but_is_sparse(tmp_path):
    with _client(tmp_path) as client:
        css = client.get("/static/styles.css").text
        html = client.get("/").text
        js = client.get("/static/app.js").text

        assert client.get("/static/styles.css").status_code == 200
        assert '"IBM Plex Mono"' in css
        assert '"Instrument Serif"' in css
        assert ".grain" in css
        assert "--for: #b6e388" in css

        assert ".outcome-banner" in css
        assert ".chip.closed" in css
        assert "--space-5: 40px" in css
        assert "--space-6: 56px" in css
        assert "font-size: 14px" in css
        assert "line-height: 1.6" in css
        assert "padding: 40px 28px" in css
        assert "padding: 52px 64px var(--space-7)" in css
        assert "padding: 18px 0" in css
        assert "padding: 22px 24px" in css
        assert "gap: var(--space-6) var(--space-6)" in css
        assert "max-width: 72ch" in css
        assert "line-height: 1.8" in css

        assert "padding: 22px 18px" not in css
        assert "font-size: 13px" not in css
        assert "padding: 32px 40px 64px" not in css
        assert "padding: 10px 0" not in css

        assert html.count('class="action-row"') == 2
        assert ">Watch<" in html
        assert ">Diff<" in html
        assert ">Blame<" in html
        assert ">Revert<" in html
        assert 'id="outcome-banner"' in html
        assert 'id="closed-chip"' in html
        assert 'id="watch-controls"' in html
        assert 'id="conf-label"' in html
        assert "/static/app.js?v=" in html
        assert "live watch" in html
        assert 'id="watch-cadence"' in html
        assert "Full update every 5m." in html
        assert 'id="watch-permit"' in html
        assert "Allow" in html
        assert 'id="watch-interval"' not in html
        assert "Checking sources every" in js
        assert "Allow live watch" in js

        assert "renderClosedState" in js
        assert "isClosedClaim" in js
        assert "closed · " in js
        assert "Claim already closed." in js
        assert "is-closed" in js
        assert "#claim-view.is-closed #watch-btn" in css
        assert "#claim-view.is-closed #claim-reason" in css
        assert "a.src:visited" in css
        assert "color: var(--accent)" in css
        assert 'id="service-btn"' in html
        assert html.index('id="revert-btn"') < html.index('id="service-btn"')
        assert html.index("</div>", html.index('id="watch-controls"')) < html.index('id="service-btn"')

        assert html.index('id="claim-title"') < html.index('id="outcome-banner"')
        assert html.index('id="outcome-banner"') < html.index('id="claim-reason"')

        assert "outcomeSummaryText" in js
        assert "Primary source:" in js

        assert '|| 300' in js
        assert '|| 360' in js
        assert "Math.max(220, rail)" in js
        assert "Math.max(260, log)" in js
        assert "clampPane" in js
        assert "formatShort" in js
        assert "cadenceLine" in js
        assert "pointerdown" in js
        assert "aria-busy" in js
        assert "touch-action: none" in css
        assert "100dvh" in css
        assert "safe-area-inset" in css
        assert "color-scheme: dark" in css
        assert "pointer: coarse" in css

        assert 'id="status-filter"' in html
        assert 'id="stat-strip"' in html
        assert "sec-citations" not in html
        assert "collectCitations" not in js
        assert 'id="citations"' not in html
        assert 'id="confidence-card"' in html
        assert "score-ledger" not in html
        assert "renderScoreLedger" not in js
        assert 'id="inspector-backdrop"' in html
        assert 'id="keys"' in html
        assert "matchesClaim" in js
        assert "weightPercent" in js
        assert "weight-fill" in js
        assert "hideDetail" in js
        assert ".stat-strip" in css
        assert ".weight" in css
        assert "position: fixed" in css
        assert "prefers-reduced-motion" in css
        assert 'id="compare-view"' in html
        assert 'id="compare-grid"' in html
        assert 'id="compare-run"' in html
        assert "renderCompareResults" in js
        assert "/api/compare" in js
        assert ".compare-grid" in css
        assert "claim-identity" in html
        assert ".claim-identity" in css
        assert "position: sticky" in css
        assert "conf-ring" not in html
        assert "setConfidenceRing" not in js
        assert 'id="home-list"' in html
        assert 'id="home-logo"' in html
        assert 'id="agent-status"' in html
        assert 'id="agent-kicker"' in html
        assert 'id="agent-detail"' in html
        assert "/api/activity" in js
        assert "function renderAgentStatus" in js
        assert "function pollActivity" in js
        assert "function agentStatusView" in js
        assert ".agent-status" in css
        assert ".agent-status.is-live" in css
        assert "agent-led" in css
        assert "function renderHome" in js
        assert "claimsCache[0]) selectClaim" not in js


def test_outcome_summary_strips_urls_and_source_label():
    app_js = Path(__file__).resolve().parents[1] / "src/realitydiff/static/app.js"
    script = f"""
const src = await Bun.file({json.dumps(str(app_js))}).text();
const match = src.match(/function outcomeSummaryText\\([\\s\\S]*?\\n\\}}/);
if (!match) {{ throw new Error("missing outcomeSummaryText"); }}
const outcomeSummaryText = new Function(match[0] + "; return outcomeSummaryText;")();
const url = "https://store.steampowered.com/news/group/45479024/view/692020124159311887";
const summary = "Valve officially launched the Steam Frame. Primary source: " + url;
const out = outcomeSummaryText(summary, url);
if (out.includes("http")) throw new Error(out);
if (/Primary source/i.test(out)) throw new Error(out);
if (!out.includes("Steam Frame")) throw new Error(out);
if (outcomeSummaryText("", null) !== "This claim is closed.") throw new Error("empty");
const matchFn = src.match(/function matchesClaim\\([\\s\\S]*?\\n\\}}/);
const weightFn = src.match(/function weightPercent\\([\\s\\S]*?\\n\\}}/);
if (!matchFn || !weightFn) throw new Error("missing helpers");
const matchesClaim = new Function(matchFn[0] + "; return matchesClaim;")();
const weightPercent = new Function(weightFn[0] + "; return weightPercent;")();
const open = {{ id: "abc", title: "Siri rewrite", closed: false }};
const closed = {{ id: "def", title: "Steam Frame", closed: true }};
if (!matchesClaim(open, "siri", "all")) throw new Error("query");
if (matchesClaim(open, "steam", "all")) throw new Error("miss");
if (!matchesClaim(open, "", "open")) throw new Error("open");
if (matchesClaim(open, "", "closed")) throw new Error("open excluded");
if (!matchesClaim(closed, "def", "closed")) throw new Error("id");
if (weightPercent(0.6) !== 60) throw new Error("weight");
if (weightPercent(2) !== 100) throw new Error("clamp");
if (weightPercent("nope") !== 0) throw new Error("nan");
const clampFn = src.match(/function clampPane\\([\\s\\S]*?\\n\\}}/);
const shortFn = src.match(/function formatShort\\([\\s\\S]*?\\n\\}}/);
if (!clampFn || !shortFn) throw new Error("missing pane helpers");
const clampPane = new Function(clampFn[0] + "; return clampPane;")();
const formatShort = new Function(shortFn[0] + "; return formatShort;")();
if (clampPane("rail", 100, 1600, 360) !== 220) throw new Error("rail floor");
if (clampPane("rail", 900, 1600, 360) !== 480) throw new Error("rail cap");
if (clampPane("log", 900, 1600, 300) !== 560) throw new Error("log cap");
if (clampPane("rail", 400, 1200, 560) !== 220) throw new Error("stage room");
if (formatShort(90) !== "90s") throw new Error("seconds");
if (formatShort(120) !== "2m") throw new Error("minutes");
if (formatShort(7200) !== "2h") throw new Error("hours");
if (formatShort(172800) !== "2d") throw new Error("days");
const elapsedFn = src.match(/function formatElapsed\\([\\s\\S]*?\\n\\}}/);
const routeFn = src.match(/function routeBits\\([\\s\\S]*?\\n\\}}/);
const viewFn = src.match(/function agentStatusView\\([\\s\\S]*?\\n\\}}/);
if (!elapsedFn || !routeFn || !viewFn) throw new Error("missing status helpers");
const formatElapsed = new Function(elapsedFn[0] + "; return formatElapsed;")();
const agentStatusView = new Function(elapsedFn[0] + "\\n" + routeFn[0] + "\\n" + viewFn[0] + "; return agentStatusView;")();
if (formatElapsed(14) !== "14s") throw new Error("elapsed s");
if (formatElapsed(65) !== "1m 05s") throw new Error("elapsed m");
const idle = agentStatusView([], null, 1_000_000);
if (idle.state !== "idle" || idle.detail !== "No research running") throw new Error("idle");
const live = agentStatusView([{{
  claim_id: "cl_1", kind: "watch", phase: "Searching the web and X to update confidence",
  title: "Apple replaces Siri", started_at: 990,
}}], null, 1_014_000, {{ api: "api.x.ai", model: "grok-4.7" }});
if (live.state !== "live" || live.kicker !== "researching") throw new Error("live " + live.kicker);
if (!live.detail.includes("Siri")) throw new Error("title");
if (!live.meta.includes("Searching")) throw new Error("phase");
if (!live.meta.includes("api.x.ai") || !live.meta.includes("grok-4.7")) throw new Error("route " + live.meta);
const wired = agentStatusView([], null, 1_000_000, {{ api: "api.x.ai", model: "grok-4.7" }});
if (wired.state !== "idle" || !wired.meta.includes("grok-4.7")) throw new Error("idle route");
const done = agentStatusView([], {{ until: 2_000_000, claimId: "cl_1", title: "Apple replaces Siri", confidence: 78 }}, 1_500_000);
if (done.state !== "done" || done.meta !== "78.0%") throw new Error("done " + done.meta);
"""
    result = subprocess.run(["bun", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_compare_hint_is_neutral():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "src/realitydiff/static/index.html").read_text()
    assert "pretend" not in html
    assert "Enter the model ids to compare" in html
