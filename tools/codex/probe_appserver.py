"""Probe the codex app-server protocol on THIS machine's codex version.

Run:  python probe_appserver.py
It spawns `codex app-server`, does the initialize handshake, starts a thread and
a tiny turn, auto-accepts any approval request, and prints every JSONL line the
server sends for ~40s. Paste the output back so the adapter can be matched to
your codex version's exact method/field names.
"""
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from runner.adapters.codex import resolve_launcher

PROMPT = "建立一個檔案 probe.txt,內容寫 hi,然後說完成"

launcher = resolve_launcher("codex")
if launcher is None:
    sys.exit("codex not found on PATH")

print(">> launching:", launcher + ["app-server"])
proc = subprocess.Popen(
    launcher + ["app-server"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", errors="replace", bufsize=1,
)


def drain_stderr():
    for line in proc.stderr:
        print("STDERR:", line.rstrip())


threading.Thread(target=drain_stderr, daemon=True).start()


def send(obj):
    print(">> SEND:", json.dumps(obj, ensure_ascii=False))
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


send({"method": "initialize", "id": 0, "params": {
    "clientInfo": {"name": "probe", "version": "0.1.0"},
    "capabilities": {"experimentalApi": True},
}})

deadline = time.time() + 40
while time.time() < deadline:
    line = proc.stdout.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    print("<< RECV:", line)
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    method = msg.get("method")

    # auto-approve any approval request
    if method and "approval" in method.lower() and msg.get("id") is not None:
        send({"id": msg["id"], "result": {"decision": "accept"}})
        continue

    # drive the handshake
    if msg.get("id") == 0 and "result" in msg:
        send({"method": "initialized", "params": {}})
        send({"method": "thread/start", "id": 1, "params": {
            "cwd": ".", "approvalPolicy": "onRequest", "sandbox": "workspaceWrite",
        }})
    elif msg.get("id") == 1 and "result" in msg:
        tid = msg.get("result", {}).get("thread", {}).get("id")
        print(">> thread id:", tid)
        send({"method": "turn/start", "id": 2, "params": {
            "threadId": tid,
            "input": [{"type": "text", "text": PROMPT}],
            "cwd": ".", "approvalPolicy": "onRequest", "sandbox": "workspaceWrite",
        }})
    elif method in ("turn/completed", "turn/failed"):
        print(">> turn finished, stopping")
        break

try:
    proc.terminate()
except OSError:
    pass
print(">> done")
