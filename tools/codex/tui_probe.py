"""Probe codex TUI under PTY, mirroring the adapter flow with step-by-step
alive checks to pinpoint where/if codex dies.

Run:  python tui_probe.py
"""
import glob
import os
import re
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from runner.adapters.codex import resolve_launcher

try:
    from winpty import PtyProcess
except ImportError:
    sys.exit("pywinpty not installed (pip install pywinpty)")


def strip(s: str) -> str:
    s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)
    s = re.sub(r"\x1b[()][AB012]", "", s)
    s = re.sub(r"\x1b[=>]", "", s)
    return "".join(ch for ch in s if ch >= " " or ch in "\r\n\t")


def alive(pty):
    try:
        return pty.isalive()
    except Exception as e:
        return f"err({e!r})"


def safe_write(pty, data, label):
    try:
        pty.write(data)
        print(f">> wrote {label}: ok | alive={alive(pty)}")
    except Exception as e:
        print(f">> wrote {label}: FAILED {e!r} | alive={alive(pty)}")


sessions = os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions")
before = set(glob.glob(os.path.join(sessions, "**", "rollout-*.jsonl"), recursive=True))

workdir = tempfile.mkdtemp(prefix="tuiprobe_", dir=os.path.abspath(".work"))
launcher = resolve_launcher("codex")
print(">> workdir:", workdir)
print(">> launcher:", launcher)

pty = PtyProcess.spawn(launcher, cwd=workdir, dimensions=(50, 200))
screen = []

def drain():
    try:
        while True:
            d = pty.read()
            if not d:
                break
            screen.append(d)
    except Exception:
        pass
threading.Thread(target=drain, daemon=True).start()

print(">> spawned | alive:", alive(pty))

# Step 1: handle trust dialog
trusted = False
banner = False
t0 = time.time()
while time.time() - t0 < 30:
    blob = "".join(screen)
    low = blob.lower()
    if not trusted and ("do you trust" in low or "trust the contents" in low or "yes, continue" in low):
        print(f">> trust dialog seen at {round(time.time()-t0,1)}s | alive={alive(pty)}")
        safe_write(pty, "\r", "trust-Enter")
        trusted = True
        time.sleep(2.0)
        continue
    if "OpenAI Codex" in blob:
        banner = True
        print(f">> banner seen at {round(time.time()-t0,1)}s | alive={alive(pty)}")
        break
    time.sleep(0.3)

print(f">> trusted={trusted} banner={banner} | alive={alive(pty)}")
print(">> settling 3s ...")
time.sleep(3.0)
print(">> after settle | alive:", alive(pty))

# Step 2: type the task
safe_write(pty, "\x15", "ctrl-u")
time.sleep(0.3)
safe_write(pty, "建立一個檔案 hello.txt 內容寫 hi,然後說完成", "task")
time.sleep(0.8)
safe_write(pty, "\r", "enter")

# Step 3: watch for rollout
print(">> watching for rollout (up to 60s)...")
found = None
for _ in range(60):
    new = [f for f in glob.glob(os.path.join(sessions, "**", "rollout-*.jsonl"), recursive=True)
           if f not in before]
    if new:
        found = max(new, key=os.path.getmtime)
        break
    time.sleep(1)
print(">> new rollout:", found, "| alive:", alive(pty))

print("\n>> READABLE SCREEN (last 2000 chars):")
print(strip("".join(screen))[-2000:])

try:
    pty.terminate(force=True)
except Exception:
    pass
shutil.rmtree(workdir, ignore_errors=True)
print("\n>> done")
