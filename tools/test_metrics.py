#!/usr/bin/env python3
"""Tests for scripts/mobile-drive-metrics.py, scripts/mobile-drive-stats.py and scripts/mobile-drive-nudge.py
(no device, no model). The scripts are loaded in-process so a line-coverage tracer can see them.
Run by tools/check.sh or on its own: python3 tools/test_metrics.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tools", "fixtures")
TMP = tempfile.mkdtemp(prefix="mobile-drive-metrics-test-")
# the scripts read these at import time (and tempfile caches its directory), so set them first
os.environ["MOBILE_DRIVE_METRICS_DIR"] = os.path.join(TMP, "metrics")
os.environ["TMPDIR"] = TMP
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ.pop("MOBILE_DRIVE_NO_NUDGE", None)
os.environ.pop("MOBILE_DRIVE_NO_METRICS", None)
tempfile.tempdir = TMP


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


md = load("mobile_drive_metrics", "mobile-drive-metrics.py")
st = load("mobile_drive_stats", "mobile-drive-stats.py")
nd = load("mobile_drive_nudge", "mobile-drive-nudge.py")
failures = []


def expect(name, cond, detail=""):
    print(("ok    " if cond else "FAIL  ") + "metrics: " + name + ("" if cond else f"  ({detail})"))
    if not cond:
        failures.append(name)
    return bool(cond)


def use_dir(path):
    """Point both scripts at a fresh metrics directory."""
    os.makedirs(path, exist_ok=True)
    md.DIR = st.DIR = path
    st.REVIEW_MARK = os.path.join(path, ".last-review")


def events(session=None):
    out = []
    for f in sorted(os.listdir(md.DIR)):
        if f.endswith(".jsonl"):
            with open(os.path.join(md.DIR, f)) as fh:
                out += [json.loads(l) for l in fh if l.strip()]
    return [e for e in out if session is None or e["session"] == session]


def raw_jsonl():
    return "".join(open(os.path.join(md.DIR, f)).read() for f in os.listdir(md.DIR) if f.endswith(".jsonl"))


def bash(session, cmd, stdout="", stderr="", event="PostToolUse", agent=None, cwd="/tmp/app", error=None):
    data = {"hook_event_name": event, "tool_name": "Bash", "session_id": session, "cwd": cwd,
            "tool_input": {"command": cmd}, "tool_response": {"stdout": stdout, "stderr": stderr}}
    if agent:
        data["agent_type"] = agent
    if error:
        data["error"] = error
    md.on_bash(data)
    return events(session)


def run_main(mod, stdin_text, argv=None, env=None):
    """Call mod.main() with a patched stdin/stdout (and argv/env); return what it printed."""
    out, old_argv, old_env = io.StringIO(), sys.argv, dict(os.environ)
    sys.argv = argv or ["x"]
    os.environ.update(env or {})
    try:
        with contextlib.redirect_stdout(out):
            old_stdin, sys.stdin = sys.stdin, io.StringIO(stdin_text)
            try:
                mod.main()
            finally:
                sys.stdin = old_stdin
    finally:
        sys.argv = old_argv
        os.environ.clear()
        os.environ.update(old_env)
    return out.getvalue()


# 1. Parser table: only real agent-device commands are recorded
use_dir(os.path.join(TMP, "metrics"))
ad = lambda evs: [e for e in evs if e["kind"] == "ad"]
HEREDOC_COMMIT = ("git commit -m \"$(cat <<'EOF'\nREADME: install notes\n\n"
                  "docs: agent-device builds the runner\nagent-device snapshot -i\nEOF\n)\"")
table = [
    ("help topic without the redirection", "agent-device help snapshot 2>&1 | head -40",
     lambda e: [(x["verb"], x.get("topic")) for x in ad(e)] == [("help", "snapshot")]),
    ("--version is not a verb", "agent-device --version", lambda e: not ad(e)),
    ("git commit message", 'git commit -m "docs: agent-device builds the runner"', lambda e: not ad(e)),
    ("grep pattern", 'grep -rn "agent-device snapshot" skills/', lambda e: not ad(e)),
    ("echo text with backticks", 'echo "run `agent-device snapshot -i` → then press"', lambda e: not ad(e)),
    ("$var verb", "c=open; agent-device $c com.example.app", lambda e: not ad(e)),
    ("press ref --settle", "agent-device press @e3 --settle",
     lambda e: [(x["verb"], x.get("selector"), x.get("ref"), x.get("settle")) for x in ad(e)] ==
     [("press", "ref", "@e3", True)]),
    ("press by coordinates", "agent-device press 540 1200",
     lambda e: [(x["verb"], x.get("selector")) for x in ad(e)] == [("press", "coord")] and
     [x["type"] for x in e if x["kind"] == "fallback"] == ["coord_tap"]),
    ("fill records the label, never the text", "agent-device fill 'label=\"Name\"' \"secret text\" --settle",
     lambda e: [(x["verb"], x.get("label")) for x in ad(e)] == [("fill", "Name")] and "secret text" not in raw_jsonl()),
    ("adb am start", "adb shell am start -n com.example/.Main",
     lambda e: not ad(e) and not [x for x in e if x["kind"] == "fallback"]),
    ("global flag before the verb", "agent-device --platform ios snapshot -i",
     lambda e: [(x["verb"], x.get("platform")) for x in ad(e)] == [("snapshot", "ios")]),
    ("npx with a version", "npx agent-device@0.21.22 snapshot -i", lambda e: [x["verb"] for x in ad(e)] == ["snapshot"]),
]
passed = 0
for n, (name, cmd, check) in enumerate(table, 1):
    evs = bash(f"parse{n}", cmd, stdout="ok")
    passed += expect(f"parse {n:2}: {name}", check(evs), evs)
print(f"      parser table: {passed}/{len(table)}")
evs = bash("parse-heredoc", HEREDOC_COMMIT)
expect("parse: heredoc commit body is not a command", not ad(evs), evs)
evs = bash("parse-chain", "agent-device press @e3 --settle && agent-device wait 'label=\"Saved\"'")
expect("parse: && chain gives two events and one call", [x["verb"] for x in ad(evs)] == ["press", "wait"] and
       [x["ad_commands"] for x in evs if x["kind"] == "call"] == [2], evs)
evs = bash("parse-misc", "FOO=1 ~/.local/bin/agent-device --serial emulator-5554 --json open com.example.app > /dev/null; "
                         "if agent-device is visible 'id=\"save\"'; then agent-device type 'hello there'; fi")
got = [(x["verb"], x.get("serial"), x.get("app"), x.get("selector"), x.get("label")) for x in ad(evs)]
expect("parse: env, path prefix, value flag, keywords; `type` text never recorded",
       got == [("open", "emulator-5554", "com.example.app", None, None), ("is", None, None, None, None),
               ("type", None, None, None, None)] and "hello there" not in raw_jsonl(), got)
evs = bash("parse-misc2", "agent-device --platform=android --help snapshot ; agent-device scroll down --until "
                          "'label=\"End\"' --platform=ios & npx -y agent-device swipe @e1~s2 \\\n  up # trailing comment")
got = [(x["verb"], x.get("selector"), x.get("ref"), x.get("platform"), x.get("direction")) for x in ad(evs)]
expect("parse: --help stops, --flag=value, scroll --until, npx -y, ~sN pin, continuation",
       got == [("scroll", "until", None, "ios", "down"), ("swipe", "ref", "@e1", None, None)], got)
evs = bash("parse-misc3", "agent-device screenshot s.png --scale 0.5 --overlay-refs && agent-device screenshot s.png && "
                          "agent-device press 'label=\"Delete account\"' && agent-device help && "
                          "agent-device replay flows/add.ad && agent-device unknownverb", stdout="")
shots = [x for x in evs if x["kind"] == "fallback"]
expect("parse: screenshot scale/overlay → ad_screenshot fallbacks", [x.get("scaled") for x in shots] == [True, False] and
       ad(evs)[0].get("overlay") is True, shots)
expect("parse: risky label flagged", any(x.get("risky") and x.get("label") == "Delete account" for x in ad(evs)), evs)
expect("parse: help without topic, replay verb PASS, unknown verb dropped",
       [x["verb"] for x in ad(evs)][-2:] == ["help", "replay"] and ad(evs)[-1].get("outcome") == "PASS" and
       ad(evs)[-1].get("flow") == "add.ad" and not ad(evs)[-2].get("topic"), ad(evs))
expect("tokenizer: redirection targets and escaped quotes", md.strip_redirects(md.segments(
    'agent-device get text "say \\"hi\\"" > out.txt 2> err.txt')[0]) == ["agent-device", "get", "text", 'say "hi"'] and
    md.flag(["--until"], "--until") is None, md.segments('agent-device get text "say \\"hi\\"" > out.txt'))
expect("tokenizer: unterminated quote and empty segments", md.segments("agent-device fill @e1 'oops") ==
       [[("agent-device", False), ("fill", False), ("@e1", False), ("oops", True)]] and md.segments(" ; && ") == [] and
       md.segments("a\\ b\\;c") == [[("a b;c", False)]] and
       md.parse_agent_device([("FOO=1", False)]) is None and md.parse_agent_device([("npx", False), ("-y", False)]) is None)
expect("selector kinds", [md.selector_kind(a) for a in ("@e1", "12.5", 'id="x"', 'role="button"', 'text="Hi"', "Hi")] ==
       ["ref", "coord", "id", "role", "label", "text"])

# 2. on_bash: replay.sh, agent-device replay, ui-audit, raw fallbacks, repeats, failures
evs = bash("replay1", "bash scripts/replay.sh add-item.android.ad", stdout="PASS replay add-item.android.ad (5 steps)\n")
r = [x for x in evs if x["kind"] == "replay"]
expect("replay.sh PASS recorded", len(r) == 1 and r[0]["outcome"] == "PASS" and r[0]["flow"] == "add-item.android.ad" and
       r[0]["step"] is None and not r[0]["dismissed"] and not ad(evs), evs)
evs = bash("replay2", "bash scripts/replay.sh add-item.android.ad",
           stdout='DIVERGED replay add-item.android.ad (1 popup dismissed)\n'
                  'Replay failed at step 3 (press label="Save" --settle): element not found\n', event="PostToolUseFailure")
r = [x for x in evs if x["kind"] == "replay"]
expect("replay.sh DIVERGED with step", len(r) == 1 and r[0]["outcome"] == "DIVERGED" and r[0]["step"] == 3 and
       r[0]["step_cmd"].startswith('press label="Save"') and r[0]["dismissed"] and r[0]["failed"], r)
evs = bash("replay3", "bash scripts/replay.sh add-item.android.ad", stdout="DIVERGED replay add-item.android.ad\n",
           error="Error (DEVICE_NOT_FOUND) no device")
r = [x for x in evs if x["kind"] == "replay"]
expect("replay.sh error before any step", len(r) == 1 and r[0]["error"] == "DEVICE_NOT_FOUND" and r[0]["step"] is None, r)
expect("replay.sh without a result line records nothing", bash("replay4", "bash scripts/replay.sh x.ad", stdout="") == [])
evs = bash("replay5", "agent-device replay flows/x.ad", stdout="Replay failed at step 2 (fill @e1): stale ref")
expect("agent-device replay DIVERGED", [(x.get("outcome"), x.get("step")) for x in ad(evs)] == [("DIVERGED", 2)], evs)
audit_out = ("ui-audit items ios: FAIL — 1 fail, 2 warn, 1 for eyes (matched 5/6)\n"
             "FAIL missing    item_add: id=item_add not on screen\nWARN moved   save: 12pt\nWARN moved   x: 3pt\n")
evs = bash("audit1", "python3 scripts/ui-audit.py check items --platform ios", stdout=audit_out)
a = [x for x in evs if x["kind"] == "ui_audit"]
expect("ui-audit verdict and counts", len(a) == 1 and (a[0]["verdict"], a[0]["fail"], a[0]["warn"], a[0]["eyes"],
       a[0]["matched"], a[0]["spec_elements"], a[0]["checks"]) == ("FAIL", 1, 2, 1, 5, 6, {"missing": 1, "moved": 2})
       and "not on screen" not in raw_jsonl(), a)
expect("ui-audit without a summary records nothing", bash("audit2", "python3 scripts/ui-audit.py check x", stdout="usage") == [])
raw_cases = [("adb exec-out screencap -p > s.png", "raw_screenshot"), ("adb -s emulator-5554 shell input tap 10 20", "raw_tap"),
             ("adb shell uiautomator dump /sdcard/u.xml", "raw_tree_dump"),
             ("xcrun simctl io booted screenshot s.png", "raw_screenshot"), ("idb ui tap 1 2", "raw_tap"),
             ("axe tap -x 1 -y 2", "raw_tap"), ('"$ANDROID_HOME/platform-tools/adb" shell input swipe 1 2 3 4', "raw_tap")]
got = [[x["type"] for x in bash(f"raw{n}", c) if x["kind"] == "fallback"] for n, (c, _) in enumerate(raw_cases)]
expect("raw fallbacks by type", got == [[t] for _, t in raw_cases], got)
expect("raw fallback text inside echo is not a call", bash("raw-echo", 'echo "adb shell input tap 1 2"') == [])
expect("non-mobile command records nothing", bash("plain", "ls -la") == [])
for n in range(3):
    evs = bash("rep", "agent-device press @e7 --settle", agent="general-purpose")
bash("rep", "agent-device scroll down --until 'label=\"End\"'")
bash("rep", "agent-device scroll down --until 'label=\"End\"'")
evs = bash("rep", "agent-device press @e8")
reps = [x.get("repeat", False) for x in ad(evs)]
expect("repeat detection", reps == [False, True, True, False, False, False] and
       {x["agent"] for x in ad(evs)} == {"general-purpose", "main"}, reps)
expect("session marker written", os.path.exists(os.path.join(md.DIR, ".sessions", "rep")))

# 3. on_subagent_stop
stop = lambda **kw: md.on_subagent_stop({"hook_event_name": "SubagentStop", "session_id": "sub", **kw})
stop(agent_type="mobile-drive:mobile-runner", last_assistant_message="PASS\nflow: add-item.android.ad (recorded)\n"
     "created: one item\nnotes:\n- Save needs a name\n- none\n")
stop(agent_type="mobile-runner", last_assistant_message="FAIL Save button never enabled\nnotes: n/a")
stop(agent_type="mobile-drive:mobile-runner", last_assistant_message="")
stop(agent_type="mobile-drive:ui-inspector", last_assistant_message="item_icon: FAIL — different icon\nsave: PASS — ok\n"
     "logo: UNSURE — too small\nhero: PASS — ok")
stop(agent_type="general-purpose", last_assistant_message="PASS")
evs = events("sub")
runs = [x for x in evs if x["kind"] == "runner"]
insp = [x for x in evs if x["kind"] == "inspector"]
expect("runner PASS: flow and notes", runs[0]["status"] == "PASS" and runs[0]["flow"] == "add-item.android.ad" and
       runs[0]["notes"] == ["Save needs a name"], runs[0])
expect("runner FAIL reason, empty report UNKNOWN", (runs[1]["status"], runs[1]["reason"], runs[1]["notes"]) ==
       ("FAIL", "Save button never enabled", []) and runs[2]["status"] == "UNKNOWN", runs[1:])
expect("inspector verdict counts", len(insp) == 1 and (insp[0].get("pass"), insp[0].get("fail"), insp[0].get("unsure")) ==
       (2, 1, 1), insp)
expect("other subagents ignored", len(evs) == 4, evs)

# 4. on_session_end: device-only tokens from the fixture transcript
DEV = [3 + 9 * k for k in range(10)]
MOB = sorted(set(DEV) | {d + 1 for d in DEV})
ctx = lambda i: (10 + i) + 1000 * i + 7
tdir = os.path.join(TMP, "transcripts")
os.makedirs(tdir)
tpath = os.path.join(tdir, "sess.jsonl")
shutil.copy(os.path.join(FIX, "metrics-transcript.jsonl"), tpath)
with open(tpath, "a") as f:
    f.write("not json\n[1, 2]\n" + json.dumps({"type": "assistant", "message": "text only"}) + "\n")
bash("tok", "agent-device snapshot -i")
old = os.path.join(md.DIR, "2020-01.jsonl")
open(old, "w").write("{}\n")
md.on_session_end({"hook_event_name": "SessionEnd", "session_id": "tok", "transcript_path": tpath, "cwd": "/tmp/app"})
s = [x for x in events("tok") if x["kind"] == "session"]
s = s[0] if s else {}
expect("session: whole-session totals unchanged", s.get("turns") == 100 and
       sum(m["calls"] for m in s.get("by_model", {}).values()) == 100 and s.get("peak_context") == ctx(99) and
       sum(m["input"] for m in s.get("by_model", {}).values()) == sum(ctx(i) for i in range(100)), s)
expect("session: device-only calls = 10 commands + 10 follow-ups", s.get("mobile_calls") == 20 and len(MOB) == 20, s)
expect("session: device-only input is the sum of exactly those 20",
       s.get("mobile_input") == sum(ctx(i) for i in MOB) and s.get("mobile_output") == sum(5 + i for i in MOB), s)
mbm = s.get("mobile_by_model", {})
expect("session: mobile_by_model has the by_model shape",
       set(mbm) == {"model-a", "model-b"} and all(set(v) == {"input", "output", "calls"} for v in mbm.values()) and
       sum(v["calls"] for v in mbm.values()) == 20 and sum(v["input"] for v in mbm.values()) == s.get("mobile_input"), mbm)
expect("session end removes the session markers", not [f for f in os.listdir(os.path.join(md.DIR, ".sessions"))
                                                         if f.startswith("tok")])
expect("session end drops month files older than six months", not os.path.exists(old))
md.on_session_end({"hook_event_name": "SessionEnd", "session_id": "never-mobile", "transcript_path": tpath})
expect("session end without device activity records nothing", events("never-mobile") == [])
sub = os.path.join(tdir, "sess", "subagents")
os.makedirs(sub)
with open(os.path.join(sub, "agent-1.jsonl"), "w") as f:
    for i, cmd in enumerate(["ls", "adb shell input tap 1 2", "cat x", "cat y"]):
        f.write(json.dumps({"type": "assistant", "message": {"id": f"sub{i}", "model": "model-c", "usage": {
            "input_tokens": 1000 + i, "output_tokens": 1}, "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}) + "\n")
u = md.transcript_usage(tpath)
expect("subagent transcripts: same rule (command + next response)", u["mobile_calls"] == 22 and
       u["mobile_by_model"]["model-c"] == {"input": 1001 + 1002, "output": 2, "calls": 2} and u["turns"] == 100 and
       u["by_model"]["model-c"]["calls"] == 4, u["mobile_by_model"])

# 5. main(): hook dispatch, opt-out, error trail
use_dir(os.path.join(TMP, "metrics-main"))
hook = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "m1", "cwd": "/tmp/app",
        "tool_input": {"command": "agent-device snapshot -i"}, "tool_response": "plain string response"}
run_main(md, json.dumps(hook))
run_main(md, json.dumps({"hook_event_name": "SubagentStop", "agent_type": "mobile-runner", "session_id": "m1",
                         "last_assistant_message": "PASS"}))
run_main(md, json.dumps({**hook, "session_id": "m2"}), env={"MOBILE_DRIVE_NO_METRICS": "1"})
run_main(md, json.dumps({"hook_event_name": "SessionEnd", "session_id": "m1", "transcript_path": "/nonexistent.jsonl"}))
expect("main dispatches Bash and SubagentStop; opt-out records nothing",
       [x["kind"] for x in events()] == ["ad", "call", "runner"], events())
run_main(md, "{not json")
log = os.path.join(md.DIR, "errors.log")
expect("main swallows errors and leaves a trace", os.path.exists(log) and "Error" in open(log).read())

# 6. nudge: reminders at the 1st, 4th and 12th raw call only
shot = lambda sid, cmd="adb exec-out screencap -p > s.png", tool="Bash": json.dumps(
    {"tool_name": tool, "tool_input": {"command": cmd}, "session_id": sid})
outs = [run_main(nd, shot("nudge-a")) for _ in range(13)]
at = [n for n, o in enumerate(outs, 1) if o.strip()]
expect("nudge: reminders exactly at calls 1, 4, 12", at == [1, 4, 12], at)
texts = [json.loads(outs[n - 1])["hookSpecificOutput"]["additionalContext"] for n in at] if at == [1, 4, 12] else ["", "", ""]
expect("nudge 1 names the skill and the runner", "`mobile-drive:drive`" in texts[0] and
       "`mobile-drive:mobile-runner`" in texts[0] and "~5 steps" in texts[0], texts[0])
expect("nudge 2 is firmer: count, cost, walk, escape", "4 raw" in texts[1] and "2,400–3,300 tokens" in texts[1] and
       "walk, not a look" in texts[1] and "canvas, visual check" in texts[1] and len(texts[1]) < len(texts[0]), texts[1])
expect("nudge 3 says it is the last", texts[2].startswith("Last reminder") and "12 raw" in texts[2] and
       texts[2].count(". ") <= 1, texts[2])
expect("nudge: agent-device, other tools and bad input are silent",
       not run_main(nd, shot("nudge-b", "agent-device snapshot -i")) and not run_main(nd, shot("nudge-b", tool="Read")) and
       not run_main(nd, "{bad") and not os.path.exists(os.path.join(TMP, "mobile-drive-nudged-nudge-b")))
expect("nudge: opt-out", not run_main(nd, shot("nudge-c"), env={"MOBILE_DRIVE_NO_NUDGE": "1"}) and
       not os.path.exists(os.path.join(TMP, "mobile-drive-nudged-nudge-c")))
open(os.path.join(TMP, "mobile-drive-nudged-nudge-d"), "w").close()  # left by the once-per-session version
outs = [run_main(nd, shot("nudge-d")) for _ in range(3)]
expect("nudge: an old empty marker counts as one call", [bool(o.strip()) for o in outs] == [False, False, True], outs)
open(os.path.join(TMP, "mobile-drive-nudged-nudge-e"), "w").write("garbage")
expect("nudge: an unreadable count restarts at one", not run_main(nd, shot("nudge-e")))

# 7. digest
use_dir(os.path.join(TMP, "metrics-digest"))
os.makedirs(os.path.join(os.environ["HOME"], ".claude", "mobile-drive"))
json.dump({"model-a": {"input": 3, "output": 15}}, open(os.path.join(os.environ["HOME"], ".claude", "mobile-drive",
                                                                     "prices.json"), "w"))
now = int(time.time())
rows = []


def ev(session, kind, n=1, **kw):
    for _ in range(n):
        rows.append({"ts": now - 1000 + len(rows), "session": session, "kind": kind, "agent": "main", "repo": "shop-app",
                     "failed": False, **kw})


def sess_row(session, mobile_input=None):
    row = {"turns": 40, "peak_context": 90000, "by_model": {"model-a": {"input": 5_000_000, "output": 9000, "calls": 40}}}
    if mobile_input is not None:
        row.update(mobile_calls=8, mobile_input=mobile_input, mobile_output=800,
                   mobile_by_model={"model-a": {"input": mobile_input, "output": 800, "calls": 8}})
    ev(session, "session", **row)


ev("clean", "ad", verb="open", app="com.example.shop")
ev("clean", "ad", verb="press", selector="id", label="add")
ev("clean", "replay", flow="add-item.android.ad", outcome="PASS", dismissed=True)
sess_row("clean", 100_000)
ev("deleg", "ad", agent="mobile-drive:mobile-runner", verb="press", selector="ref", ref="@e1")
ev("deleg", "runner", agent="mobile-runner", status="FAIL", reason="Save never enabled", notes=["Save needs a name"])
sess_row("deleg")  # older data: no device-only split
ev("raw", "fallback", 3, type="raw_screenshot")
ev("raw", "fallback", 2, type="raw_tap")
ev("raw", "ad", verb="help", topic="snapshot")
sess_row("raw", 300_000)
ev("look", "ad", verb="snapshot")
ev("look", "ad", verb="screenshot", app=None)
ev("look", "fallback", type="ad_screenshot", scaled=False)
sess_row("look", 50_000)
for k in range(4):
    sid = f"walk{k}"
    ev(sid, "ad", 6, verb="press", selector="coord", repeat=True)
    ev(sid, "ad", 3, verb="fill", selector="label", label="Delete account", risky=True)
    ev(sid, "ad", 12, verb="scroll", selector="plain", direction="down", repeat=True)
    ev(sid, "ad", verb="help", topic="wait")
    ev(sid, "call", ad_commands=1)
    sess_row(sid, 200_000 * (k + 1) ** 2)
ev("div", "replay", flow="checkout.ios.ad", outcome="DIVERGED", step=4, step_cmd="press label=Pay", n=2)
ev("div", "replay", flow="checkout.ios.ad", outcome="DIVERGED", error="DEVICE_NOT_FOUND")
ev("ui", "ui_audit", screen="items", platform="ios", verdict="FAIL", fail=1, warn=0, eyes=2, matched=5,
   spec_elements=6, checks={"missing": 1})
ev("ui", "inspector", unsure=6, **{"pass": 3, "fail": 1})
ev("misc", "call", ad_commands=1)
with open(os.path.join(md.DIR, time.strftime("%Y-%m") + ".jsonl"), "w") as f:
    f.write("".join(json.dumps(r) + "\n" for r in rows) + "broken line\n")
open(os.path.join(md.DIR, "errors.log"), "w").write("2026-11-01 KeyError: 'x'\n")
text = st.digest(st.load(30), False)
first = text.splitlines()[0]
for kind in ("replay (clean): 1", "walk, delegated: 1", "raw loop (no tree): 1", "look only: 1", "walk, main session: 4",
             "replay + hand repair: 1", "screen check: 1", "other: 1"):
    expect(f"digest kind: {kind}", kind in first, first)
expect("digest: kinds list never empty", not first.rstrip().endswith("·") and "Sessions with device activity: 11" in first,
       first)
expect("digest: raw-loop action line", "the skill did not load in 1 session(s)" in text and
       "check the nudge and the routing rule" in text, text)
expect("digest: folder shown as folder, app id as app", "(folder) shop-app" in text and "com.example.shop" in text, text)
expect("digest: no 'walk (main)' label for a raw loop", "walk (main)" not in text and
       "raw loop (no tree): n=1, input 300,000 tok" in text, text)
expect("digest: older rows apart and labelled", "walk, delegated, whole session (older data): n=1, input 5,000,000" in text,
       text)
expect("digest: trend over device-only rows", "trend: median device input" in text and
       "device-work token use per session rose" in text, text)
expect("digest: cost from prices.json", "≈$" in text, text)
for needle in ("walk(s) of >5 steps", "checkout.ios.ad` step 4 keeps breaking", "fails outside the flow (DEVICE_NOT_FOUND)",
               "of taps by coordinates", "chaining guidance", "agents retry the same tap", "long plain-scroll loops",
               "`help wait` looked up 4×", "Save never enabled", "pending review", "UNSURE >30%", "'missing' fires",
               "Risky taps: Delete account", "Collector errors: 1", "unscaled"):
    expect(f"digest finds: {needle}", needle in text, needle)

# 8. stats main(): --json, --mark-reviewed, no events
out = run_main(st, "", ["stats", "--json", "--since", "30"])
expect("stats --json prints the raw events", len(json.loads(out)) == len(rows))
expect("stats --mark-reviewed", run_main(st, "", ["stats", "--mark-reviewed"]).strip() == "marked reviewed" and
       os.path.exists(st.REVIEW_MARK))
out = run_main(st, "", ["stats"])
expect("stats digest after review hides old notes", out.startswith("Sessions with device activity") and
       "pending review" not in out, out[:200])
expect("stats --all shows them again", "pending review" in run_main(st, "", ["stats", "--all"]))
use_dir(os.path.join(TMP, "metrics-empty"))
expect("stats with no events", run_main(st, "", ["stats", "--since", "7"]).startswith("No mobile-drive metrics"))
expect("digest of one plain session: no suggestions", "Suggested changes: none" in st.digest(
    [{"ts": now, "session": "q", "kind": "ad", "agent": "main", "verb": "snapshot"}], False))

shutil.rmtree(TMP, ignore_errors=True)
print("all metrics tests passed" if not failures else f"{len(failures)} metrics test(s) FAILED")
sys.exit(1 if failures else 0)
