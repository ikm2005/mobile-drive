#!/usr/bin/env python3
"""Digest of the passively collected mobile-drive metrics. A pure script, no model and no tokens.
Every finding is tied to the change it suggests, so a retrospective starts from evidence.

  python3 mobile-drive-stats.py                  # last 30 days
  python3 mobile-drive-stats.py --since 90       # last 90 days
  python3 mobile-drive-stats.py --all            # ignore the last-review marker for runner notes
  python3 mobile-drive-stats.py --mark-reviewed  # after a retrospective: notes so far are handled
  python3 mobile-drive-stats.py --json           # raw aggregates

Data: ~/.claude/mobile-drive/metrics/*.jsonl (MOBILE_DRIVE_METRICS_DIR overrides), written by
scripts/mobile-drive-metrics.py. Optional prices for cost estimates:
~/.claude/mobile-drive/prices.json = {"<model id>": {"input": <$ per M tokens>, "output": <$ per M>}}
(input = blended rate across cache reads/writes; leave the file out to see tokens only).
"""
import argparse
import collections
import glob
import json
import os
import statistics
import time

DIR = os.path.expanduser(os.environ.get("MOBILE_DRIVE_METRICS_DIR", "~/.claude/mobile-drive/metrics"))
REVIEW_MARK = os.path.join(DIR, ".last-review")


def load(since_days):
    cutoff = time.time() - since_days * 86400
    events = []
    for f in sorted(glob.glob(os.path.join(DIR, "*.jsonl"))):
        with open(f) as fh:
            lines = fh.readlines()
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("ts", 0) >= cutoff:
                events.append(e)
    return events


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "–"


def digest(events, all_notes):
    by_session = collections.defaultdict(list)
    for e in events:
        by_session[e["session"]].append(e)
    out, actions = [], []

    def say(line=""):
        out.append(line)

    def act(text):
        actions.append(text)
        say(f"   → {text}")

    # sessions: what kind of task, delegated or not, which app
    kinds = collections.Counter()
    misses, app_of = 0, {}
    for sid, evs in by_session.items():
        apps = [e["app"] for e in evs if e.get("verb") == "open" and e.get("app")]
        app_of[sid] = apps[-1] if apps else (next((e.get("repo") for e in evs if e.get("repo")), "?"))
        main_actions = sum(1 for e in evs if e["kind"] == "ad" and e["agent"] == "main" and e.get("verb") in
                           ("press", "fill", "click", "scroll", "swipe", "type", "longpress"))
        replays = [e for e in evs if e["kind"] == "replay"]
        runner = any(e["kind"] == "runner" for e in evs)
        if replays and all(r["outcome"] == "PASS" for r in replays) and main_actions <= 3:
            kinds["replay (clean)"] += 1
        elif replays:
            kinds["replay + hand repair"] += 1
        elif runner:
            kinds["walk, delegated"] += 1
        elif main_actions:
            kinds["walk, main session"] += 1
            misses += main_actions > 5
    say(f"Sessions with device activity: {len(by_session)}  ·  " +
        "  ·  ".join(f"{k}: {v}" for k, v in kinds.most_common()))
    if misses:
        act(f"{misses} walk(s) of >5 steps ran in the main session instead of `mobile-drive:mobile-runner` — "
            "strengthen the delegation line in CLAUDE.md / SKILL.md")

    # replays
    flows = collections.defaultdict(lambda: {"runs": 0, "pass": 0, "steps": collections.Counter(),
                                             "dismissed": 0, "errors": collections.Counter()})
    for e in events:
        if e["kind"] == "replay" and e.get("flow"):
            f = flows[e["flow"]]
            f["runs"] += 1
            f["pass"] += e["outcome"] == "PASS"
            f["dismissed"] += bool(e.get("dismissed"))
            if e.get("step"):
                f["steps"][(e["step"], e.get("step_cmd") or "")] += 1
            elif e["outcome"] != "PASS":
                f["errors"][e.get("error") or "unknown"] += 1
    if flows:
        say("\nReplays (flow: pass/runs, popups dismissed)")
        for name, f in sorted(flows.items(), key=lambda kv: kv[1]["pass"] / kv[1]["runs"]):
            say(f"  {name}: {f['pass']}/{f['runs']}" + (f", popups {f['dismissed']}" if f["dismissed"] else ""))
            for (step, cmd), n in f["steps"].most_common(2):
                say(f"    diverged {n}× at step {step}: {cmd[:90]}")
                if n >= 2:
                    act(f"`{name}` step {step} keeps breaking — give that element an id in the app "
                        "(testTag / accessibilityIdentifier) and re-record, or harden the step")
            for err, n in f["errors"].most_common(1):
                say(f"    failed {n}× before any step: {err}")
                act(f"`{name}` fails outside the flow ({err}) — check replay.sh / device state")

    # fallbacks and selector mix per app
    sel = collections.defaultdict(collections.Counter)
    fb = collections.defaultdict(collections.Counter)
    fb_sessions = set()
    for e in events:
        app = app_of.get(e["session"], "?")
        if e["kind"] == "ad" and e.get("selector") and e.get("verb") not in ("scroll",):
            sel[app][e["selector"]] += 1
        if e["kind"] == "fallback":
            fb[app][e["type"] + ("" if e["type"] != "ad_screenshot" or e.get("scaled") else " (unscaled)")] += 1
            if e["type"].startswith("raw"):
                fb_sessions.add(e["session"])
    if sel or fb:
        say("\nTargeting per app (selector mix · fallbacks)")
        for app in sorted(set(sel) | set(fb)):
            s, total = sel[app], sum(sel[app].values())
            mix = ", ".join(f"{k} {pct(v, total)}" for k, v in s.most_common())
            fbs = ", ".join(f"{k} {v}" for k, v in fb[app].most_common())
            say(f"  {app}: {mix or '–'}" + (f"  ·  fallbacks: {fbs}" if fbs else ""))
            if total and s["coord"] / total > 0.10:
                act(f"{app}: {pct(s['coord'], total)} of taps by coordinates — the tree lacks those elements; "
                    "add ids/labels in the app, or note the screens in app-facts.md")
    if fb_sessions:
        act(f"{len(fb_sessions)} session(s) used raw screenshots/taps — check whether the tree was tried "
            "first (fine as a fallback on canvas/visual screens)")

    # batching, retries, help
    calls = [e["ad_commands"] for e in events if e["kind"] == "call"]
    repeats = [e for e in events if e["kind"] == "ad" and e.get("repeat") and e.get("verb") != "scroll"]
    scroll_loops = [e for e in events if e["kind"] == "ad" and e.get("repeat") and e.get("verb") == "scroll"]
    helps = collections.Counter(e.get("topic") or "" for e in events if e.get("verb") == "help")
    if calls:
        single = sum(1 for c in calls if c == 1)
        say(f"\nBatching: {len(calls)} Bash calls, {sum(calls)} agent-device commands, "
            f"{statistics.mean(calls):.1f} per call, single-command calls {pct(single, len(calls))}")
        if single / len(calls) > 0.6:
            act("most calls carry one command — the chaining guidance (`&&`, `--settle`) isn't landing")
    if repeats:
        top = collections.Counter((e.get("verb"), e.get("label") or e.get("ref") or "(coords)") for e in repeats)
        say(f"Same action on the same target twice in a row: {len(repeats)} — top: " +
            ", ".join(f"{v} {l!s:.30} ×{n}" for (v, l), n in top.most_common(3)))
        if len(repeats) / max(len(by_session), 1) > 2:
            act("agents retry the same tap — sharpen 'no change, no retry', or record the quirk "
                "(disabled button, keyboard covering it) in app-facts.md")
    if scroll_loops:
        per = collections.Counter(e["session"] for e in scroll_loops)
        worst = per.most_common(1)[0][1]
        say(f"Plain scroll repeats (no --until): {len(scroll_loops)}, worst session {worst}")
        if worst > 10:
            act("long plain-scroll loops (counting or auditing a list?) — remind 'verify your own action, "
                "not the whole list' and 'scroll --until <selector>'")
    if helps:
        say("help lookups: " + ", ".join(f"{t or '(index)'} ×{n}" for t, n in helps.most_common(5)))
        for t, n in helps.items():
            if n >= 3:
                act(f"`help {t}` looked up {n}× — put the answer in SKILL.md")

    # runner
    runner = [e for e in events if e["kind"] == "runner"]
    if runner:
        fails = [e for e in runner if e["status"] != "PASS"]
        say(f"\nmobile-runner: {len(runner)} reports, {len(fails)} not PASS")
        for e in fails[:3]:
            say(f"  FAIL: {(e.get('reason') or '')[:110]}")
        since = 0 if all_notes or not os.path.exists(REVIEW_MARK) else float(open(REVIEW_MARK).read() or 0)
        notes = [(app_of.get(e["session"], "?"), n) for e in runner if e["ts"] > since for n in e.get("notes", [])]
        if notes:
            say(f"  {len(notes)} note(s) pending review for app-facts.md (newest first, up to 12):")
            for app, n in notes[::-1][:12]:
                say(f"   · [{app}] {n[:150]}")
            act("review the pending runner notes; add the durable ones to app-facts.md")

    # screen checks (ui-check)
    audits = [e for e in events if e["kind"] == "ui_audit"]
    if audits:
        fails = sum(e["verdict"] == "FAIL" for e in audits)
        checks = collections.Counter()
        for e in audits:
            checks.update(e.get("checks", {}))
        eyes = sum(e.get("eyes", 0) for e in audits)
        say(f"\nScreen checks: {len(audits)} runs, {fails} FAIL · findings: " +
            (", ".join(f"{k} ×{v}" for k, v in checks.most_common(6)) or "none") + f" · {eyes} crop(s) sent for eyes")
        insp = [e for e in events if e["kind"] == "inspector"]
        if insp:
            tot = collections.Counter()
            for e in insp:
                tot.update({k: e.get(k, 0) for k in ("pass", "fail", "unsure")})
            say(f"  ui-inspector: {tot['pass']} PASS, {tot['fail']} FAIL, {tot['unsure']} UNSURE")
            if sum(tot.values()) >= 10 and tot["unsure"] > 0.3 * sum(tot.values()):
                act("ui-inspector answers UNSURE >30% of the time — crops may be too small; raise the crop "
                    "padding or screenshot scale in ui-audit.py")
        missing = checks.get("missing", 0)
        if missing and missing >= len(audits):
            act("'missing' fires on most screen checks — add `map` entries to the specs or ids/test tags in the app")

    # risky
    risky = collections.Counter(e.get("label") for e in events if e.get("risky"))
    if risky:
        say("\nRisky taps: " + ", ".join(f"{l} ×{n}" for l, n in risky.most_common(5)))
        act("confirm each risky tap was asked for; if not, tighten the safety rules")

    # cost
    prices = {}
    pf = os.path.expanduser("~/.claude/mobile-drive/prices.json")
    if os.path.exists(pf):
        prices = json.load(open(pf))
    sess = [e for e in events if e["kind"] == "session"]
    if sess:
        say("\nTokens per session (median · peak context · turns)")
        groups = collections.defaultdict(list)
        for e in sess:
            evs = by_session[e["session"]]
            kind = ("replay" if any(x["kind"] == "replay" for x in evs) else
                    "walk (delegated)" if any(x["kind"] == "runner" for x in evs) else "walk (main)")
            groups[kind].append(e)
        for kind, es in sorted(groups.items()):
            totals = [sum(m["input"] for m in e["by_model"].values()) for e in es]
            cost = [sum(m["input"] / 1e6 * prices.get(k, {}).get("input", 0) +
                        m["output"] / 1e6 * prices.get(k, {}).get("output", 0)
                        for k, m in e["by_model"].items()) for e in es]
            say(f"  {kind}: n={len(es)}, input {statistics.median(totals):,.0f} tok, "
                f"peak {statistics.median(e['peak_context'] for e in es):,.0f}, "
                f"turns {statistics.median(e['turns'] for e in es):.0f}"
                + (f", ≈${statistics.median(cost):.2f}" if prices else ""))
        if len(sess) >= 6:
            half = len(sess) // 2
            ordered = sorted(sess, key=lambda e: e["ts"])
            a = statistics.median(sum(m["input"] for m in e["by_model"].values()) for e in ordered[:half])
            b = statistics.median(sum(m["input"] for m in e["by_model"].values()) for e in ordered[half:])
            say(f"  trend: median input {a:,.0f} → {b:,.0f} tok (older half → newer half)")
            if b > a * 1.25:
                act("token use per session rose >25% — check what changed (plugin/agent-device upgrade, app release)")

    errlog = os.path.join(DIR, "errors.log")
    if os.path.exists(errlog):
        with open(errlog) as fh:
            errs = fh.read().splitlines()
        if errs:
            say(f"\nCollector errors: {len(errs)} (last: {errs[-1][:120]})")
            act("the metrics hook is failing on some events — fix scripts/mobile-drive-metrics.py, then clear errors.log")
    say("\nSuggested changes" + (":" if actions else ": none — keep collecting"))
    for i, a in enumerate(dict.fromkeys(actions), 1):
        say(f"  {i}. {a}")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", type=int, default=30, help="days to include (default 30)")
    p.add_argument("--all", action="store_true", help="show all runner notes, not only unreviewed ones")
    p.add_argument("--json", action="store_true", help="print the raw events instead of the digest")
    p.add_argument("--mark-reviewed", action="store_true", help="mark runner notes so far as reviewed")
    a = p.parse_args()
    if a.mark_reviewed:
        os.makedirs(DIR, exist_ok=True)
        open(REVIEW_MARK, "w").write(str(time.time()))
        print("marked reviewed")
        return
    events = load(a.since)
    if not events:
        print(f"No mobile-drive metrics in the last {a.since} days ({DIR}).")
        return
    print(json.dumps(events, indent=1) if a.json else digest(events, a.all))


if __name__ == "__main__":
    main()
