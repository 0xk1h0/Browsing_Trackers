#!/usr/bin/env python3
"""Multi-participant user-study web app.

Routes the same Cloudflare-Tunnel-exposed URL to isolated per-participant
xpra+chromium+mitmproxy sessions via path-namespaced reverse proxy:

    /                          → landing + briefing (auto-assigns pid +
                                  6-digit survey code on ack)
    /briefing                  → POST ack handler
    /login                     → deprecated redirect (no manual login)
    /dashboard                 → task list (uses pid cookie)
    /task/<order>/start        → POST, spawn isolated session
    /task/<order>/iframe       → page hosting the xpra HTML5 client iframe
    /task/<order>/complete     → POST, stop session
    /p/<pid>/(.*)              → reverse proxy (HTTP + WS) to backend xpra
    /admin/status              → JSON of active sessions

Backend per-participant xpra HTML5 servers live on 127.0.0.1:14600+N.
We strip the `/p/<pid>` prefix when forwarding, so the xpra server sees the
same paths it would in the standalone setup.

Run:
    python web_app.py --port 8888
Expose via Cloudflare Tunnel:
    cloudflared tunnel --url http://localhost:8888
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import random
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web


SCRIPT_DIR = Path(__file__).resolve().parent
STUDY_DIR = SCRIPT_DIR.parent
SESSIONS_DIR = STUDY_DIR / "runtime" / "sessions"
SPAWN_SCRIPT = SCRIPT_DIR / "spawn_session.py"
ASSIGNMENTS_FILE = STUDY_DIR / "participant_assignments.json"
PASSWORD_FILE = Path.home() / ".config/study/xpra_password"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_assignments() -> dict:
    return json.loads(ASSIGNMENTS_FILE.read_text())


def find_assignment(pid: str) -> Optional[dict]:
    for a in load_assignments().get("assignments", []):
        if a["participant_id"] == pid:
            return a
    return None


# ---------------------------------------------------------------------------
# Auto-assignment: the participant never sees / types their internal PID.
# On briefing-ack we pick the lowest unused slot, mint a 6-digit survey code,
# and persist the mapping so the researcher can join survey responses with
# session data after the fact.
# ---------------------------------------------------------------------------
ASSIGN_LOCK = STUDY_DIR / "runtime" / "assign.lock"


def _next_free_pid() -> Optional[str]:
    """Pick the lowest-numbered assignment slot that has no session dir yet."""
    assignments = load_assignments().get("assignments", [])
    used = set()
    if SESSIONS_DIR.is_dir():
        used = {p.name for p in SESSIONS_DIR.iterdir() if p.is_dir()}
    for a in assignments:
        if a["participant_id"] not in used:
            return a["participant_id"]
    return None


def _generate_survey_code() -> str:
    """6-digit code shown to the participant at the end of the session.
    Use the cryptographic RNG so codes can't be predicted, but format as
    plain digits because we display it for manual transcription."""
    return f"{secrets.randbelow(1_000_000):06d}"


def assign_new_participant() -> tuple[str, str]:
    """Atomically claim the next free PID and assign it a fresh survey code.
    Returns (pid, code). Raises RuntimeError if the assignment pool is
    exhausted. File-locked so two concurrent briefings can't collide."""
    ASSIGN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with ASSIGN_LOCK.open("w") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            pid = _next_free_pid()
            if pid is None:
                raise RuntimeError("no free participant slot — extend assignments")
            code = _generate_survey_code()
            pid_dir = SESSIONS_DIR / pid
            pid_dir.mkdir(parents=True, exist_ok=True)
            # Persist the mapping next to the session data. Researcher can
            # later grep for the code → resolve to pid → look up tasks.
            (pid_dir / "survey_code.txt").write_text(code + "\n")
            return pid, code
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def load_survey_code(pid: str) -> str:
    """Read the persisted survey code for a participant (empty if missing)."""
    f = SESSIONS_DIR / pid / "survey_code.txt"
    try:
        return f.read_text().strip()
    except FileNotFoundError:
        return ""


def load_password() -> str:
    return PASSWORD_FILE.read_text().strip()


def load_session_state(pid: str) -> Optional[dict]:
    state_file = SESSIONS_DIR / pid / "state.json"
    if not state_file.is_file():
        return None
    try:
        return json.loads(state_file.read_text())
    except Exception:
        return None


def is_session_alive(state: dict) -> bool:
    """Return True only if both xpra and mitmdump processes are still running."""
    import os as _os
    for key in ("xpra_pid", "mitm_pid"):
        pid = state.get(key)
        if not pid:
            return False
        try:
            _os.kill(int(pid), 0)
        except (OSError, ValueError):
            return False
    return True


def get_completed_tasks(pid: str) -> set[str]:
    pid_dir = SESSIONS_DIR / pid
    if not pid_dir.is_dir():
        return set()
    return {
        json.loads(p.read_text()).get("task_id")
        for p in pid_dir.glob("*/stopped.json")
        if p.is_file()
    } - {None}


async def spawn_session(
    pid: str, task_id: str, start_url: str, width: int, height: int,
) -> dict:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(SPAWN_SCRIPT),
        "start", "--pid", pid, "--task-id", task_id, "--start-url", start_url,
        "--width", str(width), "--height", str(height),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"spawn failed: {err.decode(errors='replace')}")
    return json.loads(out)


async def stop_session(pid: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(SPAWN_SCRIPT), "stop", "--pid", pid,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


# ---------------------------------------------------------------------------
# HTML templates (kept inline; pure prototype). For production move to jinja.
# ---------------------------------------------------------------------------
PAGE_CSS = """
body { font-family:-apple-system,sans-serif; background:#f0f4f8; margin:0; }
.wrap { max-width:760px; margin:36px auto; background:#fff; padding:36px;
        border-radius:16px; box-shadow:0 4px 20px rgba(0,0,0,.08); }
h1 { color:#333; margin-top:0; }
h2 { color:#444; }
p { color:#555; line-height:1.6; }
.btn { display:inline-block; padding:10px 22px; background:#667eea; color:#fff;
       border:0; border-radius:8px; font-size:15px; font-weight:600;
       cursor:pointer; text-decoration:none; }
.btn:hover { background:#5a6fd6; }
.btn-done { background:#27ae60; }
.btn-done:hover { background:#219a52; }
input { padding:10px 14px; font-size:16px; border:2px solid #ddd;
        border-radius:8px; width:200px; }
input:focus { border-color:#667eea; outline:0; }
table { width:100%; border-collapse:collapse; }
th, td { padding:9px 12px; text-align:left; font-size:13px;
         border-bottom:1px solid #eee; }
th { background:#f8f9fa; color:#555; }
.muted { color:#888; font-size:13px; }
.warn { background:#fff3cd; border:1px solid #ffc107; padding:10px 14px;
        border-radius:8px; margin:12px 0; font-size:14px; }
.iframe-wrap { background:#000; border-radius:8px; overflow:hidden; }
iframe { width:100%; height:78vh; border:0; display:block; }
.task-bar { background:#667eea; color:#fff; padding:14px 22px;
            border-radius:10px; margin-bottom:14px; display:flex;
            justify-content:space-between; align-items:center; }
.task-bar h2 { margin:0; font-size:16px; color:#fff; }
.cat { background:#e9ecef; padding:2px 8px; border-radius:4px;
       font-size:11px; color:#555; }

/* Task page: full-bleed iframe so xpra remote display fills the participant viewport. */
body.task-page { margin:0; background:#1a1a1a; }
.task-page .top-strip { display:flex; align-items:center; justify-content:space-between;
                         background:#222; color:#eee; padding:8px 16px; font-size:13px; }
.task-page .top-strip .q { color:#cfd; flex:1; margin:0 12px;
                            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.task-page .top-strip .done-btn { background:#27ae60; color:#fff; border:0;
                                   padding:7px 14px; border-radius:6px; font-weight:600;
                                   cursor:pointer; font-size:13px; }
.task-page .top-strip .done-btn:hover { background:#219a52; }
.task-page .body { display:flex; width:100vw; height:calc(100vh - 38px); }
.task-page .stage { position:relative; flex:1; min-width:0; }
.task-page .full-iframe { width:100%; height:100%; border:0; display:block; background:#000; }
.task-page .loading { position:absolute; inset:0; display:flex; flex-direction:column;
                       align-items:center; justify-content:center; background:#000;
                       color:#cfd; font-size:15px; pointer-events:none; transition:opacity .6s; }
.task-page .loading .spin { width:38px; height:38px; border:3px solid #233;
                             border-top-color:#79c; border-radius:50%;
                             animation:spin 0.8s linear infinite; margin-bottom:14px; }
.task-page .loading.hide { opacity:0; }
@keyframes spin { to { transform:rotate(360deg); } }
/* Briefing landing page */
body.briefing-page { background:#f0f4f8; }
.briefing { max-width:1100px; margin:24px auto; background:#fff; padding:28px 32px;
            border-radius:14px; box-shadow:0 4px 18px rgba(0,0,0,.06); }
.briefing h1 { color:#333; margin-top:0; font-size:22px; }
.briefing .bilingual { display:grid; grid-template-columns:1fr 1fr; gap:24px;
                       margin-bottom:14px; padding:14px 18px;
                       background:#f8fafc; border-left:4px solid #667eea;
                       border-radius:8px; }
.briefing .bilingual h2 { font-size:15px; color:#444; margin:0 0 6px 0; }
.briefing .bilingual p { font-size:13px; color:#444; line-height:1.55; margin:6px 0; }
.briefing .task-list { width:100%; border-collapse:collapse; margin-bottom:18px;
                       border:1px solid #e6ebf2; border-radius:10px; overflow:hidden; }
.briefing .task-list th { background:#eef2f7; padding:9px 12px;
                          font-size:12px; color:#555; text-align:left; }
.briefing .task-list td { padding:11px 12px; border-top:1px solid #eef2f7;
                          font-size:13px; vertical-align:top; }
.briefing .task-list td.num { width:32px; font-weight:600; color:#667eea;
                              text-align:center; font-size:15px; }
.briefing .task-list td.site { width:160px; }
.briefing .task-list .muted { color:#94a3b8; font-size:11px; }
.briefing .task-list .qkr { color:#333; }
.briefing .task-list .qen { color:#778; font-size:12px; margin-top:4px; font-style:italic; }
.briefing .confirm { background:#eaf6ea; border:1px solid #b9e0ba; padding:12px 16px;
                     border-radius:8px; margin:14px 0; }
.briefing .confirm label { display:flex; align-items:flex-start; gap:8px;
                            cursor:pointer; font-size:14px; color:#333; }
.briefing .confirm input { transform:translateY(2px); }

/* Answer side-panel for participant input */
.task-page .answer { width:340px; background:#262a2e; color:#e8eaed; padding:16px 18px;
                      display:flex; flex-direction:column; box-sizing:border-box;
                      border-left:1px solid #111; }
.task-page .answer h3 { margin:0 0 8px 0; font-size:14px; color:#9ec5fe; }
.task-page .answer .qtext { font-size:13px; color:#d2d6da; line-height:1.5;
                             background:#1b1e21; padding:10px 12px; border-radius:8px;
                             margin-bottom:12px; max-height:160px; overflow-y:auto; }
.task-page .answer label { font-size:12px; color:#9ec5fe; margin-bottom:6px; display:block; }
.task-page .answer textarea { flex:1; background:#1b1e21; color:#e8eaed;
                               border:1px solid #3a3f44; border-radius:8px;
                               padding:10px; font-size:14px; font-family:inherit;
                               resize:none; min-height:140px; box-sizing:border-box; }
.task-page .answer textarea:focus { outline:none; border-color:#79c; }
.task-page .answer .submit { background:#27ae60; color:#fff; border:0; padding:11px 18px;
                              border-radius:8px; font-size:14px; font-weight:600;
                              cursor:pointer; margin-top:12px; }
.task-page .answer .submit:hover { background:#219a52; }
.task-page .answer .note { color:#888; font-size:11px; margin-top:8px; line-height:1.4; }
.pid-card { background:#eef2f7; border:1px solid #d6dce5; border-radius:10px;
            padding:18px 22px; margin:20px 0; }
.pid-card .pid-label { font-size:12px; color:#667; letter-spacing:0.5px;
                       text-transform:uppercase; margin-bottom:8px; }
.pid-card .pid-row { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.pid-card code { font-size:28px; font-weight:700; font-family:ui-monospace,
                 SFMono-Regular,Menlo,monospace; color:#243; background:#fff;
                 padding:6px 14px; border-radius:6px; border:1px solid #d6dce5;
                 user-select:all; }
.pid-card .pid-copied { color:#27ae60; font-size:13px; font-weight:600; }
"""


def html_page(title: str, body: str, body_class: str = "") -> web.Response:
    cls = f' class="{body_class}"' if body_class else ""
    return web.Response(
        text=f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title><style>{PAGE_CSS}</style></head><body{cls}>{body}</body></html>""",
        content_type="text/html",
    )


# Width of the right-hand answer sidebar (kept in sync with `.task-page .answer`
# in PAGE_CSS) and height of the top strip. Used to compute the iframe area we
# want Chromium / Xvfb sized to.
SIDEBAR_PX = 340
TOPSTRIP_PX = 38


def _viewport_bootstrap_response() -> web.Response:
    """Tiny page that measures the participant's viewport and re-requests
    /task/<order>/iframe with `?w=&h=` so the spawn step can size Chromium and
    the Xvfb display 1:1 to the iframe area. Cache-busted with a "v=" param so
    a stale browser cache cannot pin a wrong size."""
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Loading…</title><style>"
        "html,body{margin:0;height:100%;background:#1a1a1a;color:#cfd;"
        "font-family:-apple-system,sans-serif;display:flex;align-items:center;"
        "justify-content:center;font-size:14px;}"
        "</style></head><body><div>Preparing session…</div>"
        "<script>(function(){"
        f"var sidebar={SIDEBAR_PX},top={TOPSTRIP_PX};"
        "var w=Math.max(1024,Math.min(3840,Math.round(window.innerWidth-sidebar)));"
        "var h=Math.max(700, Math.min(2160,Math.round(window.innerHeight-top)));"
        "var u=new URL(location.href);"
        "u.searchParams.set('w',w);u.searchParams.set('h',h);"
        "location.replace(u.toString());"
        "})();</script></body></html>"
    )
    return web.Response(text=html, content_type="text/html")


def landing_html() -> str:
    """Briefing page: shows the 10 tasks before PID entry."""
    try:
        master = load_assignments().get("tasks_master", [])
    except Exception:
        master = []
    rows = ""
    for i, t in enumerate(master, start=1):
        rows += (
            f'<tr>'
            f'<td class="num">{i}</td>'
            f'<td class="site"><b>{t["domain"]}</b><br>'
            f'<span class="muted">{t["start_url"]}</span></td>'
            f'<td><div class="qen">{t["question"]}</div></td>'
            f'</tr>'
        )
    return f"""
<div class="briefing">
  <h1>Browser User Study — Briefing</h1>
  <div class="bilingual">
    <div class="en">
      <h2>Overview</h2>
      <p>This study consists of <b>10 tasks</b>, each typically taking <b>2-5 minutes</b>.</p>
      <p>For each task (1 through 10), visit the assigned website, carefully read the question shown in
      the right-hand panel, and <b>find the requested information and type it into the answer box</b>.
      You do not need to log in, purchase, or add anything to a cart.</p>
      <p><b>Copy / paste:</b> The right-hand answer panel uses your local OS input method directly.
      If you want to paste text from the remote browser window, select text inside the browser, press
      <b>Ctrl+C</b>, click into the answer box, and press <b>Ctrl+V</b>.</p>
      <p>The full list of 10 tasks is below (the actual order will be shuffled).</p>
    </div>
  </div>

  <table class="task-list">
    <thead><tr><th>#</th><th>Site</th><th>Question</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <form method="POST" action="/briefing">
    <p class="confirm">
      <label>
        <input type="checkbox" name="ack" required>
        I have read and understood the above.
      </label>
    </p>
    <button class="btn" type="submit">Next &rarr;</button>
  </form>
</div>"""


def login_html(err: str = "") -> str:
    err_html = f'<p class="warn">{err}</p>' if err else ""
    return f"""
<div class="wrap">
<h1>Enter Participant ID</h1>
<p>Your researcher should have given you a participant ID (format: P001,
P002, …).</p>
{err_html}
<form method="POST" action="/login">
  <input name="pid" placeholder="P001" pattern="P[0-9]{{3}}" required>
  <button class="btn" type="submit">Start session</button>
</form>
</div>"""


def dashboard_html(pid: str, assignment: dict, completed: set[str]) -> str:
    tasks = assignment["tasks"]
    rows = ""
    next_order = None
    for t in tasks:
        if t["task_id"] not in completed:
            next_order = t["order"]
            break
    for t in tasks:
        done = t["task_id"] in completed
        is_next = t["order"] == next_order
        status = "✅" if done else ("▶" if is_next else "·")
        action = ""
        if is_next:
            action = f'<a class="btn" href="/task/{t["order"]}/iframe">Start</a>'
        elif done:
            action = '<span class="muted">done</span>'
        q = t["question"]
        if len(q) > 80:
            q = q[:78] + "…"
        rows += (
            f'<tr><td>{status}</td><td>{t["order"]}</td>'
            f'<td><span class="cat">{t["category"]}</span></td>'
            f'<td>{t["domain"]}</td><td>{q}</td>'
            f'<td>{action}</td></tr>'
        )
    n_done = len(completed)
    n_total = len(tasks)
    return f"""
<div class="wrap">
<div class="task-bar">
  <h2>{pid} — {n_done}/{n_total} tasks completed</h2>
  <span class="muted" style="color:#dde">{len(assignment['domains'])} domains</span>
</div>
<p>Click "Start" on the next task. A browser will open in your window. Complete
the task naturally, then click "I'm done" to record and move on.</p>
<table>
  <tr><th></th><th>#</th><th>Category</th><th>Domain</th><th>Task</th><th></th></tr>
  {rows}
</table>
</div>"""


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def iframe_page_html(pid: str, task: dict, order: int, total: int, iframe_url: str) -> str:
    safe_q = _esc(task['question'])
    # Pre-fill the textarea with the task's structured answer scaffold
    # (e.g. "a) Product name:\nb) Price:\nc) Rating:"). Participants type their answers
    # next to each colon; uniform structure makes grading consistent. Empty
    # string when no template is defined for the task.
    template = _esc(task.get("answer_template") or "")
    return f"""
<div class="top-strip">
  <span><b>Task {order}/{total}</b> — {task['domain']}</span>
  <span class="q" title="{safe_q}"></span>
  <span style="opacity:.7;">{pid}</span>
</div>
<div class="body">
  <div class="stage">
    <iframe id="taskFrame" class="full-iframe" src="{iframe_url}" allow="clipboard-read; clipboard-write"></iframe>
    <div id="loading" class="loading">
      <div class="spin"></div>
      <div>Loading session…</div>
      <div style="font-size:12px; opacity:0.7; margin-top:6px;">First load may take 5–10 seconds.</div>
    </div>
  </div>
  <aside class="answer">
    <h3>Task {order} / {total}</h3>
    <div class="qtext">{safe_q}</div>
    <form method="POST" action="/task/{order}/complete" style="display:flex; flex-direction:column; flex:1;">
      <label for="answer">Answer (fill in next to each item)</label>
      <textarea id="answer" name="answer" placeholder="Type your answer next to each item." required>{template}</textarea>
      <button class="submit" type="submit">Done &mdash; next task</button>
      <div class="note">
        When you click Done, your answer and this task's measurement data are saved and you move on to the next task.<br>
        <span style="opacity:.75;">Tip: select text inside the browser and press <b>Ctrl+C</b>, then click the answer box and press <b>Ctrl+V</b> to paste it.</span>
      </div>
    </form>
  </aside>
</div>
<script>
(function() {{
  const f = document.getElementById("taskFrame");
  const o = document.getElementById("loading");
  function hide() {{ o.classList.add("hide"); setTimeout(() => o.remove(), 800); }}
  f.addEventListener("load", () => setTimeout(hide, 1500));
  setTimeout(hide, 12000);

  // Re-focus xpra-html5's internal #pasteboard whenever the participant
  // mouses back into the stage. Without this, typing/Ctrl+C inside the
  // remote browser silently breaks after the answer textarea steals focus:
  // xpra's own blur-autofocus chain only triggers when #pasteboard loses
  // focus *inside* the iframe, not when focus comes back from the parent
  // page. Same-origin lets us reach in and focus the element directly.
  const stage = document.querySelector(".stage");
  function refocusPasteboard() {{
    try {{
      f.contentWindow.focus();
      const pb = f.contentDocument?.getElementById("pasteboard");
      if (pb) pb.focus();
    }} catch (e) {{ /* cross-origin: ignore */ }}
  }}
  if (stage) {{
    // mousedown fires before the click sinks into the canvas, so pasteboard
    // is focused before xpra sees the click.
    stage.addEventListener("mousedown", refocusPasteboard);
    // mouseenter handles the common case where the participant moves from
    // the answer panel into the stage without immediately clicking — e.g.
    // they just want to type into a remote field after finishing typing
    // here. Belt-and-suspenders for the keyboard handoff.
    stage.addEventListener("mouseenter", refocusPasteboard);
  }}

  // Shadow buffer that mirrors the LAST clipboard value the xpra *server*
  // pushed via `clipboard-token`. We maintain this ourselves (by hooking
  // `_process_clipboard_token`) instead of reading `client.clipboard_buffer`
  // directly, because the local browser→server poll routine overwrites
  // `clipboard_buffer` with the stale OS clipboard on focus events — that
  // is why a second Ctrl+C used to "lose" to the first.
  let xpraShadow = "";
  let lastCopyKeydown = 0;
  let lastWrittenOSBuf = "";
  function readXpraBuffer() {{
    // Prefer shadow (server-pushed). Fall back to the client field for
    // the brief window before our hook is installed.
    if (xpraShadow) return xpraShadow;
    try {{ return f.contentWindow?.__sailsClient?.clipboard_buffer || ""; }}
    catch (e) {{ return ""; }}
  }}
  async function syncXpraToOS() {{
    const buf = readXpraBuffer();
    if (!buf || buf === lastWrittenOSBuf) return;
    try {{
      await navigator.clipboard.writeText(buf);
      lastWrittenOSBuf = buf;
    }} catch (err) {{ /* gesture expired / permission denied — swallow */ }}
  }}

  function attachHooks() {{
    let ifw;
    try {{ ifw = f.contentWindow; }} catch (e) {{ return false; }}
    if (!ifw || !ifw.document) return false;
    if (!ifw.__sailsKeyHook) {{
      ifw.addEventListener("keydown", (ke) => {{
        const c = (ke.key || "").toLowerCase();
        if ((ke.ctrlKey || ke.metaKey) && (c === "c" || c === "x")) {{
          lastCopyKeydown = performance.now();
          // Retry sync because xpra's protocol round-trip varies
          // (~150-600ms, more on slow tunnels). The keydown is a transient
          // user gesture that propagates through these setTimeouts.
          setTimeout(syncXpraToOS, 250);
          setTimeout(syncXpraToOS, 600);
          setTimeout(syncXpraToOS, 1200);
          setTimeout(syncXpraToOS, 2000);
        }}
      }}, true);
      ifw.__sailsKeyHook = true;
    }}
    // Non-invasive: wrap _process_clipboard_token so we get a copy of every
    // server-pushed clipboard value. We do NOT modify any other xpra-html5
    // behavior, so #pasteboard autofocus and keyboard input keep working.
    try {{
      // xpra-html5 exposes the client via window.__sailsClient (one-line
      // patch in index.html). The bare `client` symbol is script-scoped
      // and not reachable from the parent page.
      const c = ifw.__sailsClient;
      if (c && c._process_clipboard_token && !c.__sailsTokenHook) {{
        const orig = c._process_clipboard_token.bind(c);
        c._process_clipboard_token = function(packet) {{
          orig(packet);
          const buf = c.clipboard_buffer;
          if (typeof buf === "string" && buf) xpraShadow = buf;
        }};
        c.__sailsTokenHook = true;
        return true;
      }}
    }} catch (e) {{}}
    return false;
  }}
  f.addEventListener("load", attachHooks);
  if (f.contentDocument && f.contentDocument.readyState === "complete") {{
    attachHooks();
  }}
  // The xpra-html5 `client` object is assigned during async init, often
  // after iframe `load`. Poll briefly until the token hook sticks.
  const hookRetry = setInterval(() => {{
    if (attachHooks()) clearInterval(hookRetry);
  }}, 500);
  setTimeout(() => clearInterval(hookRetry), 30000);

  // Ctrl+V into the answer box: xpra-html5's clipboard sync (remote-browser
  // copy → server roundtrip → navigator.clipboard.writeText) has 200–500ms
  // latency *and* can silently fail when its transient user-gesture window
  // expires. The OS clipboard is therefore unreliable at the moment of
  // paste — old or empty.
  //
  // The iframe is same-origin (we reverse-proxy /p/<pid>/), so we can read
  // xpra's *internal* clipboard buffer (`client.clipboard_buffer`)
  // synchronously. We track which value we last pasted and prefer the
  // internal buffer whenever it has *changed* since then. That way a fresh
  // Ctrl+C in the remote browser always wins over a stale OS clipboard.
  const ta = document.getElementById("answer");
  let lastConsumedXpraBuf = "";
  function insertAtCursor(text) {{
    const start = ta.selectionStart ?? ta.value.length;
    const end   = ta.selectionEnd   ?? ta.value.length;
    ta.value = ta.value.slice(0, start) + text + ta.value.slice(end);
    const cursor = start + text.length;
    ta.setSelectionRange(cursor, cursor);
    ta.dispatchEvent(new Event("input", {{ bubbles: true }}));
  }}
  if (ta) {{
    ta.addEventListener("paste", (e) => {{
      const native  = e.clipboardData?.getData("text") || "";
      let xpraBuf = readXpraBuffer();
      const xpraIsFresh = xpraBuf && xpraBuf !== lastConsumedXpraBuf;
      const recentCopy = (performance.now() - lastCopyKeydown) < 1500;

      // If the user just hit Ctrl+C in the remote browser but xpra's buffer
      // hasn't caught up yet (network round-trip in flight), block the paste
      // and poll for the new value. Without this the participant pastes the
      // previous selection. We refuse to fall back to the OS clipboard when
      // it still matches the previously-consumed value (= same stale text).
      if (recentCopy && !xpraIsFresh && lastConsumedXpraBuf) {{
        e.preventDefault();
        const deadline = performance.now() + 2500;
        const poll = () => {{
          const cur = readXpraBuffer();
          if (cur && cur !== lastConsumedXpraBuf) {{
            insertAtCursor(cur);
            lastConsumedXpraBuf = cur;
            return;
          }}
          if (performance.now() < deadline) {{
            setTimeout(poll, 50);
            return;
          }}
          // Final fallback at timeout: use OS clipboard only if it has
          // diverged from the stale value. Otherwise paste what xpra has
          // (might still be the old text — better than nothing).
          if (native && native !== lastConsumedXpraBuf) {{
            insertAtCursor(native);
            lastConsumedXpraBuf = native;
          }} else if (cur) {{
            insertAtCursor(cur);
            lastConsumedXpraBuf = cur;
          }}
        }};
        poll();
        return;
      }}

      // Prefer xpra's buffer when it has changed since our last paste
      // (= participant just copied something new inside the remote
      // browser), or when the OS clipboard is empty.
      const useXpra = xpraIsFresh || (xpraBuf && !native);
      if (useXpra) {{
        e.preventDefault();
        insertAtCursor(xpraBuf);
        lastConsumedXpraBuf = xpraBuf;
      }} else {{
        lastConsumedXpraBuf = xpraBuf;
      }}
    }});

    // Textarea focus is a user gesture — use it to push xpra's latest
    // buffer into the OS clipboard, so any subsequent native Ctrl+V picks
    // up the right text even outside this textarea.
    ta.addEventListener("focus", async () => {{
      const buf = readXpraBuffer();
      if (!buf) return;
      try {{
        const cur = await navigator.clipboard.readText().catch(() => "");
        if (cur !== buf) await navigator.clipboard.writeText(buf);
      }} catch (err) {{ /* permission denied: paste-event fallback still works */ }}
    }});
  }}
}})();
</script>"""


def done_html(pid: str, code: str, completed: int, total: int) -> str:
    is_last = completed >= total
    if is_last:
        # Final page: participants now need to fill out a separate Qualtrics/
        # Google Forms survey and enter THIS 6-digit code there to link the
        # responses to their measurement session. The code is generated at
        # briefing-ack and persisted at SESSIONS_DIR/<pid>/survey_code.txt
        # so the researcher can resolve it back to the internal pid later.
        body = f"""
<div class="wrap">
<h1>All tasks completed!</h1>
<p>Thank you. As a last step, please fill out the <b>participant survey</b>.</p>
<p>The survey will ask for the <b>6-digit participant code</b> shown below;
that code is what links your survey responses to your measurement session.
(You can use the Copy button.)</p>
<div class="pid-card">
  <div class="pid-label">Survey Code</div>
  <div class="pid-row">
    <code id="pid-value">{code}</code>
    <button type="button" class="btn" id="pid-copy">Copy</button>
    <span id="pid-copied" class="pid-copied"></span>
  </div>
</div>
<p class="muted">Progress: {completed}/{total}</p>
</div>
<script>
(function() {{
  const btn = document.getElementById("pid-copy");
  const v = document.getElementById("pid-value");
  const s = document.getElementById("pid-copied");
  if (!btn || !v) return;
  btn.addEventListener("click", async () => {{
    const text = v.textContent.trim();
    try {{
      await navigator.clipboard.writeText(text);
      s.textContent = "Copied";
    }} catch (e) {{
      const r = document.createRange();
      r.selectNodeContents(v);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
      s.textContent = "Please press Ctrl+C manually";
    }}
    setTimeout(() => {{ s.textContent = ""; }}, 2000);
  }});
}})();
</script>"""
    else:
        body = f"""
<div class="wrap">
<h1>Task recorded ✅</h1>
<p>Progress: {completed}/{total}</p>
<a class="btn" href="/dashboard">Next task →</a>
</div>"""
    return body


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def handle_index(req: web.Request) -> web.Response:
    if req.cookies.get("pid"):
        raise web.HTTPFound("/dashboard")
    return html_page("Study Briefing", landing_html(), body_class="briefing-page")


async def handle_briefing(req: web.Request) -> web.Response:
    """POST handler for the briefing checkbox. Auto-assigns the participant
    a PID + 6-digit survey code so they never have to type one in. Code is
    shown at the end of the session for survey linkage."""
    data = await req.post()
    if not data.get("ack"):
        return html_page("Study Briefing", landing_html(), body_class="briefing-page")
    try:
        pid, code = assign_new_participant()
    except RuntimeError as exc:
        return html_page(
            "No slots",
            f'<div class="wrap"><h1>No participant slots are currently available</h1>'
            f'<p class="warn">{exc}</p>'
            f'<p>Please try again later or contact the researcher.</p></div>',
        )
    resp = web.HTTPFound("/dashboard")
    cookie_kwargs = dict(max_age=86400, httponly=True, samesite="Lax", secure=False)
    resp.set_cookie("briefed", "1", **cookie_kwargs)
    resp.set_cookie("pid", pid, **cookie_kwargs)
    resp.set_cookie("survey_code", code, **cookie_kwargs)
    raise resp


# The old manual-login path is kept as a redirect for backward compatibility
# with bookmarks or stale tabs; PID is now auto-assigned at briefing-ack.
async def handle_login_get(req: web.Request) -> web.Response:
    raise web.HTTPFound("/dashboard" if req.cookies.get("pid") else "/")


async def handle_login_post(req: web.Request) -> web.Response:
    raise web.HTTPFound("/")


def require_pid(req: web.Request) -> str:
    pid = req.cookies.get("pid")
    if not pid or not find_assignment(pid):
        # No PID yet — bounce back to the briefing landing page, which will
        # auto-assign one on ack.
        raise web.HTTPFound("/")
    return pid


async def handle_dashboard(req: web.Request) -> web.Response:
    pid = require_pid(req)
    assignment = find_assignment(pid)
    completed = get_completed_tasks(pid)
    return html_page(f"Dashboard {pid}", dashboard_html(pid, assignment, completed))


async def handle_task_iframe(req: web.Request) -> web.Response:
    pid = require_pid(req)
    order = int(req.match_info["order"])
    assignment = find_assignment(pid)
    task = next((t for t in assignment["tasks"] if t["order"] == order), None)
    if not task:
        raise web.HTTPNotFound(text=f"task order {order} not in assignment")

    # If a session is already running AND its xpra/mitm are still alive, reuse
    # it. Stale state.json from a crashed session must be replaced — otherwise
    # the reverse-proxy returns 502 because the backend xpra is dead.
    state = load_session_state(pid)
    needs_spawn = (
        state is None
        or state.get("task_id") != task["task_id"]
        or not is_session_alive(state)
    )
    if needs_spawn:
        # Before spawning we need the participant's viewport so the Xvfb /
        # Chromium are sized to fit the iframe area (viewport minus the 340px
        # answer sidebar). The page below measures `window.innerWidth/Height`
        # and reloads with `?w=&h=` query params; on the reload we proceed
        # with the real spawn.
        w_q, h_q = req.query.get("w"), req.query.get("h")
        if not (w_q and h_q):
            return _viewport_bootstrap_response()
        try:
            width = max(1024, min(3840, int(w_q)))
            height = max(700,  min(2160, int(h_q)))
        except ValueError:
            width, height = 1600, 900

        if state is not None:
            await stop_session(pid)
        try:
            state = await spawn_session(
                pid, task["task_id"], task["start_url"], width, height,
            )
        except Exception as exc:
            return html_page(
                "Error",
                f'<div class="wrap"><h1>Session start failed</h1>'
                f'<p class="warn">{exc}</p>'
                f'<p><a class="btn" href="/dashboard">Back</a></p></div>',
            )

    # Build iframe URL. The xpra HTML5 client accepts URL parameters:
    # host/port/ssl/password/path. We tell it to WebSocket-connect to the
    # same origin at the /p/<pid>/ subpath, which we reverse-proxy below.
    password = load_password()
    forwarded_proto = req.headers.get("X-Forwarded-Proto", req.scheme)
    forwarded_host = req.headers.get("Host", "")
    use_ssl = forwarded_proto == "https"
    port = 443 if use_ssl else 80
    # Use index.html (which auto-invokes client.connect() on load) rather than
    # connect.html (which is the settings form requiring manual submit).
    iframe_url = (
        f"/p/{pid}/index.html"
        f"?host={forwarded_host}"
        f"&port={port}"
        f"&ssl={'true' if use_ssl else 'false'}"
        f"&path=/p/{pid}/"
        f"&username={pid}"
        f"&password={password}"
        f"&autoconnect=true"
        # Clipboard / keyboard config:
        #   - clipboard=true / clipboard_poll=true: keep xpra-html5's default
        #     polling chain intact. We were tempted to disable it to stop
        #     the OS-clipboard-overwrites-server-buffer race, but disabling
        #     it broke the #pasteboard autofocus path → keyboard input died.
        #     Instead we read from a *shadow* buffer below that ignores
        #     anything the local poll writes.
        #   - keyboard=false: hide xpra's on-screen simple-keyboard widget.
        #   - floating_menu=false: hide xpra's toolbar (focus thief).
        f"&clipboard=true"
        f"&clipboard_poll=true"
        f"&keyboard=false"
        f"&floating_menu=false"
    )
    return html_page(
        f"Task {order}",
        iframe_page_html(pid, task, order, len(assignment["tasks"]), iframe_url),
        body_class="task-page",
    )


async def handle_task_complete(req: web.Request) -> web.Response:
    pid = require_pid(req)
    order = int(req.match_info["order"])

    # Capture answer from the participant's form before tearing down the session,
    # so the session_dir path is still in state.json.
    data = await req.post()
    answer_text = (data.get("answer") or "").strip()
    state = load_session_state(pid)
    if state:
        sess_dir = Path(state["session_dir"])
        try:
            sess_dir.mkdir(parents=True, exist_ok=True)
            (sess_dir / "answer.json").write_text(
                json.dumps({
                    "participant_id": pid,
                    "task_id": state.get("task_id"),
                    "order": order,
                    "question": next(
                        (t["question"] for t in find_assignment(pid)["tasks"]
                         if t["order"] == order),
                        None,
                    ),
                    "answer": answer_text,
                    "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }, ensure_ascii=False, indent=2)
            )
        except Exception as exc:
            print(f"[warn] could not write answer.json: {exc}", file=sys.stderr)

    await stop_session(pid)
    assignment = find_assignment(pid)
    completed = get_completed_tasks(pid)
    return html_page(
        "Recorded",
        # Survey code: prefer the live cookie (set at briefing-ack); fall
        # back to disk so a participant who clears cookies mid-session still
        # sees the same code they were assigned.
        done_html(
            pid,
            req.cookies.get("survey_code") or load_survey_code(pid),
            len(completed),
            len(assignment["tasks"]),
        ),
    )


async def handle_logout(req: web.Request) -> web.Response:
    pid = req.cookies.get("pid")
    if pid:
        await stop_session(pid)
    resp = web.HTTPFound("/")
    resp.del_cookie("pid")
    resp.del_cookie("consent")
    raise resp


async def handle_admin_status(req: web.Request) -> web.Response:
    out = []
    if SESSIONS_DIR.is_dir():
        for state_file in SESSIONS_DIR.glob("*/state.json"):
            try:
                out.append(json.loads(state_file.read_text()))
            except Exception:
                pass
    return web.json_response({"active": out, "n": len(out)})


# ---------------------------------------------------------------------------
# Reverse proxy: /p/<pid>/<rest>   →   127.0.0.1:<xpra_port>/<rest>
# Handles both HTTP and WebSocket upgrades.
# ---------------------------------------------------------------------------
async def handle_proxy(req: web.Request) -> web.StreamResponse:
    pid = req.match_info["pid"]
    rest = req.match_info.get("rest", "")
    state = load_session_state(pid)
    if not state:
        return web.Response(status=503, text=f"no active session for {pid}")
    backend_port = state["xpra_port"]
    upstream_url = f"http://127.0.0.1:{backend_port}/{rest}"
    if req.query_string:
        upstream_url += f"?{req.query_string}"

    # WebSocket upgrade detection
    is_ws = (
        req.headers.get("Upgrade", "").lower() == "websocket"
        and "upgrade" in req.headers.get("Connection", "").lower()
    )
    if is_ws:
        return await _proxy_ws(req, upstream_url)
    return await _proxy_http(req, upstream_url)


# Hop-by-hop headers (RFC 7230 §6.1) that must not be forwarded.
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
    "content-length",
})


async def _proxy_http(req: web.Request, upstream_url: str) -> web.StreamResponse:
    fwd_headers = {
        k: v for k, v in req.headers.items()
        if k.lower() not in HOP_BY_HOP
    }
    fwd_headers.setdefault("X-Forwarded-For", req.remote or "")
    fwd_headers.setdefault("X-Forwarded-Proto", req.scheme)

    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout, auto_decompress=False) as cs:
        try:
            data = await req.read() if req.body_exists else None
            async with cs.request(
                req.method,
                upstream_url,
                headers=fwd_headers,
                data=data,
                allow_redirects=False,
            ) as up:
                resp_headers = {
                    k: v for k, v in up.headers.items()
                    if k.lower() not in HOP_BY_HOP
                }
                body = await up.read()
                return web.Response(
                    status=up.status,
                    headers=resp_headers,
                    body=body,
                )
        except aiohttp.ClientError as exc:
            return web.Response(status=502, text=f"upstream error: {exc}")


async def _proxy_ws(req: web.Request, upstream_url: str) -> web.WebSocketResponse:
    # Note: xpra over WebSocket uses the "binary" subprotocol.
    client_ws = web.WebSocketResponse(protocols=("binary",), max_msg_size=64 * 1024 * 1024)
    await client_ws.prepare(req)

    # Translate http:// → ws:// for the upstream URL.
    upstream_ws_url = upstream_url.replace("http://", "ws://", 1)

    session = aiohttp.ClientSession()
    try:
        upstream_ws = await session.ws_connect(
            upstream_ws_url,
            protocols=("binary",),
            max_msg_size=64 * 1024 * 1024,
            timeout=30,
            heartbeat=None,
        )

        async def pump(src, dst):
            async for msg in src:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    await dst.send_bytes(msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    await dst.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.CLOSE:
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break

        try:
            await asyncio.gather(
                pump(client_ws, upstream_ws),
                pump(upstream_ws, client_ws),
            )
        finally:
            await upstream_ws.close()
    except Exception as exc:
        if not client_ws.closed:
            await client_ws.close(code=1011, message=str(exc).encode()[:120])
    finally:
        await session.close()
    return client_ws


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def make_app() -> web.Application:
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_get("/", handle_index)
    app.router.add_post("/briefing", handle_briefing)
    app.router.add_get("/login", handle_login_get)
    app.router.add_post("/login", handle_login_post)
    app.router.add_get("/dashboard", handle_dashboard)
    app.router.add_get("/task/{order}/iframe", handle_task_iframe)
    app.router.add_post("/task/{order}/complete", handle_task_complete)
    app.router.add_get("/logout", handle_logout)
    app.router.add_get("/admin/status", handle_admin_status)

    # Reverse-proxy catch-all for backend xpra HTTP + WS.
    app.router.add_route("*", "/p/{pid}/{rest:.*}", handle_proxy)
    app.router.add_route("*", "/p/{pid}", handle_proxy)
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8888)
    args = ap.parse_args()

    if not ASSIGNMENTS_FILE.is_file():
        print(f"[ERROR] {ASSIGNMENTS_FILE} missing.", file=sys.stderr)
        sys.exit(1)
    if not PASSWORD_FILE.is_file():
        print(f"[ERROR] {PASSWORD_FILE} missing.", file=sys.stderr)
        sys.exit(1)

    print(f"[web_app] listening on http://{args.host}:{args.port}/")
    print(f"[web_app] expose via:  cloudflared tunnel --url http://localhost:{args.port}")
    web.run_app(make_app(), host=args.host, port=args.port, print=lambda _: None)


if __name__ == "__main__":
    main()
