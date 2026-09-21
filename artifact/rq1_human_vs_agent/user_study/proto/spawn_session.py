#!/usr/bin/env python3
"""Spawn an isolated per-participant browser session.

Allocates a unique X display, mitmproxy port, and xpra bind port, then starts:
    - mitmdump (with the standard measurement addon)
    - xpra :N --bind-tcp=127.0.0.1:<xpra_port> --html=<html5 root>
              --start-child=<per-session chromium wrapper>
              --tcp-auth/--ws-auth=password (file-backed)

Each participant gets a fresh chromium profile dir + capture.jsonl.

The web app (web_app.py) will call this to create a session and reverse-proxy
the xpra HTML5 client over participant-namespaced URLs.

Usage (CLI for manual testing):
    spawn_session.py start --pid P001 --task-id Allrecipes--0 \\
        --start-url https://www.allrecipes.com/
    spawn_session.py stop --pid P001

State files (one per active session) live in:
    user_study/runtime/sessions/<pid>/state.json
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


STUDY_DIR = Path(__file__).resolve().parent.parent
PROTO_DIR = STUDY_DIR / "proto"
RUNTIME_DIR = STUDY_DIR / "runtime"
SESSIONS_DIR = RUNTIME_DIR / "sessions"
HTML5_ROOT = STUDY_DIR / "xpra-html5" / "html5"


def _resolve_addon_path() -> Path:
    """Locate the mitmproxy capture addon shared with the agent measurement
    pipeline. Search order:
      1. USER_STUDY_ADDON_PATH env var (explicit override for reviewers).
      2. ../../pipeline/capture/mitm_addon.py relative to the user-study
         folder, where this artifact ships it.
      3. AGENTCLOAK_ROOT/measurement/mitm_capture_addon.py, the development
         tree location, if AGENTCLOAK_ROOT is set.
    """
    env = os.environ.get("USER_STUDY_ADDON_PATH")
    if env:
        return Path(env)
    shipped = STUDY_DIR.parent.parent / "pipeline" / "capture" / "mitm_addon.py"
    if shipped.is_file():
        return shipped
    root = os.environ.get("AGENTCLOAK_ROOT")
    if root:
        return Path(root) / "measurement" / "mitm_capture_addon.py"
    return shipped  # may not exist; surfaced as a clear error at start


def _resolve_chromium_candidates() -> list[Path]:
    """Chromium binary candidates. Override with USER_STUDY_CHROMIUM_BIN
    (single path) or USER_STUDY_CHROMIUM_BINS (':'-separated)."""
    explicit = os.environ.get("USER_STUDY_CHROMIUM_BIN")
    if explicit:
        return [Path(explicit)]
    multi = os.environ.get("USER_STUDY_CHROMIUM_BINS")
    if multi:
        return [Path(p) for p in multi.split(":") if p]
    cache = Path.home() / ".cache" / "ms-playwright"
    candidates: list[Path] = [
        # Prefer regular Chromium (1161, v134) over Chrome for Testing
        # (1208, v145): CfT shows a permanent "only for automated testing"
        # InfoBar that --test-type does not suppress.
        cache / "chromium-1161" / "chrome-linux" / "chrome",
        cache / "chromium-1208" / "chrome-linux64" / "chrome",
    ]
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    return candidates


def _resolve_xpra_bin() -> Path:
    env = os.environ.get("USER_STUDY_XPRA_BIN")
    if env:
        return Path(env)
    found = shutil.which("xpra")
    return Path(found) if found else Path("xpra")


def _resolve_mitmdump_bin() -> Path:
    env = os.environ.get("USER_STUDY_MITMDUMP_BIN")
    if env:
        return Path(env)
    found = shutil.which("mitmdump")
    return Path(found) if found else Path("mitmdump")


ADDON_PATH = _resolve_addon_path()
STEALTH_ADDON_PATH = Path(__file__).resolve().parent / "stealth_addon.py"
# Enable stealth-JS injection by default (real-user fingerprint emulation).
# Set USER_STUDY_STEALTH=0 to disable for ablation runs that should match
# the paper's existing agent methodology (which had stealth off).
STEALTH_DEFAULT = "1"
CHROMIUM_CANDIDATES = _resolve_chromium_candidates()
XPRA_BIN = _resolve_xpra_bin()
MITMDUMP_BIN = _resolve_mitmdump_bin()
PASSWORD_FILE = Path(
    os.environ.get("USER_STUDY_XPRA_PASSWORD_FILE")
    or (Path.home() / ".config" / "study" / "xpra_password")
)

# Display number range for participant sessions (avoid clobbering :100 used by
# the earlier single-session phase-1 prototype that used display :100).
DISPLAY_MIN = 110
DISPLAY_MAX = 199
# xpra bind port range (loopback only; web_app reverse-proxies these).
XPRA_PORT_MIN = 14600
XPRA_PORT_MAX = 14699


def find_free_port() -> int:
    """Allocate a free TCP port (kernel chooses)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def allocate_display() -> int:
    """Return the lowest free display number in [DISPLAY_MIN, DISPLAY_MAX]."""
    in_use: set[int] = set()
    # Inspect /tmp/.X11-unix for sockets like X100, X110, etc.
    xdir = Path("/tmp/.X11-unix")
    if xdir.exists():
        for entry in xdir.iterdir():
            name = entry.name
            if name.startswith("X"):
                try:
                    in_use.add(int(name[1:]))
                except ValueError:
                    pass
    # Also inspect any active session state files.
    if SESSIONS_DIR.exists():
        for state_file in SESSIONS_DIR.glob("*/state.json"):
            try:
                state = json.loads(state_file.read_text())
            except Exception:
                continue
            n = state.get("display_num")
            if isinstance(n, int):
                in_use.add(n)
    for n in range(DISPLAY_MIN, DISPLAY_MAX + 1):
        if n not in in_use:
            return n
    raise RuntimeError("no free X display number")


def allocate_xpra_port() -> int:
    """Return the lowest free port in [XPRA_PORT_MIN, XPRA_PORT_MAX]."""
    import socket as _s
    for p in range(XPRA_PORT_MIN, XPRA_PORT_MAX + 1):
        with _s.socket(_s.AF_INET, _s.SOCK_STREAM) as sk:
            try:
                sk.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free xpra port")


def find_chromium() -> Path:
    for c in CHROMIUM_CANDIDATES:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    raise FileNotFoundError("no usable chromium binary; expected Playwright bundle")


def load_password() -> str:
    if not PASSWORD_FILE.is_file():
        raise FileNotFoundError(
            f"xpra password file missing: {PASSWORD_FILE} "
            f"(create with: openssl rand -hex 12 > {PASSWORD_FILE}; chmod 600 ...)"
        )
    return PASSWORD_FILE.read_text().strip()


def write_chromium_launcher(
    session_dir: Path,
    chromium: Path,
    mitm_port: int,
    profile_dir: Path,
    start_url: str,
    width: int,
    height: int,
) -> Path:
    launcher = session_dir / "chromium_launch.sh"
    # Note: we used to pre-write Preferences + a "First Run" marker to control
    # startup, but that backfired: chromium then treats the profile as a
    # returning user, looks for non-existent session data, and falls back to
    # the New Tab Page — discarding the positional start URL we pass.
    # The simpler approach (fresh profile, no Preferences seeding, single
    # positional URL after --no-first-run) reliably opens start_url alone.
    # `--test-type` suppresses the "You are using an unsupported command-line
    # flag: --no-sandbox" banner that Chrome shows to end users.
    # `--disable-gpu` skips GPU probing on Xvfb where no GPU is available
    # (Chrome otherwise wastes 1-2s probing /dev/dri before falling back).
    # `--window-size/position` is used instead of `--start-maximized` because
    # Xvfb has no window manager and won't honor "maximized" requests.
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "\n"
        f'# Clean stale SingletonLock (chromium 134 puts it globally despite\n'
        f'# --user-data-dir; a dead PID lock would cause the new chromium to\n'
        f'# "Open in existing browser session" and exit silently).\n'
        f'rm -f ~/.config/chromium/SingletonLock ~/.config/chromium/SingletonSocket ~/.config/chromium/SingletonCookie 2>/dev/null || true\n'
        "\n"
        "# --- Chromium ---\n"
        f'exec "{chromium}" \\\n'
        f'    --proxy-server="http://127.0.0.1:{mitm_port}" \\\n'
        "    --ignore-certificate-errors \\\n"
        "    --no-first-run \\\n"
        "    --no-default-browser-check \\\n"
        "    --no-sandbox \\\n"
        "    --test-type \\\n"
        "    --disable-gpu \\\n"
        "    --disable-dev-shm-usage \\\n"
        "    --disable-background-networking \\\n"
        "    --disable-sync \\\n"
        "    --disable-extensions \\\n"
        "    --disable-default-apps \\\n"
        "    --disable-features=RendererCodeIntegrity,Translate,InterestFeedContentSuggestions \\\n"
        "    --enable-features=UseOzonePlatform \\\n"
        "    --ozone-platform=x11 \\\n"
        "    --disable-blink-features=AutomationControlled \\\n"
        "    --metrics-recording-only \\\n"
        "    --safebrowsing-disable-auto-update \\\n"
        "    --lang=en-US \\\n"
        # CRITICAL: must be quoted; bash treats `;` as command separator,
        # which would truncate the chromium argv and drop the start URL.
        "    --accept-lang='en-US,en;q=0.9' \\\n"
        f'    --user-data-dir="{profile_dir}" \\\n'
        f"    --window-size={width},{height} \\\n"
        "    --window-position=0,0 \\\n"
        # `--start-maximized` is the proper Chromium flag for "fill the
        # screen". Xvfb has no WM to honor a runtime maximize request, but
        # the flag still sets Chrome's *internal* maximized state — which
        # affects how it draws its own header (merged tabs into title bar,
        # no separate title bar). Paired with the xpra-html5 decoration
        # patch (Window.js), the participant sees the page edge-to-edge.
        "    --start-maximized \\\n"
        "    --force-device-scale-factor=1 \\\n"
        # Single positional URL = the start page. We intentionally drop
        # `--homepage` and `--restore-last-session` flags here: chromium 134
        # honors a single positional URL on a fresh profile when --no-first-run
        # is set, and adding those extra flags appears to confuse the launcher
        # into opening NTP. Preferences (written above) reinforces this.
        f'    "{start_url}"\n'
    )
    launcher.chmod(0o755)
    return launcher


def wait_for_port(host: str, port: int, timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(0.25)
    return False


@contextlib.contextmanager
def spawn_lock():
    """Serialize concurrent spawn requests so display/port allocation does not race."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = RUNTIME_DIR / "spawn.lock"
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def cmd_start(args) -> None:
    pid_dir = SESSIONS_DIR / args.pid
    pid_dir.mkdir(parents=True, exist_ok=True)
    state_file = pid_dir / "state.json"
    if state_file.exists():
        prev = json.loads(state_file.read_text())
        if _is_process_alive(prev.get("xpra_pid")):
            print(f"[error] session already active for {args.pid}: {state_file}")
            sys.exit(2)

    # Per-task subdirectory (so re-runs of the same task accumulate cleanly).
    # task_id may contain spaces (e.g. "Google Search--28"); xpra's
    # --start-child=<path> splits on whitespace internally and fails to find
    # the launcher when the path contains spaces. Normalize to shell-safe.
    import re as _re
    task_safe = _re.sub(r"[^A-Za-z0-9._-]+", "_", args.task_id) if args.task_id else "ad-hoc"
    task_ts = time.strftime("%Y%m%d_%H%M%S")
    session_dir = pid_dir / f"{task_safe}_{task_ts}"
    session_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = session_dir / "chrome_profile"
    profile_dir.mkdir(exist_ok=True)
    logs_dir = session_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    capture_file = session_dir / "capture.jsonl"

    chromium = find_chromium()
    password = load_password()
    # The lock must cover *the entire* spawn sequence, not just allocation:
    # subsequent spawns detect "in use" only after Xvfb has created its X11
    # socket AND xpra has bound its tcp port AND state.json has been written.
    # Releasing earlier lets a concurrent spawn observe the same display/port
    # as "free" and re-claim it.
    # The lock must cover *the entire* spawn sequence, not just allocation:
    # subsequent spawns detect "in use" only after Xvfb has created its X11
    # socket AND xpra has bound its tcp port AND state.json has been written.
    # Releasing earlier lets a concurrent spawn observe the same display/port
    # as "free" and re-claim it.
    with spawn_lock():
        display_num = allocate_display()
        xpra_port = allocate_xpra_port()
        mitm_port = find_free_port()

        # 1. mitmdump
        env = os.environ.copy()
        env["AGENTCLOAK_CAPTURE_FILE"] = str(capture_file)
        # The addon writes JS-instrumentation telemetry (Canvas/WebGL/Audio
        # API interceptions + pre-encryption XHR/Fetch bodies) to a separate
        # file. Without this env, all sessions clobber a single global
        # js_telemetry.jsonl in the CWD, mixing participants' fingerprinting
        # traces.
        fp_capture_file = session_dir / "js_telemetry.jsonl"
        env["AGENTCLOAK_FP_CAPTURE_FILE"] = str(fp_capture_file)
        stealth_on = os.environ.get("USER_STUDY_STEALTH", STEALTH_DEFAULT).strip() == "1"
        env["AGENTCLOAK_STEALTH"] = "1" if stealth_on else "0"
        mitm_cmd = [
            str(MITMDUMP_BIN),
            "--listen-host", "127.0.0.1",
            "--listen-port", str(mitm_port),
            "--set", "ssl_insecure=true",
            "--set", "connection_strategy=lazy",
            "-s", str(ADDON_PATH),
        ]
        if stealth_on and STEALTH_ADDON_PATH.is_file():
            mitm_cmd.extend(["-s", str(STEALTH_ADDON_PATH)])
        mitm_cmd.append("-q")
        mitm_log = open(logs_dir / "mitmdump.log", "wb")
        mitm_proc = subprocess.Popen(
            mitm_cmd, env=env, stdout=mitm_log, stderr=mitm_log,
        )
        if not wait_for_port("127.0.0.1", mitm_port, timeout=10):
            mitm_proc.kill()
            raise RuntimeError(f"mitmdump did not bind on :{mitm_port}")

        # 2. chromium launcher (per-session, with baked-in flags)
        launcher = write_chromium_launcher(
            session_dir, chromium, mitm_port, profile_dir, args.start_url,
            args.width, args.height,
        )

        # 3. xpra
        xpra_log = open(logs_dir / "xpra.log", "wb")
        xpra_cmd = [
            str(XPRA_BIN), "start", f":{display_num}",
            f"--bind-tcp=127.0.0.1:{xpra_port}",
            f"--html={HTML5_ROOT}",
            f"--tcp-auth=password:value={password}",
            f"--ws-auth=password:value={password}",
            "--no-mdns",
            "--no-pulseaudio",
            "--no-notifications",
            "--no-printing",
            "--no-system-tray",
            "--no-bell",
            # Clipboard: make defaults explicit so participants can copy text
            # from the remote chromium and paste it into the (parent-page)
            # answer textarea. `direction=both` lets xpra push selections out
            # AND accept paste-in. The HTML5 client also needs the URL flag
            # `clipboard=true`, which the web app sets when building the
            # iframe URL.
            "--clipboard=yes",
            "--clipboard-direction=both",
            # Match the Xvfb framebuffer to the participant's available iframe
            # area (viewport minus the 340px answer sidebar and ~38px top strip).
            # Sized 1:1 so the participant sees the page at its real desktop
            # rendering — not a scaled-down crop of a fixed 1440p canvas.
            f"--resize-display={args.width}x{args.height}",
            # Stateless-only encoding (no VP8/h264) because their stateful
            # encoders leave stale-tile artifacts under fast scroll — JPEG
            # and webp re-encode each frame. webp is added because for the
            # mostly-text web pages in this study it compresses ~30% smaller
            # than JPEG at equivalent quality, cutting frame size and the
            # encode/decode round-trip.
            "--encodings=webp,jpeg,png,rgb24",
            "--video-encoders=none",
            # Bias the encoder toward speed/latency rather than visual
            # fidelity. Quality 70 is still very readable for desktop web
            # content; lower min-quality lets xpra temporarily degrade
            # during heavy scrolling so frames don't queue up. Higher base
            # speed makes the first frame after a change land sooner.
            "--quality=70",
            "--speed=90",
            "--min-quality=40",
            "--min-speed=30",
            # After motion stops, xpra defers a "high quality refresh".
            # Shortening the delay (default 150ms) makes text sharpen back
            # up almost immediately when the participant pauses scrolling.
            "--auto-refresh-delay=50",
            "--mmap=no",
            # Show the real X cursor — without this, the participant sees
            # only their browser cursor over a static canvas, which feels
            # laggy on hover (no visual feedback for I-beam over text, etc.)
            "--cursors=yes",
            "--microphone=no",
            "--webcam=no",
            "--opengl=no",
            f"--start-child={launcher}",
            "--exit-with-children=yes",
            "--daemon=no",
        ]
        xpra_proc = subprocess.Popen(xpra_cmd, stdout=xpra_log, stderr=xpra_log)
        if not wait_for_port("127.0.0.1", xpra_port, timeout=25):
            try:
                xpra_proc.terminate()
                mitm_proc.terminate()
            except Exception:
                pass
            raise RuntimeError(f"xpra did not bind on :{xpra_port}")

        state = {
            "pid": args.pid,
            "task_id": args.task_id,
            "start_url": args.start_url,
            "display_num": display_num,
            "xpra_port": xpra_port,
            "mitm_port": mitm_port,
            "xpra_pid": xpra_proc.pid,
            "mitm_pid": mitm_proc.pid,
            "session_dir": str(session_dir),
            "capture_file": str(capture_file),
            "fp_capture_file": str(fp_capture_file),
            "stealth": stealth_on,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        state_file.write_text(json.dumps(state, indent=2))
    print(json.dumps(state, indent=2))


def _is_process_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def cmd_stop(args) -> None:
    state_file = SESSIONS_DIR / args.pid / "state.json"
    if not state_file.exists():
        print(f"[info] no active session for {args.pid}")
        return
    state = json.loads(state_file.read_text())

    # 1. Stop xpra by its display so it cleans up children/Xvfb.
    if isinstance(state.get("display_num"), int):
        try:
            subprocess.run(
                [str(XPRA_BIN), "stop", f":{state['display_num']}"],
                check=False, capture_output=True, timeout=10,
            )
        except Exception:
            pass

    # 2. Terminate mitmdump.
    for key in ("mitm_pid", "xpra_pid"):
        p = state.get(key)
        if p:
            try:
                os.kill(int(p), signal.SIGTERM)
            except OSError:
                pass

    # Allow children to exit, then SIGKILL stragglers.
    time.sleep(1.0)
    for key in ("mitm_pid", "xpra_pid"):
        p = state.get(key)
        if p and _is_process_alive(p):
            try:
                os.kill(int(p), signal.SIGKILL)
            except OSError:
                pass

    n_reqs = 0
    cap = Path(state.get("capture_file", ""))
    if cap.is_file():
        with cap.open("r", encoding="utf-8", errors="replace") as fh:
            n_reqs = sum(1 for line in fh if line.strip())

    stopped = {
        **state,
        "stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "n_requests_captured": n_reqs,
    }
    Path(state["session_dir"]).joinpath("stopped.json").write_text(
        json.dumps(stopped, indent=2)
    )
    state_file.unlink()
    print(f"[done] stopped {args.pid}: {n_reqs} requests captured")


def cmd_list(args) -> None:
    if not SESSIONS_DIR.exists():
        print("(no sessions dir)")
        return
    for state_file in SESSIONS_DIR.glob("*/state.json"):
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            continue
        alive = _is_process_alive(state.get("xpra_pid"))
        print(
            f"  {state.get('pid'):>6}  display :{state.get('display_num')}  "
            f"xpra=127.0.0.1:{state.get('xpra_port')}  "
            f"mitm=127.0.0.1:{state.get('mitm_port')}  "
            f"{'ALIVE' if alive else 'DEAD'}  task={state.get('task_id')}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="spawn a session for a participant")
    p_start.add_argument("--pid", required=True, help="participant ID (e.g. P001)")
    p_start.add_argument("--task-id", default=None, help="task id (used in dirs)")
    p_start.add_argument("--start-url", required=True, help="initial URL")
    # Web app passes the participant's measured stage size (viewport minus the
    # answer sidebar). Defaults give a sensible 1080p-friendly box for ad-hoc
    # CLI use.
    p_start.add_argument("--width", type=int, default=1600,
                         help="chromium window + Xvfb width (px)")
    p_start.add_argument("--height", type=int, default=900,
                         help="chromium window + Xvfb height (px)")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="stop a participant's session")
    p_stop.add_argument("--pid", required=True)
    p_stop.set_defaults(func=cmd_stop)

    p_list = sub.add_parser("list", help="list active sessions")
    p_list.set_defaults(func=cmd_list)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
