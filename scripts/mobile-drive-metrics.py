#!/usr/bin/env python3
"""Passive metrics collector for the mobile-drive plugin. Zero tokens: it never prints anything, so
nothing reaches the model's context. It appends one JSON line per mobile event to
~/.claude/mobile-drive/metrics/<YYYY-MM>.jsonl (override with MOBILE_DRIVE_METRICS_DIR), for
`mobile-drive-stats.py` and the `/mobile-drive:retro` skill to review later.

Registered for: PostToolUse + PostToolUseFailure (Bash), SubagentStop, SessionEnd.

Recorded: command verbs, selector *kinds*, labels of elements acted on, flow names, replay
outcomes, fallbacks, runner reports, screen-check verdicts and counts, inspector PASS/FAIL/UNSURE
counts, and per-session token totals read from the transcript — whole-session and device work only
(`mobile_*`: API responses that issue a device command, plus the one right after that reads its output).
An `agent-device` event is recorded only when agent-device is the command word of a shell segment
and the verb is a real agent-device command — never for text in quotes, heredocs, echo, grep or git.
Never recorded: typed text (fill values), screenshots, snapshots, anything not mobile-related.
Any error inside this hook is swallowed — metrics must never break a session.
"""
import collections
import glob
import json
import os
import re
import sys
import time

DIR = os.path.expanduser(os.environ.get("MOBILE_DRIVE_METRICS_DIR", "~/.claude/mobile-drive/metrics"))
KEEP_MONTHS = 6
MOBILE = re.compile(r"agent-device|replay\.sh|ui-audit\.py|\badb\b|\bsimctl\b|\bidb\b|\baxe\b")
AUDIT = re.compile(r"^ui-audit (\S+) (\w+): (PASS|FAIL) — (\d+) fail, (\d+) warn, (\d+) for eyes \(matched (\d+)/(\d+)\)",
                   re.M)
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
# Real agent-device verbs: the 72 commands of v0.21.22 (`agent-device --help` → `help commands`), plus
# `help` and the aliases this collector has always handled. Anything else (`$c`, `--version`, a word
# from prose) is not recorded.
VERBS = {
    "action-button", "alert", "app-switcher", "apps", "appstate", "artifacts", "audio", "auth", "back",
    "batch", "boot", "capabilities", "cdp", "click", "clipboard", "close", "connect", "connection", "daemon",
    "debug", "device", "devices", "diff", "disconnect", "doctor", "events", "fill", "find", "focus", "fold",
    "gesture", "get", "help", "home", "hover", "install", "install-from-source", "is", "keyboard", "logs",
    "longpress", "mcp", "metro", "network", "open", "orientation", "perf", "plugins", "prepare", "press",
    "proxy", "push", "react-devtools", "react-native", "record", "reinstall", "replay", "screenshot",
    "scroll", "session", "settings", "shutdown", "snapshot", "swipe", "takeover", "test", "trace",
    "trigger-app-event", "tv-remote", "type", "viewport", "wait", "web",
    "tap", "long-press",  # older spellings
}
# Flags that take a value, so a flag before the verb (`--platform ios snapshot`) skips its value too.
VALUE_FLAGS = {
    "--platform", "--serial", "--udid", "--device", "--session", "--config", "--state-dir", "--target",
    "--surface", "--tenant", "--session-isolation", "--session-lock", "--lease-id", "--lease-backend",
    "--run-id", "--remote-config", "--daemon-base-url", "--daemon-auth-token", "--daemon-transport",
    "--daemon-server-mode", "--ios-simulator-device-set", "--android-device-allowlist", "--app", "--host",
    "--port", "--timeout", "--provider", "--provider-session", "--project-root", "--artifacts-dir",
    "--out", "--scope", "-s", "--depth", "-d", "--runtime-file", "--bearer-token", "--proxy-base-url",
}
STOP_FLAGS = {"--help", "-h", "--version", "-V"}
RAW_TOOLS = {"adb", "xcrun", "simctl", "idb", "axe"}
ENV_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
REDIRECT = re.compile(r"\d*(>>?|<|>&|&>)(.*)", re.S)
HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
OPERATORS = ("&&", "||", ";", "\n", "|", "&")


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
    for n, t in enumerate(tokens):
        if t == name:
            return tokens[n + 1] if n + 1 < len(tokens) else None
        if t.startswith(name + "="):
            return t[len(name) + 1:]
    return None


def drop_heredocs(cmd):
    """Remove heredoc bodies (commit messages, scripts fed to cat/python) — they are data, not commands."""
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in HEREDOC.finditer(line):  # several heredocs on one line: their bodies follow in order
            while i < len(lines) and lines[i].strip() != m.group(2):
                i += 1
            i += 1  # the terminator line
    return "\n".join(out)


def segments(cmd):
    """Split a shell command into simple commands on && || ; | & and newlines, never inside quotes.
    Each segment is a list of (text, quoted) tokens; quoted is True if any part was quoted."""
    cmd = drop_heredocs(cmd)
    segs, cur = [], []
    tok, quoted, started = "", False, False
    i, n = 0, len(cmd)

    def flush_tok():
        nonlocal tok, quoted, started
        if started:
            cur.append((tok, quoted))
        tok, quoted, started = "", False, False

    def flush_seg():
        nonlocal cur
        flush_tok()
        if cur:
            segs.append(cur)
        cur = []

    while i < n:
        c = cmd[i]
        if c == "'":
            j = cmd.find("'", i + 1)
            j = n if j < 0 else j
            tok, quoted, started, i = tok + cmd[i + 1:j], True, True, j + 1
        elif c == '"':
            i += 1
            while i < n and cmd[i] != '"':
                if cmd[i] == "\\" and i + 1 < n and cmd[i + 1] in '"\\$`':
                    i += 1
                tok += cmd[i]
                i += 1
            quoted, started, i = True, True, i + 1
        elif c == "\\":
            if cmd[i + 1:i + 2] not in ("", "\n"):  # backslash-newline is a line continuation
                tok, started = tok + cmd[i + 1], True
            i += 2
        elif c in " \t":
            flush_tok()
            i += 1
        elif c == "#" and not started:  # comment to the end of the line
            j = cmd.find("\n", i)
            i = n if j < 0 else j
        elif c == "&" and cmd[i + 1:i + 2] != "&" and (tok.endswith((">", "<")) or cmd[i + 1:i + 2] == ">"):
            tok, started, i = tok + c, True, i + 1  # 2>&1, >&2, &>file
        elif c in ";\n|&":
            flush_seg()
            i += 2 if cmd[i:i + 2] in ("&&", "||") else 1
        else:
            tok, started, i = tok + c, True, i + 1
    flush_seg()
    return segs


KEYWORDS = {"(", "{", "!", "if", "then", "elif", "else", "do", "while", "until", "time"}


def command_word(tokens):
    """Index of the segment's command word: past shell keywords and FOO=bar assignments."""
    i = 0
    while i < len(tokens) and ((tokens[i][0] in KEYWORDS and not tokens[i][1]) or ENV_ASSIGN.match(tokens[i][0])):
        i += 1
    return i


def strip_redirects(tokens):
    out, skip = [], False
    for text, quoted in tokens:
        if skip:
            skip = False
            continue
        m = None if quoted else REDIRECT.fullmatch(text)
        if m:
            skip = not m.group(2)  # `> file`: the target is the next token
            continue
        out.append(text)
    return out


def parse_agent_device(tokens):
    """An `ad` event if agent-device is the command word of this segment and the verb is real, else None."""
    i = command_word(tokens)
    if i >= len(tokens):
        return None
    word = tokens[i][0]
    if word == "npx":
        i += 1
        while i < len(tokens) and tokens[i][0].startswith("-"):
            i += 1
        if i >= len(tokens) or not re.fullmatch(r"agent-device(@[\w.^~-]+)?", tokens[i][0]):
            return None
    elif os.path.basename(word) != "agent-device":
        return None
    args = strip_redirects(tokens[i + 1:])
    pre, j = {}, 0
    while j < len(args) and args[j].startswith("-"):  # global flags before the verb
        name, eq, value = args[j].partition("=")
        if name in STOP_FLAGS:
            return None
        if eq:
            pre[name], j = value, j + 1
        elif name in VALUE_FLAGS and j + 1 < len(args):
            pre[name], j = args[j + 1], j + 2
        else:
            j += 1
    if j >= len(args) or args[j] not in VERBS:
        return None
    args = args[j:]
    verb = args[0]
    ev = {"kind": "ad", "verb": verb, "settle": "--settle" in args,
          "platform": pre.get("--platform") or flag(args, "--platform"),
          "serial": pre.get("--serial") or flag(args, "--serial")}
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
            if verb == "type" and ev["selector"] in ("text", "coord"):
                del ev["selector"]  # `type <text>`: the argument is the typed text itself — never recorded
            elif ev["selector"] == "ref":
                ev["ref"] = target.split("~")[0]  # @e12~s345 → @e12
            elif ev["selector"] != "coord":
                ev["label"] = label_of(target)
            if ev.get("label") and RISKY.search(ev["label"]):
                ev["risky"] = True
    elif verb == "screenshot":
        ev["scaled"] = "--scale" in args
        ev["overlay"] = "--overlay-refs" in args
    elif verb == "help":
        ev["topic"] = " ".join([a for a in args[1:] if not a.startswith("-")][:2])
    elif verb == "replay" and len(args) > 1:
        ev["flow"] = os.path.basename(args[1])
    return {k: v for k, v in ev.items() if v not in (None, False, "")}


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

    if "ui-audit.py" in cmd:
        m = AUDIT.search(out)
        if m:  # screen check: verdict and counts only, never the findings' text
            checks = collections.Counter(re.findall(r"^(?:FAIL|WARN) (\S+)", out, re.M))
            write({**base, "kind": "ui_audit", "screen": m.group(1), "platform": m.group(2), "verdict": m.group(3),
                   "fail": int(m.group(4)), "warn": int(m.group(5)), "eyes": int(m.group(6)),
                   "matched": int(m.group(7)), "spec_elements": int(m.group(8)), "checks": dict(checks)}, session)

    for seg in segments(cmd):
        i = command_word(seg)
        if i < len(seg) and os.path.basename(seg[i][0]) in RAW_TOOLS:  # not `echo "adb …"` or a commit message
            text = " ".join(t for t, _ in seg[i:])
            for pattern, kind in RAW_FALLBACK:
                if pattern.search(text):
                    write({**base, "kind": "fallback", "type": kind}, session)
                    break
        ev = parse_agent_device(seg)
        if not ev:
            continue
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
    if agent == "ui-inspector" or agent.endswith(":ui-inspector"):
        verdicts = collections.Counter(re.findall(r"^\S+?: (PASS|FAIL|UNSURE)\b", data.get("last_assistant_message") or "",
                                                  re.M))
        write({"kind": "inspector", "agent": "ui-inspector", **{k.lower(): v for k, v in verdicts.items()}},
              data.get("session_id") or "unknown")
        return
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


def issues_device_command(content):
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash" and
        MOBILE.search(str((b.get("input") or {}).get("command") or "")) for b in content)


def transcript_usage(path):
    """Token totals for the whole session, and for device work only: an API response counts as device
    work if it issues a mobile Bash command, or is the next response in the same file (it reads the
    command's output). Subagent transcripts are counted by the same rule."""
    tot = {"turns": 0, "peak_context": 0, "by_model": {},
           "mobile_calls": 0, "mobile_input": 0, "mobile_output": 0, "mobile_by_model": {}}

    def add(by_model, model, ctx, out):
        m = by_model.setdefault(model, {"input": 0, "output": 0, "calls": 0})
        m["input"] += ctx
        m["output"] += out
        m["calls"] += 1

    files = [path] + sorted(glob.glob(os.path.join(path[:-len(".jsonl")], "subagents", "*.jsonl")))
    for p in files:
        responses, by_id = [], {}  # one API response can be split over several lines, one per block
        with open(p, errors="replace") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue
                msg = e.get("message")
                if e.get("type") != "assistant" or not isinstance(msg, dict) or not msg.get("usage"):
                    continue
                rid = msg.get("id") or f"line-{len(responses)}"
                r = by_id.get(rid)
                if r is None:
                    r = by_id[rid] = {"model": msg.get("model", "?"), "usage": msg["usage"], "device": False}
                    responses.append(r)
                r["device"] = r["device"] or issues_device_command(msg.get("content"))
        after_device = False
        for r in responses:
            u = r["usage"]
            ctx = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            out = u.get("output_tokens", 0)
            add(tot["by_model"], r["model"], ctx, out)
            if p == path:
                tot["turns"] += 1
                tot["peak_context"] = max(tot["peak_context"], ctx)
            if r["device"] or after_device:
                add(tot["mobile_by_model"], r["model"], ctx, out)
                tot["mobile_calls"] += 1
                tot["mobile_input"] += ctx
                tot["mobile_output"] += out
            after_device = r["device"]
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
