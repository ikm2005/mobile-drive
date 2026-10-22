#!/usr/bin/env python3
"""Passive metrics collector for the mobile-drive plugin. Zero tokens: it never prints anything, so
nothing reaches the model's context. It appends one JSON line per mobile event to
~/.claude/mobile-drive/metrics/<YYYY-MM>.jsonl (override with MOBILE_DRIVE_METRICS_DIR), for
`mobile-drive-stats.py` and the `/mobile-drive:retro` skill to review later.

Registered for: PostToolUse + PostToolUseFailure (Bash), SubagentStop, SessionEnd.

Recorded: command verbs, selector *kinds*, labels of elements acted on, flow names, replay
outcomes, fallbacks, runner reports, and per-session token totals read from the transcript.
Never recorded: typed text (fill values), screenshots, snapshots, anything not mobile-related.
Any error inside this hook is swallowed — metrics must never break a session.
"""
import glob
import json
import os
import re
import shlex
import sys
import time

DIR = os.path.expanduser(os.environ.get("MOBILE_DRIVE_METRICS_DIR", "~/.claude/mobile-drive/metrics"))
KEEP_MONTHS = 6
MOBILE = re.compile(r"agent-device|replay\.sh|\badb\b|\bsimctl\b|\bidb\b|\baxe\b")
RAW_FALLBACK = [
    (re.compile(r"\badb\b[^|;&]*\b(exec-out|shell)\s+(\S+\s+)*screencap\b"), "raw_screenshot"),
    (re.compile(r"\buiautomator\s+dump\b"), "raw_tree_dump"),
    (re.compile(r"\bsimctl\s+io\s+\S+\s+screenshot\b"), "raw_screenshot"),
    (re.compile(r"\badb\b[^|;&]*\bshell\s+input\s+(tap|swipe|draganddrop)\b"), "raw_tap"),
    (re.compile(r"\bidb\s+ui\s+(tap|swipe)\b"), "raw_tap"),
    (re.compile(r"\baxe\s+(tap|swipe)\b"), "raw_tap"),
]
ACTIONS = {"press", "click", "tap", "fill", "longpress", "long-press", "swipe", "scroll", "type", "find"}
RISKY = re.compile(r"\b(delete|remove|erase|reset|log ?out|sign ?out|unsubscribe|cancel subscription)\b", re.I)


def write(ev, session):
    os.makedirs(DIR, exist_ok=True)
    ev = {"ts": int(time.time()), "session": session, **ev}
    with open(os.path.join(DIR, time.strftime("%Y-%m") + ".jsonl"), "a") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    # marks the session as mobile, so SessionEnd knows to record its token totals
    os.makedirs(os.path.join(DIR, ".sessions"), exist_ok=True)
    open(os.path.join(DIR, ".sessions", session), "a").close()


def selector_kind(arg):
    if arg.startswith("@"):
        return "ref"
    if re.fullmatch(r"-?\d+(\.\d+)?", arg or ""):
        return "coord"
    for k in ("id", "label", "text", "value", "role"):
        if re.search(rf"\b{k}=", arg):
            return "id" if k == "id" else ("role" if k == "role" else "label")
    return "text"


def label_of(arg):
    m = re.search(r'(?:label|text|value|id)="([^"]{0,60})', arg)
    return m.group(1) if m else (None if arg.startswith("@") else arg[:60])


def flag(tokens, name):
    return tokens[tokens.index(name) + 1] if name in tokens and tokens.index(name) + 1 < len(tokens) else None


def parse_agent_device(segment):
    try:
        t = shlex.split(segment)
    except ValueError:
        t = segment.split()
    i = next((n for n, x in enumerate(t) if x.endswith("agent-device")), None)
    if i is None or i + 1 >= len(t):
        return None
    args = t[i + 1:]
    verb = args[0]
    ev = {"kind": "ad", "verb": verb, "settle": "--settle" in args, "platform": flag(args, "--platform"),
          "serial": flag(args, "--serial")}
    if verb == "open" and len(args) > 1:
        ev["app"] = args[1]
    elif verb in ACTIONS and len(args) > 1:
        target = args[1]
        if verb == "scroll":
            ev["direction"] = target
            until = flag(args, "--until")
            ev["selector"] = "until" if until else "plain"
        else:
            ev["selector"] = selector_kind(target)
            if ev["selector"] == "ref":
                ev["ref"] = target.split("~")[0]  # @e12~s345 → @e12
            elif ev["selector"] != "coord":
                ev["label"] = label_of(target)
            if ev.get("label") and RISKY.search(ev["label"]):
                ev["risky"] = True
    elif verb == "screenshot":
        ev["scaled"] = "--scale" in args
        ev["overlay"] = "--overlay-refs" in args
    elif verb == "help":
        ev["topic"] = " ".join(args[1:3])
    elif verb == "replay" and len(args) > 1:
        ev["flow"] = os.path.basename(args[1])
    return {k: v for k, v in ev.items() if v not in (None, False)}


def on_bash(data):
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not MOBILE.search(cmd):
        return
    session = data.get("session_id") or "unknown"
    resp = data.get("tool_response") or {}
    out = resp.get("stdout", "") + resp.get("stderr", "") if isinstance(resp, dict) else str(resp)
    out += str(data.get("error") or "")
    base = {"agent": data.get("agent_type") or "main", "repo": os.path.basename(data.get("cwd") or ""),
            "failed": data.get("hook_event_name") == "PostToolUseFailure"}
    segments = [s for s in re.split(r"&&|\|\||;|\n", cmd) if s.strip()]
    ad_count = 0

    if "replay.sh" in cmd:
        m = re.search(r"^(PASS|DIVERGED) replay (\S+)(?: \(([^)]*)\))?", out, re.M)
        step = re.search(r"Replay failed at step (\d+) \((.{0,120}?)\)?:", out)
        if m:  # no result line = the script never ran (e.g. waiting for permission)
            write({**base, "kind": "replay", "flow": m.group(2), "outcome": m.group(1),
                   "dismissed": "dismiss" in (m.group(3) or ""),
                   "step": int(step.group(1)) if step else None,
                   "step_cmd": step.group(2) if step else None,
                   "error": None if step else (re.search(r"Error \((\w+)\)", out) or [None, None])[1]},
                  session)

    for seg in segments:
        for pattern, kind in RAW_FALLBACK:
            if pattern.search(seg):
                write({**base, "kind": "fallback", "type": kind}, session)
                break
        if "agent-device" in seg and "replay.sh" not in seg:
            ev = parse_agent_device(seg)
            if ev:
                ad_count += 1
                if ev["verb"] == "replay":
                    step = re.search(r"Replay failed at step (\d+)", out)
                    ev["outcome"] = "DIVERGED" if (base["failed"] or step) else "PASS"
                    ev["step"] = int(step.group(1)) if step else None
                if ev.get("selector") == "coord" or ev["verb"] == "screenshot":
                    write({**base, "kind": "fallback",
                           "type": "coord_tap" if ev.get("selector") == "coord" else "ad_screenshot",
                           "scaled": ev.get("scaled", False)}, session)
                ev["repeat"] = is_repeat(session, base["agent"], ev)
                write({**base, **ev}, session)
    if ad_count:
        write({**base, "kind": "call", "ad_commands": ad_count}, session)


def is_repeat(session, agent, ev):
    """Same action on the same target as the previous action in this session/agent."""
    if ev["verb"] not in ACTIONS or ev["verb"] == "scroll" and ev.get("selector") == "until":
        return False
    key = os.path.join(DIR, ".sessions", f"{session}.{agent}.last")
    sig = json.dumps([ev["verb"], ev.get("selector"), ev.get("ref"), ev.get("label"), ev.get("direction")])
    prev = open(key).read() if os.path.exists(key) else ""
    os.makedirs(os.path.dirname(key), exist_ok=True)
    open(key, "w").write(sig)
    return prev == sig


def on_subagent_stop(data):
    agent = data.get("agent_type") or ""
    if agent != "mobile-runner" and not agent.endswith(":mobile-runner"):  # plugin agents are namespaced
        return
    msg = data.get("last_assistant_message") or ""
    first = msg.strip().splitlines()[0] if msg.strip() else ""
    status = "PASS" if first.startswith("PASS") else ("FAIL" if first.startswith("FAIL") else "UNKNOWN")
    flow = re.search(r"^flow:\s*(.+)$", msg, re.M)
    notes = re.search(r"^notes:\s*(.*)", msg, re.M | re.S)
    note_lines = [l.strip(" -") for l in (notes.group(1).splitlines() if notes else []) if l.strip(" -")]
    write({"kind": "runner", "agent": "mobile-runner", "status": status,
           "reason": first[5:].strip()[:200] if status == "FAIL" else None,
           "flow": (re.findall(r"[\w.-]+\.ad\b", flow.group(1)) or [None])[0] if flow else None,
           "notes": [n[:200] for n in note_lines if n.lower() not in ("none", "n/a")][:15]},
          data.get("session_id") or "unknown")


def transcript_usage(path):
    tot = {"turns": 0, "peak_context": 0, "by_model": {}}
    files = [path] + glob.glob(os.path.join(path[:-len(".jsonl")], "subagents", "*.jsonl"))
    for p in files:
        seen = set()
        for line in open(p):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            msg = e.get("message") or {}
            if e.get("type") != "assistant" or not isinstance(msg, dict) or not msg.get("usage"):
                continue
            if msg.get("id") in seen:  # one API response can be split over several lines
                continue
            seen.add(msg.get("id"))
            u = msg["usage"]
            ctx = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            m = tot["by_model"].setdefault(msg.get("model", "?"), {"input": 0, "output": 0, "calls": 0})
            m["input"] += ctx
            m["output"] += u.get("output_tokens", 0)
            m["calls"] += 1
            if p == path:
                tot["turns"] += 1
                tot["peak_context"] = max(tot["peak_context"], ctx)
    return tot


def on_session_end(data):
    session = data.get("session_id") or "unknown"
    marker = os.path.join(DIR, ".sessions", session)
    if not os.path.exists(marker):  # this session never touched a device
        return
    path = data.get("transcript_path") or ""
    if os.path.exists(path):
        write({"kind": "session", "repo": os.path.basename(data.get("cwd") or ""), **transcript_usage(path)},
              session)
    for f in glob.glob(os.path.join(DIR, ".sessions", session + "*")):
        os.remove(f)
    cutoff = time.strftime("%Y-%m", time.localtime(time.time() - KEEP_MONTHS * 31 * 86400))
    for f in glob.glob(os.path.join(DIR, "*.jsonl")):
        if os.path.basename(f)[:7] < cutoff:
            os.remove(f)


def main():
    if os.environ.get("MOBILE_DRIVE_NO_METRICS") == "1":  # opt-out, set in settings.json "env"
        return
    try:
        data = json.load(sys.stdin)
        event = data.get("hook_event_name")
        if event in ("PostToolUse", "PostToolUseFailure") and data.get("tool_name") == "Bash":
            on_bash(data)
        elif event == "SubagentStop":
            on_subagent_stop(data)
        elif event == "SessionEnd":
            on_session_end(data)
    except Exception:  # never break the session over metrics; leave a trace for the digest
        try:
            import traceback
            os.makedirs(DIR, exist_ok=True)
            with open(os.path.join(DIR, "errors.log"), "a") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc().splitlines()[-1] + "\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
