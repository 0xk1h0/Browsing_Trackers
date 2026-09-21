#!/usr/bin/env python3
"""User Study Client — Standalone (no server required)

Runs entirely on the participant's computer:
1. Starts a local mitmproxy to capture HTTP(S) traffic
2. Launches Chrome with proxy + stealth anti-bot evasion
3. Packages results as ZIP for submission to researcher

Usage:
    pip install mitmproxy
    python study_client.py --participant P001

Requirements:
    - Python 3.8+
    - mitmproxy (pip install mitmproxy)
    - Google Chrome or Chromium
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ASSIGNMENTS_FILE = SCRIPT_DIR / "participant_assignments.json"

# ── Stealth + Capture mitmproxy addon ──
# Combines traffic capture with puppeteer-stealth-like anti-bot evasion.
# Injects JavaScript into HTML pages to:
#   - Remove navigator.webdriver flag
#   - Spoof navigator.plugins, languages, platform
#   - Add chrome.runtime stub
#   - Fix WebGL renderer strings
# This significantly reduces CAPTCHA triggers from bot-detection systems.
CAPTURE_ADDON_CODE = r'''
"""Stealth capture addon: traffic logging + anti-bot evasion JS injection."""
import json, os, time, re
from mitmproxy import http

CAPTURE_FILE = os.environ.get("AGENTCLOAK_CAPTURE_FILE", "capture.jsonl")

STEALTH_JS = """
<script>
// --- puppeteer-stealth equivalent for mitmproxy ---
// 1. Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// 2. Spoof plugins (Chrome normally has 5)
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const p = [
            {name:'Chrome PDF Plugin', filename:'internal-pdf-viewer', description:'Portable Document Format'},
            {name:'Chrome PDF Viewer', filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai', description:''},
            {name:'Native Client', filename:'internal-nacl-plugin', description:''},
        ];
        p.length = 3;
        return p;
    }
});
// 3. Spoof languages
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
// 4. Chrome runtime stub (missing = headless flag)
if (!window.chrome) window.chrome = {};
if (!window.chrome.runtime) window.chrome.runtime = {connect: () => {}, sendMessage: () => {}};
// 5. Fix permissions query
const origQuery = window.navigator.permissions?.query;
if (origQuery) {
    window.navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : origQuery(params);
}
// 6. WebGL vendor/renderer (avoid "Google SwiftShader" = headless)
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Intel Inc.';
    if (param === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, param);
};
</script>
"""

class StealthCaptureAddon:
    def __init__(self):
        self.out = open(CAPTURE_FILE, "a", encoding="utf-8")

    def response(self, flow: http.HTTPFlow):
        try:
            req = flow.request
            resp = flow.response

            # Inject stealth JS into HTML responses
            content_type = resp.headers.get("content-type", "")
            if resp and "text/html" in content_type and resp.content:
                try:
                    html = resp.content.decode("utf-8", errors="replace")
                    if "<head" in html.lower():
                        html = re.sub(r'(<head[^>]*>)', r'\1' + STEALTH_JS, html, count=1, flags=re.IGNORECASE)
                    elif "<html" in html.lower():
                        html = re.sub(r'(<html[^>]*>)', r'\1<head>' + STEALTH_JS + '</head>', html, count=1, flags=re.IGNORECASE)
                    resp.content = html.encode("utf-8")
                    resp.headers["content-length"] = str(len(resp.content))
                except Exception:
                    pass

            # Log request
            from urllib.parse import urlparse
            parsed = urlparse(req.pretty_url)
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "method": req.method,
                "url": req.pretty_url,
                "hostname": parsed.hostname or "",
                "path": parsed.path,
                "query": list(req.query.fields) if req.query else [],
                "headers": dict(req.headers),
                "status_code": resp.status_code if resp else 0,
                "resource_type": req.headers.get("sec-fetch-dest", ""),
                "is_third_party": False,
                "is_tracker": False,
            }
            self.out.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.out.flush()
        except Exception:
            pass

    def done(self):
        self.out.close()

addons = [StealthCaptureAddon()]
'''


def find_chrome() -> str:
    """Find Chrome/Chromium on the participant's system."""
    system = platform.system()
    candidates = []

    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        prog = os.environ.get("PROGRAMFILES", "C:\\Program Files")
        prog86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
        candidates = [
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(prog, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(prog86, "Google", "Chrome", "Application", "chrome.exe"),
        ]
    else:
        candidates = [
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser", "/usr/bin/chromium", "/snap/bin/chromium",
        ]

    for c in candidates:
        if os.path.exists(c):
            return c
    for name in ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "chrome"]:
        path = shutil.which(name)
        if path:
            return path
    return ""


def find_free_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def check_mitmdump():
    """Check if mitmdump is available (cross-platform)."""
    path = shutil.which("mitmdump")
    if path:
        return path
    candidates = [
        os.path.expanduser("~/.local/bin/mitmdump"),
        "/usr/local/bin/mitmdump",
        os.path.join(sys.prefix, "bin", "mitmdump"),
        os.path.join(sys.prefix, "Scripts", "mitmdump.exe"),
    ]
    if platform.system() == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            import glob
            candidates.append(os.path.join(local, "Programs", "Python", "**", "Scripts", "mitmdump.exe"))
            candidates.append(os.path.join(local, "Packages", "PythonSoftwareFoundation*", "LocalCache", "local-packages", "**", "Scripts", "mitmdump.exe"))
            expanded = []
            for c in candidates:
                if "*" in c:
                    expanded.extend(glob.glob(c, recursive=True))
                else:
                    expanded.append(c)
            candidates = expanded
    for p in candidates:
        if os.path.exists(p):
            return p
    # Last resort: pip show
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "-f", "mitmproxy"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            location = ""
            for line in result.stdout.splitlines():
                if line.startswith("Location:"):
                    location = line.split(":", 1)[1].strip()
            if location:
                base = os.path.dirname(location)
                for subdir in ["Scripts", "bin"]:
                    p = os.path.join(base, subdir, "mitmdump.exe" if platform.system() == "Windows" else "mitmdump")
                    if os.path.exists(p):
                        return p
    except Exception:
        pass
    return ""


def setup_mitmproxy_ca():
    """Install mitmproxy CA certificate for trusted TLS interception."""
    ca_cert = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    if not ca_cert.exists():
        proc = subprocess.Popen(
            [check_mitmdump() or "mitmdump", "-q"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass

    if not ca_cert.exists():
        return

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(
                ["security", "add-trusted-cert", "-d", "-r", "trustRoot",
                 "-k", os.path.expanduser("~/Library/Keychains/login.keychain-db"),
                 str(ca_cert)],
                capture_output=True, timeout=10,
            )
        elif system == "Windows":
            cer_path = ca_cert.with_suffix(".cer")
            shutil.copy(ca_cert, cer_path)
            subprocess.run(
                ["certutil", "-addstore", "-user", "Root", str(cer_path)],
                capture_output=True, timeout=10,
            )
    except Exception:
        pass


def run_task(chrome: str, mitmdump: str, task: dict, task_num: int, total: int,
             output_dir: Path) -> dict:
    """Run a single task: start stealth proxy, launch browser, wait for completion."""

    task_id_safe = task["task_id"].replace("/", "_").replace("--", "_")
    task_dir = output_dir / task_id_safe
    task_dir.mkdir(parents=True, exist_ok=True)

    capture_file = task_dir / "capture.jsonl"
    addon_file = task_dir / "_addon.py"
    profile_dir = task_dir / "chrome_profile"
    profile_dir.mkdir(exist_ok=True)

    addon_file.write_text(CAPTURE_ADDON_CODE, encoding="utf-8")

    # Start mitmproxy with stealth addon
    port = find_free_port()
    env = os.environ.copy()
    env["AGENTCLOAK_CAPTURE_FILE"] = str(capture_file)

    proxy_cmd = [
        mitmdump,
        "--listen-port", str(port),
        "--set", "ssl_insecure=true",
        "--set", "connection_strategy=lazy",
        "--set", "upstream_cert=true",
        "-s", str(addon_file),
        "-q",
    ]
    proxy_proc = subprocess.Popen(proxy_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    # Launch Chrome with proxy + English locale + stealth-friendly flags
    chrome_cmd = [
        chrome,
        f"--proxy-server=http://127.0.0.1:{port}",
        "--ignore-certificate-errors",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-blink-features=AutomationControlled",  # stealth: hide automation
        "--lang=en-US",
        "--accept-lang=en-US,en",
        f"--user-data-dir={profile_dir}",
        task["start_url"],
    ]
    browser_proc = subprocess.Popen(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"  Browser opened.")
    print(f"  Complete the task, then come back here.")
    print(f"  * If a CAPTCHA appears, please solve it and continue.")
    print(f"      Enter = done  |  s = skip  |  q = quit")

    start_time = time.monotonic()

    completed = False
    while True:
        try:
            user_input = input(f"\n  [{task_num}/{total}] > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input == "":
            completed = True
            break
        elif user_input == "s":
            break
        elif user_input == "q":
            try:
                browser_proc.terminate()
            except Exception:
                pass
            try:
                proxy_proc.terminate()
            except Exception:
                pass
            print("\n  Session ended.")
            sys.exit(0)

    duration = time.monotonic() - start_time

    # Cleanup
    for proc in [browser_proc, proxy_proc]:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    n_requests = 0
    if capture_file.exists():
        with open(capture_file, encoding="utf-8") as f:
            n_requests = sum(1 for l in f if l.strip())

    metadata = {
        "participant_id": task.get("_pid", ""),
        "task_id": task["task_id"],
        "task_num": task_num,
        "benchmark": task["benchmark"],
        "domain": task["domain"],
        "category": task["category"],
        "ad_profile": task["ad_profile"],
        "question": task["question"],
        "start_url": task["start_url"],
        "duration_seconds": round(duration, 2),
        "completed": completed,
        "n_requests_captured": n_requests,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "client_platform": platform.system(),
        "client_python": sys.version.split()[0],
    }
    with open(task_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    addon_file.unlink(missing_ok=True)
    shutil.rmtree(profile_dir, ignore_errors=True)

    status = "Done" if completed else "Skipped"
    print(f"  {status} ({duration:.0f}s, {n_requests} requests captured)")

    return metadata


def package_results(pid: str, output_dir: Path) -> Path:
    """Package all results into a ZIP file for submission."""
    zip_name = f"user_study_{pid}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    zip_path = output_dir.parent / zip_name

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in output_dir.rglob("*"):
            if f.is_file() and f.suffix in (".jsonl", ".json"):
                zf.write(f, f.relative_to(output_dir.parent))
    return zip_path


def main():
    parser = argparse.ArgumentParser(
        description="Browser Privacy User Study (Standalone Client)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  python study_client.py --participant P001\n  python study_client.py --participant P001 --start-from 5",
    )
    parser.add_argument("--participant", required=True, help="Participant ID (e.g. P001)")
    parser.add_argument("--start-from", type=int, default=1, help="Resume from task N")
    args = parser.parse_args()
    pid = args.participant.upper()

    print(f"\n{'='*60}")
    print(f"  Browser Privacy User Study")
    print(f"{'='*60}")

    # 1. Check dependencies
    print(f"\n[1/3] Checking environment...")

    chrome = find_chrome()
    if not chrome:
        print("  [ERROR] Chrome/Chromium not found.")
        print("          Install: https://www.google.com/chrome/")
        sys.exit(1)
    print(f"  [OK] Chrome: {chrome}")

    mitmdump = check_mitmdump()
    if not mitmdump:
        print("  [ERROR] mitmdump not found.")
        print("          Install: pip install mitmproxy")
        sys.exit(1)
    print(f"  [OK] mitmdump: {mitmdump}")

    # Install CA cert (first run only)
    print(f"  [..] Setting up TLS certificate...")
    setup_mitmproxy_ca()
    print(f"  [OK] TLS ready")

    # 2. Load assignments
    print(f"\n[2/3] Loading tasks...")

    if not ASSIGNMENTS_FILE.exists():
        print(f"  [ERROR] {ASSIGNMENTS_FILE.name} not found.")
        print(f"          Place it in the same folder as study_client.py")
        sys.exit(1)

    with open(ASSIGNMENTS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    assignment = None
    for a in data["assignments"]:
        if a["participant_id"] == pid:
            assignment = a
            break

    if not assignment:
        valid = [a["participant_id"] for a in data["assignments"]]
        print(f"  [ERROR] Participant '{pid}' not found.")
        print(f"          Valid IDs: {', '.join(valid[:5])}...")
        sys.exit(1)

    tasks = assignment["tasks"]
    for t in tasks:
        t["_pid"] = pid

    print(f"  [OK] {pid}: {len(tasks)} tasks ({len(assignment['categories'])} categories)")

    # 3. Run tasks
    output_dir = SCRIPT_DIR / "results" / pid
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[3/3] Starting tasks!")
    print(f"  Output: {output_dir}")
    print(f"\n{'='*60}")
    print(f"  A browser window will open for each task.")
    print(f"  Complete the task, then press Enter here.")
    print(f"  If a CAPTCHA appears, solve it normally.")
    print(f"{'='*60}")

    results = []
    for task in tasks:
        if task["order"] < args.start_from:
            continue

        print(f"\n{'='*60}")
        print(f"  Task {task['order']}/{len(tasks)}")
        print(f"  Category: {task['category']} | Domain: {task['domain']}")
        print(f"  Q: {task['question']}")
        print(f"  URL: {task['start_url']}")

        result = run_task(chrome, mitmdump, task, task["order"], len(tasks), output_dir)
        results.append(result)

        if task["order"] < len(tasks):
            print(f"\n  Next task in 3 seconds...")
            time.sleep(3)

    # Summary
    completed = [r for r in results if r["completed"]]
    total_reqs = sum(r["n_requests_captured"] for r in results)

    summary = {
        "participant_id": pid,
        "tasks_assigned": len(tasks),
        "tasks_completed": len(completed),
        "tasks_skipped": len(results) - len(completed),
        "total_requests_captured": total_reqs,
        "total_duration_seconds": sum(r["duration_seconds"] for r in results),
        "platform": platform.system(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "results": results,
    }
    with open(output_dir / "session_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    zip_path = package_results(pid, output_dir)

    print(f"\n{'='*60}")
    print(f"  Session complete!")
    print(f"  Completed: {len(completed)}/{len(results)} tasks")
    print(f"  Captured: {total_reqs} HTTP requests")
    print(f"")
    print(f"  Results file: {zip_path}")
    print(f"")
    print(f"  Please send this ZIP file to the researcher:")
    print(f"  -> Email or Google Drive")
    print(f"{'='*60}")
    print(f"\nThank you for participating!")


if __name__ == "__main__":
    main()
