"""Inspect the newest codex rollout JSONL to learn its structure.

Run:  python inspect_rollout.py
Prints the newest rollout file's path, its session cwd, the turn_context
(approval/sandbox/permission profile), every distinct event type seen, and the
last few events. Paste the output back so the TUI adapter's completion-detection
and result-parsing can be matched exactly.
"""
import glob
import json
import os

base = os.path.expanduser("~/.codex/sessions")
files = glob.glob(os.path.join(base, "**", "rollout-*.jsonl"), recursive=True)
if not files:
    raise SystemExit(f"no rollout files under {base}")

f = max(files, key=os.path.getmtime)
print("FILE:", f)

lines = [l for l in open(f, encoding="utf-8").read().splitlines() if l.strip()]
print("TOTAL LINES:", len(lines))

records = []
for l in lines:
    try:
        records.append(json.loads(l))
    except json.JSONDecodeError:
        pass

# distinct top-level types and event_msg subtypes
top_types = {}
event_types = {}
fn_names = set()
for r in records:
    t = r.get("type")
    top_types[t] = top_types.get(t, 0) + 1
    payload = r.get("payload", {})
    if t == "event_msg" and isinstance(payload, dict):
        et = payload.get("type")
        event_types[et] = event_types.get(et, 0) + 1
    if t == "response_item" and isinstance(payload, dict) and payload.get("type") == "function_call":
        fn_names.add(payload.get("name"))

print("\nTOP-LEVEL TYPES:", top_types)
print("EVENT_MSG TYPES:", event_types)
print("FUNCTION_CALL NAMES:", fn_names)

# session cwd + turn_context
for r in records:
    if r.get("type") == "session_meta":
        p = r.get("payload", {})
        print("\nSESSION cwd:", p.get("cwd"), "| originator:", p.get("originator"))
    if r.get("type") == "turn_context":
        p = r.get("payload", {})
        print("TURN_CONTEXT approval_policy:", p.get("approval_policy"),
              "| sandbox_policy:", p.get("sandbox_policy"),
              "| permission_profile:", p.get("permission_profile"))

# last 6 events, compact (truncate big fields)
def compact(r):
    t = r.get("type")
    p = r.get("payload", {})
    if isinstance(p, dict):
        pt = p.get("type")
        if pt == "function_call":
            return f"{t}/{pt} name={p.get('name')}"
        if pt == "function_call_output":
            return f"{t}/{pt} (output omitted)"
        if pt in ("agent_message", "user_message"):
            msg = (p.get("message") or "")[:80]
            return f"{t}/{pt} msg={msg!r}"
        if pt == "reasoning":
            return f"{t}/reasoning"
        return f"{t}/{pt}"
    return t

print("\nLAST 8 EVENTS:")
for r in records[-8:]:
    print(" -", compact(r))
