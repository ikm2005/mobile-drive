#!/usr/bin/env python3
"""Tests for scripts/ui-audit.py and scripts/figma-spec.py on synthetic screens (no device).

Each defect is planted in a copy of one passing screen, and the test asserts that the right check
fires, and that the passing screen stays clean. Run by tools/check.sh.
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tools", "fixtures")


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

    # Figma frame → spec
    out = os.path.join(tmp, "figma.spec.json")
    res = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "figma-spec.py"), "items", "--from-json",
                          os.path.join(FIX, "figma-nodes.json"), "--state", tmp, "--out", out],
                         capture_output=True, text=True, env={**os.environ, "FIGMA_TOKEN": ""})
    fs = json.load(open(out)) if os.path.exists(out) else {"elements": [], "insets": {}}
    fk = {e["key"] for e in fs["elements"]}
    expect("figma: elements from text, ids and instances", {"items", "item_add", "item_icon", "save"} <= fk and
           "guide" not in fk and "plus" not in fk, (fk, res.stderr))
    expect("figma: insets from status bar and home indicator", (fs["insets"].get("top"), fs["insets"].get("bottom")) ==
           (47, 34), fs["insets"])
    f, _, matched = run(screen(), fs)
    expect("the Figma spec audits the matching screen clean", not checks(f, "FAIL"), f)

print("all ui-audit tests passed" if not failures else f"{len(failures)} ui-audit test(s) FAILED")
sys.exit(1 if failures else 0)
