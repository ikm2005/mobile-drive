#!/usr/bin/env python3
"""Tests for scripts/ui-audit.py and scripts/figma-spec.py on synthetic screens (no device).

Each defect is planted in a copy of one passing screen, and the test asserts that the right check
fires, and that the passing screen stays clean. Run by tools/check.sh.
"""
import contextlib
import copy
import glob
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tools", "fixtures")
STUBS = os.path.join(ROOT, "tools", "stubs")  # fake agent-device and adb: no device is ever touched


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ua = load("ui_audit", "ui-audit.py")
CFG = ua.load_config("/nonexistent")
IOS_INSETS = {"top": 47, "bottom": 34, "left": 0, "right": 0}
failures = []


def node(i, kind, rect, parent=0, **kw):
    x, y, w, h = rect
    return {"index": i, "parentIndex": parent, "kind": kind, "rect": {"x": x, "y": y, "width": w, "height": h},
            "ref": f"e{i + 1}", **kw}


def screen():
    """An 'Items' screen on a 390×844pt phone: title, Add button, a list of two rows with an icon, Save."""
    nodes = [
        {"index": 0, "kind": "other", "label": "Example", "rect": {"x": 0, "y": 0, "width": 390, "height": 844}, "ref": "e1"},
        node(1, "text", (16, 60, 200, 34), label="Items"),
        node(2, "button", (330, 52, 44, 44), identifier="item_add", label="Add", hittable=True),
        node(3, "collection", (0, 100, 390, 640), hittable=True),
        node(4, "cell", (0, 100, 390, 56), parent=3, label="Milk", hittable=True),
        node(5, "image", (20, 110, 36, 36), parent=4, identifier="item_icon"),
        node(6, "cell", (0, 156, 390, 56), parent=3, label="Bread", hittable=True),
        node(7, "button", (100, 760, 190, 44), label="Save", hittable=True),
    ]
    return {"data": {"nodes": nodes, "viewport": {"width": 390, "height": 844}, "appBundleId": "com.example.Items"}}


def by(raw, label=None, ident=None):
    return next(n for n in raw["data"]["nodes"] if (label and n.get("label") == label) or
                (ident and n.get("identifier") == ident))


def run(raw, spec, platform="ios", insets=IOS_INSETS, image=None, out=None):
    snap = ua.Snap(raw, platform)
    return ua.audit(spec, snap, insets, CFG, image, out)


def checks(findings, sev=None):
    return sorted({f["check"] for f in findings if sev is None or f["severity"] == sev})


def expect(name, cond, detail=""):
    print(("ok    " if cond else "FAIL  ") + "ui-audit: " + name + ("" if cond else f"  ({detail})"))
    if not cond:
        failures.append(name)


fs = load("figma_spec", "figma-spec.py")


def cli(mod, *argv, **env):
    """Run mod.main() in-process with this argv, the stubs first on PATH and `env` set (None unsets).

    Returns (exit code, stdout, stderr); any STUB_* variables from outside are cleared first."""
    saved_argv, saved_env = sys.argv, dict(os.environ)
    sys.argv = [mod.__name__] + [str(a) for a in argv]
    for k in [k for k in os.environ if k.startswith("STUB_")]:
        del os.environ[k]
    os.environ["PATH"] = STUBS + os.pathsep + saved_env.get("PATH", "")
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = mod.main()
            except SystemExit as e:
                rc = e.code
    finally:
        sys.argv = saved_argv
        os.environ.clear()
        os.environ.update(saved_env)
    return rc, out.getvalue(), err.getvalue()


REF = ua.spec_from_snapshot(ua.Snap(screen(), "ios"), IOS_INSETS, "items", "reference-app:ios", CFG)
keys = {e["key"] for e in REF["elements"]}
expect("capture keeps labelled and id'd elements", {"items", "item_add", "milk", "bread", "save", "item_icon"} <= keys, keys)
expect("capture marks images visual", any(e.get("visual") for e in REF["elements"] if e["key"] == "item_icon"))

f, eyes, matched = run(screen(), REF)
expect("identical screen passes", not checks(f, "FAIL") and matched == len(REF["elements"]), f)


def planted(name, mutate, want, sev="FAIL", platform="ios", insets=IOS_INSETS, spec=REF):
    raw = screen()
    mutate(raw)
    f, _, _ = run(raw, spec, platform, insets)
    got = checks(f, sev)
    expect(f"{name} → {sev} {want}", want in got, got)


planted("Add removed", lambda r: r["data"]["nodes"].remove(by(r, ident="item_add")), "missing")
planted("Save above the title", lambda r: (by(r, "Save")["rect"].update(y=50), by(r, "Items")["rect"].update(y=760)), "order")
planted("Save shifted right", lambda r: by(r, "Save")["rect"].update(x=160), "moved", "WARN")
planted("Save in the home-indicator area", lambda r: by(r, "Save")["rect"].update(y=800, height=40), "safe-area")
planted("title under the status bar", lambda r: by(r, "Items")["rect"].update(y=20), "safe-area")
planted("Save past the right edge", lambda r: by(r, "Save")["rect"].update(x=300), "off-screen")
planted("Save covered", lambda r: by(r, "Save").update(interactionBlocked="covered"), "covered")
planted("button on top of Add", lambda r: r["data"]["nodes"].append(node(8, "button", (334, 56, 40, 40), label="Edit",
                                                                         hittable=True)), "overlap")
planted("Add shrunk to 30pt", lambda r: by(r, ident="item_add")["rect"].update(width=30, height=30), "tap-size", "WARN")
planted("truncated row", lambda r: r["data"]["nodes"].append(node(8, "cell", (0, 212, 390, 56), parent=3,
                                                                  label="A very long item na…", hittable=True)),
        "truncated", "WARN")

# scroll content under a bar is a warning, not a failure, and covered scroll content is ignored
raw = screen()
by(raw, "Bread")["rect"].update(y=20)
f, _, _ = run(raw, {"elements": []})
expect("scroll content under the status bar is only a warning", "safe-area" in checks(f, "WARN") and
       "safe-area" not in checks(f, "FAIL"), f)

# Android: px units, density, insets from dumpsys, per-platform name map
ins = ua.parse_dumpsys_window(open(os.path.join(FIX, "dumpsys-window.txt")).read())
expect("dumpsys insets top/bottom", (ins["top"], ins["bottom"], ins["left"]) == (63, 63, 0), ins)
ime = open(os.path.join(FIX, "dumpsys-window.txt")).read().replace(
    "type=ime frame=[0,0][0,0] visible=false", "type=ime frame=[0,1500][1080,2400] visible=true")
expect("visible keyboard raises the bottom inset", ua.parse_dumpsys_window(ime)["bottom"] == 900)


def android(raw):
    """The same screen on a 1080×2400px phone at density 2.625, Add labelled differently."""
    sx, sy = 1080 / 390, (2400 - 126) / (844 - 81)
    for n in raw["data"]["nodes"]:
        r = n["rect"]
        if n["index"] == 0:
            r.update(width=1080, height=2400)
            continue
        r.update(x=r["x"] * sx, y=63 + (r["y"] - 47) * sy, width=r["width"] * sx, height=r["height"] * sy)
    by(raw, ident="item_add").update(label="Add item", identifier=None)
    raw["data"].update(viewport={"width": 1080, "height": 2400}, androidSnapshot={"pixelDensity": 2.625})
    return raw


spec_map = copy.deepcopy(REF)
spec_map["map"] = {"item_add": {"android": {"label": "Add item"}}}
f, _, matched = run(android(screen()), spec_map, "android", ins)
expect("Android with a name map matches every element", matched == len(REF["elements"]) and not checks(f, "FAIL"),
       (matched, f))
f, _, _ = run(android(screen()), REF, "android", ins)
expect("Android without the map reports Add missing", "missing" in checks(f, "FAIL"), f)
raw = android(screen())
by(raw, label="Add item")["rect"].update(height=100, width=100)  # 100px = 38dp
f, _, _ = run(raw, spec_map, "android", ins)
expect("Android tap size uses dp", any(x["check"] == "tap-size" and "38×38dp" in x["detail"] for x in f), f)

# visual items: identical pixels pass silently, different pixels go to eyes
with tempfile.TemporaryDirectory() as tmp:
    def solid(path, w, h, rgb):
        ua.png_write(path, w, h, 2, [bytes(rgb) * w for _ in range(h)])

    shot = os.path.join(tmp, "shot.png")
    solid(shot, 390, 844, (250, 250, 250))
    img = ua.Image(shot)
    snap = ua.Snap(screen(), "ios")
    spec = ua.spec_from_snapshot(snap, IOS_INSETS, "items", "reference-app:ios", CFG, img, os.path.join(tmp, "ref"))
    icon = next(e for e in spec["elements"] if e["key"] == "item_icon")
    expect("capture writes a reference crop", icon.get("crop") and os.path.exists(icon["crop"]), icon)
    _, eyes, _ = run(screen(), spec, image=img, out=os.path.join(tmp, "r1"))
    expect("unchanged icon needs no eyes", not eyes, eyes)
    solid(shot, 390, 844, (20, 90, 200))
    _, eyes, _ = run(screen(), spec, image=ua.Image(shot), out=os.path.join(tmp, "r2"))
    expect("changed icon goes to eyes with both crops", len(eyes) == 1 and eyes[0]["ref"] and eyes[0]["cur"] and
           os.path.exists(eyes[0]["cur"]), eyes)
    w, h, *_ = ua.png_read(eyes[0]["cur"]) if eyes else (0, 0)
    expect("crop has the element's size plus padding", (w, h) == (52, 52), (w, h))

    # the command line, end to end on saved files
    sp, snp, insp = (os.path.join(tmp, n) for n in ("spec.json", "snap.json", "insets.json"))
    json.dump(REF, open(sp, "w"))
    bad = screen()
    bad["data"]["nodes"].remove(by(bad, ident="item_add"))
    json.dump(bad, open(snp, "w"))
    json.dump(IOS_INSETS, open(insp, "w"))
    res = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "ui-audit.py"), "audit", "--spec", sp,
                          "--snapshot", snp, "--insets", insp, "--platform", "ios", "--state", tmp,
                          "--out", os.path.join(tmp, "r3")], capture_output=True, text=True)
    lines = res.stdout.strip().splitlines()
    expect("CLI exits 1 on FAIL with a short summary", res.returncode == 1 and len(lines) <= 15 and
           "FAIL missing" in res.stdout and os.path.exists(os.path.join(tmp, "r3", "report.json")), res.stdout + res.stderr)

    # Figma frame → spec (in-process, so it counts for coverage)
    out = os.path.join(tmp, "figma.spec.json")
    rc, fout, ferr = cli(fs, "items", "--from-json", os.path.join(FIX, "figma-nodes.json"), "--state", tmp,
                         "--out", out, FIGMA_TOKEN=None)
    fs_spec = json.load(open(out)) if os.path.exists(out) else {"elements": [], "insets": {}}
    fk = {e["key"] for e in fs_spec["elements"]}
    expect("figma: elements from text, ids and instances", {"items", "item_add", "item_icon", "save"} <= fk and
           "guide" not in fk and "plus" not in fk, (fk, ferr))
    expect("figma: insets from status bar and home indicator",
           (fs_spec["insets"].get("top"), fs_spec["insets"].get("bottom")) == (47, 34), fs_spec["insets"])
    expect("figma: one summary line naming the bars", rc == 0 and "insets 47/34 (status bar, home indicator)" in fout,
           (rc, fout))
    f, _, matched = run(screen(), fs_spec)
    expect("the Figma spec audits the matching screen clean", not checks(f, "FAIL"), f)
    rc, fout, _ = cli(fs, "items", "--from-json", os.path.join(FIX, "figma-nodes.json"), "--state", tmp, "--out", out)
    expect("figma: an existing spec is kept without --force", rc == 2 and "--force" in fout, (rc, fout))


# ---------------------------------------------------------------- ui-audit commands on the stub device

def dump(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh)
    return path


def read(path):
    with open(path) as fh:
        return fh.read()


def solid_png(path, w, h, rgb):
    ua.png_write(path, w, h, 2, [bytes(rgb) * w for _ in range(h)])
    return path


with tempfile.TemporaryDirectory() as tmp:
    start_cwd = os.getcwd()
    proj = os.path.join(tmp, "app")
    state = os.path.join(proj, ".claude", "mobile-drive")
    os.makedirs(os.path.join(proj, "src"))
    os.makedirs(state)
    dump(os.path.join(state, "ui-check.json"), {"ios_insets": IOS_INSETS})
    log = os.path.join(tmp, "stub.log")
    shot = solid_png(os.path.join(tmp, "shot.png"), 390, 844, (250, 250, 250))
    good = dump(os.path.join(tmp, "good.json"), screen())
    stub = {"STUB_LOG": log, "STUB_SNAPSHOT": good, "STUB_SCREENSHOT": shot}

    def device(*argv, **env):
        """ui-audit main() on the stub; the log is fresh for every call."""
        for p in (log,):
            if os.path.exists(p):
                os.remove(p)
        return cli(ua, *argv, **{**stub, **env})

    def logged():
        return read(log).splitlines() if os.path.exists(log) else []

    os.chdir(os.path.join(proj, "src"))
    try:
        # state folder: the nearest .claude/mobile-drive above the working directory, else the home one
        expect("state_dir finds the project's .claude/mobile-drive",
               os.path.realpath(ua.state_dir()) == os.path.realpath(state), ua.state_dir())
        expect("figma state_dir finds it too", os.path.realpath(fs.state_dir()) == os.path.realpath(state))
        os.makedirs(os.path.join(tmp, "elsewhere"))
        os.chdir(os.path.join(tmp, "elsewhere"))
        far = ua.state_dir()
        expect("state_dir falls back to the home state folder",
               far.endswith(os.path.join(".claude", "mobile-drive")) and
               not os.path.realpath(far).startswith(os.path.realpath(tmp)), far)
        expect("figma state_dir falls back the same way", fs.state_dir() == far, fs.state_dir())
        os.chdir(os.path.join(proj, "src"))
        spec_p = os.path.join(state, "screens", "items.spec.json")

        # capture: snapshot + screenshot from the stub, spec and reference crops into the state folder
        rc, out, err = device("capture", "items", "--platform", "ios", "--udid", "SIM-0001")
        expect("capture exits 0 and writes the spec", rc == 0 and os.path.exists(spec_p), (rc, out, err))
        expect("capture summary names the visual element and the config insets",
               "(1 visual) from reference-app:ios" in out and "insets 47/34 (ui-check.json ios_insets)" in out, out)
        expect("capture asks agent-device for a JSON snapshot of that simulator",
               "snapshot --json --platform ios --udid SIM-0001" in logged(), logged())
        expect("capture takes an unrecorded screenshot", any(
            x.startswith("screenshot ") and x.endswith(" --no-record --platform ios --udid SIM-0001")
            for x in logged()), logged())
        expect("capture writes the icon's reference crop",
               os.path.exists(os.path.join(state, "screens", "items", "ref", "item_icon.png")))
        rc, out, _ = device("capture", "items", "--platform", "ios")
        expect("capture keeps an existing spec without --force", rc == 2 and "--force" in out and
               not logged(), (rc, out))

        # check on the same screen passes
        r_ok = os.path.join(tmp, "r-ok")
        rc, out, err = device("check", "items", "--platform", "ios", "--out", r_ok)
        expect("check on the captured screen exits 0", rc == 0, (out, err))
        n_el = len(json.load(open(spec_p))["elements"])
        expect("check prints PASS with every element matched",
               out.startswith("ui-audit items ios: PASS — 0 fail") and f"matched {n_el}/{n_el}" in out, out)
        expect("check writes report.md and report.json", all(
            os.path.exists(os.path.join(r_ok, n)) for n in ("report.md", "report.json", "insets.json")))
        expect("an unchanged icon sends nothing to eyes", not os.path.exists(os.path.join(r_ok, "eyes.txt")))

        # check with planted defects, default report folder
        bad = screen()
        bad["data"]["nodes"].remove(by(bad, ident="item_add"))
        by(bad, "Save").update(interactionBlocked="covered")
        rc, out, err = device("check", "items", "--platform", "ios",
                              STUB_SNAPSHOT=dump(os.path.join(tmp, "bad.json"), bad))
        reports = glob.glob(os.path.join(state, "reports", "*-items-ios"))
        rep = reports[0] if reports else tmp
        expect("check with a missing and a covered element exits 1", rc == 1, (out, err))
        expect("check prints the FAIL lines", "FAIL missing" in out and "FAIL covered" in out, out)
        expect("check reports into <state>/reports/<time>-<screen>-<platform>",
               len(reports) == 1 and os.path.exists(os.path.join(rep, "report.md")), reports)
        md = read(os.path.join(rep, "report.md")) if os.path.exists(os.path.join(rep, "report.md")) else ""
        expect("report.md has the verdict and a row per finding",
               "— FAIL" in md and "| FAIL | missing |" in md and "| FAIL | covered |" in md, md)
        eyes_txt = os.path.join(rep, "eyes.txt")
        eyes = read(eyes_txt) if os.path.exists(eyes_txt) else ""
        expect("the covered Save goes to eyes with a current crop", eyes.startswith("save |") and
               os.path.exists(os.path.join(rep, "crops", "save.cur.png")), eyes)
        expect("the summary points at eyes.txt", "eyes: " + os.path.join(os.path.realpath(rep), "eyes.txt") in out, out)

        # screenshot fallbacks and capture errors
        rc, out, _ = device("check", "items", "--platform", "ios", "--out", os.path.join(tmp, "r-retry"),
                            STUB_SCREENSHOT_NO_RECORD="reject")
        shots = [x for x in logged() if x.startswith("screenshot ")]
        expect("screenshot is retried without --no-record", rc == 0 and len(shots) == 2 and
               "--no-record" in shots[0] and "--no-record" not in shots[1], shots)
        rc, out, _ = device("check", "items", "--platform", "ios", "--out", os.path.join(tmp, "r-noshot"),
                            STUB_SCREENSHOT_FAIL="1")
        expect("no screenshot: check still runs, the icon goes to eyes without a crop",
               rc == 0 and "1 for eyes" in out and "cur: -" in read(os.path.join(tmp, "r-noshot", "eyes.txt")), out)
        rc, out, err = device("check", "items", "--platform", "ios", "--out", os.path.join(tmp, "r-notpng"),
                              STUB_SCREENSHOT=good)
        expect("a screenshot that is not a PNG is noted, not fatal", rc == 0 and "not usable for crops" in err, err)
        rc, out, err = device("check", "items", "--platform", "ios", "--no-screenshot",
                              "--out", os.path.join(tmp, "r-noflag"))
        expect("--no-screenshot takes no screenshot", rc == 0 and
               not any(x.startswith("screenshot") for x in logged()), logged())
        rc, out, err = device("check", "items", "--platform", "ios", "--out", os.path.join(tmp, "r-x"),
                              STUB_SNAPSHOT_FAIL="1")
        expect("a failed snapshot exits 2 with the error", rc == 2 and
               "ui-audit error: agent-device snapshot failed: Error: no active session" in err, (rc, err))
        rc, out, err = device("check", "items", "--platform", "ios", "--out", os.path.join(tmp, "r-y"),
                              "--snapshot", dump(os.path.join(tmp, "notsnap.json"), {"ok": True}), "--no-screenshot")
        expect("a file that is not a snapshot exits 2", rc == 2 and "not an agent-device snapshot" in err and
               not logged(), (rc, err, logged()))

        # no spec: the rules for every screen still run
        r_none = os.path.join(tmp, "r-none")
        rc, out, err = device("check", "other", "--platform", "ios", "--no-screenshot", "--out", r_none)
        expect("check with no spec notes it and passes a clean screen", rc == 0 and "no spec at" in err and
               "matched 0/0" in out, (out, err))
        low = screen()
        by(low, "Save")["rect"].update(y=800, height=40)
        rc, out, _ = device("check", "other", "--platform", "ios", "--no-screenshot", "--out", r_none,
                            STUB_SNAPSHOT=dump(os.path.join(tmp, "low.json"), low))
        expect("check with no spec still fails Save in the home-indicator area",
               rc == 1 and "FAIL safe-area" in out, out)

        # approve: a baseline and the pixel gate
        rc, out, _ = device("approve", "items", "--platform", "ios", "--force")
        spec = json.load(open(spec_p))
        expect("approve writes a baseline spec with a baseline PNG", rc == 0 and spec["source"] == "baseline" and
               os.path.exists(spec.get("baseline_png", "")), (out, spec.get("source")))
        r_px = os.path.join(tmp, "r-px")
        rc, out, _ = device("check", "items", "--platform", "ios", "--out", r_px)
        expect("check against a baseline runs the pixel diff", any(
            x.startswith("diff screenshot --baseline " + spec.get("baseline_png", "?")) and "--json --out" in x
            for x in logged()), logged())
        expect("identical pixels: no pixels finding", rc == 0 and "pixels" not in out, out)
        # Only the pixel diff is over the limit (the icon is unchanged), so the whole-screen crop is the first
        # file written into <out>/crops. This used to exit 2: the folder was never created.
        r_px3 = os.path.join(tmp, "r-px3")
        rc, out, err = device("check", "items", "--platform", "ios", "--out", r_px3,
                              STUB_DIFF_JSON='{"success": true, "data": {"mismatchPercentage": 3.5}}')
        expect("pixel diff alone: the whole-screen crop is written", rc == 0 and "WARN pixels" in out and
               os.path.exists(os.path.join(r_px3, "crops", "screen.cur.png")), (rc, out, err))
        # Here the icon changed too, so its crop is written before the whole-screen one.
        r_px2 = os.path.join(tmp, "r-px2")
        rc, out, err = device("check", "items", "--platform", "ios", "--out", r_px2,
                              STUB_SCREENSHOT=solid_png(os.path.join(tmp, "blue.png"), 390, 844, (20, 90, 200)),
                              STUB_DIFF_JSON='{"success": true, "data": {"mismatchPercentage": 3.5}}')
        expect("3.5% different pixels → WARN pixels, still exit 0", rc == 0 and "WARN pixels" in out and
               "3.50% of pixels differ" in out, (out, err))
        eyes_px = read(os.path.join(r_px2, "eyes.txt")) if os.path.exists(os.path.join(r_px2, "eyes.txt")) else ""
        expect("the changed icon and the whole screen go to eyes",
               eyes_px.startswith("item_icon |") and "\nscreen |" in eyes_px and
               os.path.exists(os.path.join(r_px2, "crops", "screen.cur.png")), eyes_px)
        rc, out, _ = device("check", "items", "--platform", "ios", "--out", r_px,
                            STUB_DIFF_JSON='{"success": true, "data": {"dimensionMismatch": true}}')
        expect("a size mismatch is a WARN of its own", rc == 0 and "screenshot size differs" in out, out)
        rc, out, _ = device("check", "items", "--platform", "ios", "--out", r_px, STUB_DIFF_JSON="not json")
        expect("unreadable diff output is ignored", rc == 0 and "pixels" not in out, out)
        rc, out, _ = device("approve", "plain", "--platform", "ios", STUB_SCREENSHOT_FAIL="1")
        plain = json.load(open(os.path.join(state, "screens", "plain.spec.json")))
        expect("approve without a screenshot keeps no baseline PNG", rc == 0 and "baseline_png" not in plain, plain)

        # Android: serial, half-scale screenshot, insets from a saved dumpsys
        a_shot = solid_png(os.path.join(tmp, "a-shot.png"), 540, 1200, (250, 250, 250))
        r_and = os.path.join(tmp, "r-and")
        rc, out, err = device("check", "items", "--platform", "android", "--serial", "emulator-5554",
                              "--dumpsys", os.path.join(FIX, "dumpsys-window.txt"),
                              "--spec", dump(os.path.join(tmp, "map.spec.json"), spec_map), "--out", r_and,
                              STUB_SNAPSHOT=dump(os.path.join(tmp, "android.json"), android(screen())),
                              STUB_SCREENSHOT=a_shot)
        expect("Android check with a name map passes", rc == 0 and "android: PASS" in out, (out, err))
        expect("Android screenshot is half scale on the serial", any(
            x.startswith("screenshot ") and x.endswith("--no-record --scale 0.5 --platform android --serial "
                                                       "emulator-5554") for x in logged()), logged())
        rep_and = json.load(open(os.path.join(r_and, "report.json")))
        expect("Android insets come from dumpsys", rep_and["insets"]["source"] == "dumpsys window" and
               rep_and["insets"]["top"] == 63, rep_and["insets"])

        # audit on saved files, in-process
        sp = dump(os.path.join(tmp, "ref.spec.json"), REF)
        bad_p = os.path.join(tmp, "bad.json")
        r_aud = os.path.join(tmp, "r-aud")
        rc, out, _ = device("audit", "--spec", sp, "--snapshot", bad_p, "--platform", "ios", "--screenshot", shot,
                            "--insets", dump(os.path.join(tmp, "ins.json"), IOS_INSETS), "--out", r_aud)
        expect("audit on files exits 1 on FAIL and writes report.json", rc == 1 and "FAIL missing" in out and
               os.path.exists(os.path.join(r_aud, "report.json")) and not logged(), (out, logged()))
        rc, out, _ = device("audit", "--spec", sp, "--snapshot", good, "--platform", "ios")
        aud = glob.glob(os.path.join(state, "reports", "*-items"))
        rep_aud = json.load(open(os.path.join(aud[0], "report.json"))) if aud else {"insets": {}}
        expect("audit without --insets/--out uses the config insets and the state reports folder",
               rc == 0 and len(aud) == 1 and rep_aud["insets"].get("source") == "ui-check.json ios_insets",
               (out, aud))
        rc, out, err = device("audit", "--spec", sp, "--platform", "ios")
        expect("audit without --snapshot is a usage error", rc == 2 and "audit needs --spec and --snapshot" in err,
               (rc, err))
        many = screen()
        many["data"]["nodes"] += [node(8 + i, "text", (16, 300 + 22 * i, 200, 18), label=f"Row {i} has a long name…")
                                  for i in range(12)]
        rc, out, _ = device("audit", "--spec", dump(os.path.join(tmp, "none.spec.json"), {"screen": "long"}),
                            "--snapshot", dump(os.path.join(tmp, "many.json"), many), "--platform", "ios",
                            "--out", os.path.join(tmp, "r-many"))
        lines = out.strip().splitlines()
        expect("more than 10 findings: the summary stays short", rc == 0 and len(lines) <= 15 and
               "… 2 more in report.md" in out and out.count("WARN truncated") == 10, out)

        # insets
        rc, out, _ = device("insets", "--platform", "android", "--dumpsys", os.path.join(FIX, "dumpsys-window.txt"))
        ins_a = json.loads(out) if rc == 0 else {}
        expect("insets for Android from a saved dumpsys", (ins_a.get("top"), ins_a.get("bottom"), ins_a.get("unit")) ==
               (63, 63, "px"), out)
        rc, out, _ = device("insets", "--platform", "android", "--serial", "emulator-5554",
                            STUB_DUMPSYS=os.path.join(FIX, "dumpsys-window.txt"))
        expect("insets for Android through adb on the serial", rc == 0 and json.loads(out)["top"] == 63 and
               "adb -s emulator-5554 shell dumpsys window" in logged(), (out, logged()))
        rc, out, err = device("insets", "--platform", "android", STUB_ADB_FAIL="1")
        expect("a failing adb exits 2 with the error", rc == 2 and "adb dumpsys window failed: adb: no devices" in err,
               (rc, err))
        rc, out, _ = device("insets", "--platform", "ios")
        ins_i = json.loads(out)
        expect("insets for iOS come from ui-check.json", (ins_i["top"], ins_i["bottom"], ins_i["source"]) ==
               (47, 34, "ui-check.json ios_insets"), ins_i)
        empty_state = os.path.join(tmp, "empty-state")
        os.makedirs(empty_state)
        rc, out, _ = device("insets", "--platform", "ios", "--state", empty_state)
        ins_d = json.loads(out)
        expect("insets for iOS without config are the Face ID default", (ins_d["top"], ins_d["bottom"]) == (62, 34) and
               ins_d["source"].startswith("default"), ins_d)
        rc, out, _ = device("insets", "--platform", "ios", "--state", empty_state, "--config",
                            dump(os.path.join(tmp, "cfg.json"), {"ios_insets": {"top": 59}, "tolerance": 0.1}))
        ins_c = json.loads(out)
        expect("--config overrides one inset and keeps the rest", (ins_c["top"], ins_c["bottom"]) == (59, 34), ins_c)

        # ---------------------------------------------------------------- figma-spec without the network
        expect("figma slug", (fs.slug("Add item!"), fs.slug("!!!"), len(fs.slug("x" * 60))) == ("add_item", "item", 40))
        tree = {"children": [{"id": "a", "children": [{"id": "b"}, {"id": "c", "visible": False,
                                                                      "children": [{"id": "d"}]}]}, {"id": "e"}]}
        expect("figma descendants skip hidden layers and their children",
               [n["id"] for n in fs.descendants(tree)] == ["a", "b", "e"])
        resp = json.load(open(os.path.join(FIX, "figma-nodes.json")))
        expect("figma: an unknown --node falls back to the first frame",
               fs.frame_from_response(resp, "9:9")["id"] == "1:2")
        try:
            fs.frame_from_response({"nodes": {}})
            no_nodes = None
        except SystemExit as e:
            no_nodes = str(e)
        expect("figma: an empty response is an error", no_nodes == "no nodes in the Figma response", no_nodes)
        rc, out, err = cli(fs, "items", "--from-json", dump(os.path.join(tmp, "empty-nodes.json"), {"nodes": {}}),
                           "--out", os.path.join(tmp, "e.spec.json"), FIGMA_TOKEN=None)
        expect("figma: an empty saved response exits with the error", rc == "no nodes in the Figma response" and
               not os.path.exists(os.path.join(tmp, "e.spec.json")), rc)

        def bb(x, y, w, h):
            return {"absoluteBoundingBox": {"x": x, "y": y, "width": w, "height": h}}

        frame = {"id": "9:1", **bb(0, 0, 400, 800), "children": [
            {"id": "9:2", "name": "#title_id", "type": "TEXT", "characters": " Title ", **bb(20, 100, 100, 30)},
            {"id": "9:3", "name": "Gone", "type": "TEXT", "characters": "Gone", "visible": False, **bb(0, 0, 9, 9)},
            {"id": "9:4", "name": "Row", "type": "GROUP", **bb(0, 200, 400, 50), "children": [
                {"id": "9:5", "name": "Row text", "type": "TEXT", "characters": "Row text", **bb(60, 210, 200, 20)},
                {"id": "9:6", "name": "dot", "type": "ELLIPSE", **bb(20, 215, 10, 10)}]},
            {"id": "9:7", "name": "Card", "type": "FRAME", **bb(0, 300, 400, 100), "children": [
                {"id": "9:8", "name": "t1", "type": "TEXT", "characters": "Same", **bb(10, 310, 50, 20)},
                {"id": "9:9", "name": "t2", "type": "TEXT", "characters": "Same", **bb(10, 340, 50, 20)}]},
            {"id": "9:10", "name": "Avatar", "type": "RECTANGLE", **bb(300, 500, 40, 40)},
            {"id": "9:11", "name": "Spacer", "type": "RECTANGLE"}]}
        cfg_f = {"figma": {"insets": {"top": 40, "bottom": 20, "left": 0, "right": 0}}, "tolerance": 0.05}
        syn, vis_ids = fs.build_spec(frame, "synthetic", None, cfg_f)
        sk = [e["key"] for e in syn["elements"]]
        title = syn["elements"][0]
        expect("figma build_spec: #id text, walked GROUP/FRAME, visual by type and name, duplicate keys",
               sk == ["title_id", "row_text", "dot", "same", "same_2", "avatar"], sk)
        expect("figma build_spec: id and trimmed label", (title.get("id"), title.get("label")) == ("title_id", "Title"),
               title)
        expect("figma build_spec: rect_norm inside the configured insets",
               title["rect_norm"] == [0.05, round(60 / 740, 4), 0.25, round(30 / 740, 4)], title["rect_norm"])
        expect("figma build_spec: insets and tolerance from ui-check.json",
               syn["insets_from"] == "ui-check.json figma.insets" and syn["insets"]["top"] == 40 and
               syn["tolerance"] == 0.05 and syn["source"] == "figma:?/9:1", syn)
        expect("figma build_spec: visual layers keep their node ids for --images",
               vis_ids == {"dot": "9:6", "avatar": "9:10"}, vis_ids)
        syn2, _ = fs.build_spec(frame, "synthetic", "KEY", {})
        expect("figma build_spec: no bars and no config says so", syn2["insets_from"].startswith("none found") and
               syn2["insets"]["top"] == 0 and syn2["source"] == "figma:KEY/9:1", syn2["insets_from"])

        # default output path from the nearest state folder, --node with a dash
        rc, out, _ = cli(fs, "figma_items", "--from-json", os.path.join(FIX, "figma-nodes.json"), "--node", "1-2",
                         FIGMA_TOKEN=None)
        fi = os.path.join(state, "screens", "figma_items.spec.json")
        expect("figma: writes <state>/screens/<screen>.spec.json by default", rc == 0 and os.path.exists(fi) and
               json.load(open(fi))["source"] == "figma:?/1:2", (rc, out))
        rc, out, err = cli(fs, "nothing", "--state", empty_state, FIGMA_TOKEN=None)
        expect("figma: no --file/--node and no saved JSON is a usage error",
               rc == 2 and "--file and --node are needed" in err, (rc, err))
        rc, out, err = cli(fs, "nothing", "--state", empty_state, "--file", "FILEKEY", "--node", "1:2",
                           FIGMA_TOKEN=None)
        expect("figma: the API path without FIGMA_TOKEN stops before any request",
               isinstance(rc, str) and rc.startswith("FIGMA_TOKEN is not set"), rc)

        # the API path with urllib replaced by fakes: no request leaves the machine
        api_state = os.path.join(tmp, "api-state")
        os.makedirs(api_state)
        dump(os.path.join(api_state, "ui-check.json"), {"figma": {"file_key": "FILEKEY"}})
        calls = []
        dummy = "dummy-figma-token-for-tests"

        def fake_urlopen(req, timeout=None):
            calls.append((req.full_url, req.get_header("X-figma-token"), timeout))
            if "/nodes?" in req.full_url:
                return io.BytesIO(json.dumps(resp).encode())
            return io.BytesIO(json.dumps({"images": {"1:7": "https://figma-images.invalid/item_icon.png"}}).encode())

        def fake_retrieve(url, dst):
            calls.append((url, None, None))
            solid_png(dst, 4, 4, (1, 2, 3))
            return dst, None

        real = fs.urllib.request.urlopen, fs.urllib.request.urlretrieve
        fs.urllib.request.urlopen, fs.urllib.request.urlretrieve = fake_urlopen, fake_retrieve
        try:
            rc, out, err = cli(fs, "items", "--node", "1-2", "--images", "--state", api_state, FIGMA_TOKEN=dummy)
        finally:
            fs.urllib.request.urlopen, fs.urllib.request.urlretrieve = real
        urls = [c[0] for c in calls]
        api_spec_p = os.path.join(api_state, "screens", "items.spec.json")
        api_spec = json.load(open(api_spec_p)) if os.path.exists(api_spec_p) else {"elements": []}
        icon = next((e for e in api_spec["elements"] if e["key"] == "item_icon"), {})
        expect("figma API: nodes request for the file key from ui-check.json and the node id",
               rc == 0 and urls[:1] == ["https://api.figma.com/v1/files/FILEKEY/nodes?ids=1%3A2"], (rc, urls, err))
        expect("figma API: the token goes in the header only", calls and calls[0][1] == dummy and
               all(dummy not in u for u in urls) and dummy not in out + err, calls)
        expect("figma API: --images asks for the visual layer and saves its crop",
               any("/images/FILEKEY?ids=1%3A7&format=png" in u for u in urls) and
               icon.get("crop") == os.path.join(api_state, "screens", "items", "ref", "item_icon.png") and
               os.path.exists(icon.get("crop", "")), (urls, icon))
        expect("figma API: source names the file and frame", api_spec.get("source") == "figma:FILEKEY/1:2",
               api_spec.get("source"))
    finally:
        os.chdir(start_cwd)

print("all ui-audit tests passed" if not failures else f"{len(failures)} ui-audit test(s) FAILED")
sys.exit(1 if failures else 0)
