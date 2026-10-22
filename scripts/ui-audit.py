#!/usr/bin/env python3
"""Tier-0 screen check for the mobile-drive `ui-check` skill. A pure script: no model, no tokens.

It compares the screen on the device with a screen spec (made from a reference app, a Figma frame,
an approved baseline or by hand) and with rules that hold for every screen: the safe area, nothing
off-screen, covered or overlapping, tap-target size, truncated text. It prints a summary of at most
15 lines and writes the details to a report folder. Items only eyes can judge (icons, colours,
images, and geometry failures worth a second look) become small crop pairs in `eyes.txt` for the
Haiku `ui-inspector` agent, so no full screenshot ever has to reach a model.

  ui-audit.py check   <screen> --platform android|ios [--serial S | --udid U]
                      capture the current screen with agent-device, then audit it
  ui-audit.py capture <screen> --platform ios [--udid U] [--force]
                      make <state>/screens/<screen>.spec.json from the screen shown now
                      (the reference-app source)
  ui-audit.py approve <screen> --platform P [...] [--force]
                      the same, kept as an approved baseline (adds a pixel-diff gate)
  ui-audit.py audit   --spec F --snapshot F --platform P [--screenshot F] [--insets F] [--out DIR]
                      offline audit of saved files (used by the tests)
  ui-audit.py insets  --platform android|ios [--serial S] [--dumpsys F]
                      print the screen's system-bar insets as JSON

`check`, `capture` and `approve` accept `--snapshot/--screenshot/--insets` files instead of
capturing. agent-device sessions are keyed on the working directory: run this from the same
directory as the walk that reached the screen.

Exit codes: 0 = no FAIL, 1 = at least one FAIL, 2 = usage or capture error.
"""
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib

CONTAINER_KINDS = {"other", "group", "scroll-area", "list", "collection", "composeview", "viewfactoryholder",
                   "window", "application", "cardview", "navigation-bar", "toolbar", "tab-bar", "webview"}
SCROLL_KINDS = {"scroll-area", "list", "collection", "scroll-view", "table"}
VISUAL_KINDS = {"image", "icon"}
# the thing a finger lands on; text and images inside it are not separate targets
NOT_TARGET_KINDS = (CONTAINER_KINDS - {"group", "cardview"}) | {"text", "image", "icon", "static-text"}
DEFAULTS = {"tolerance": 0.04, "placement": "warn", "min_target": {"ios": 44, "android": 48},
            "ios_insets": {"top": 62, "bottom": 34, "left": 0, "right": 0}, "max_crops": 8,
            "pixel_mismatch_max": 0.5, "eyes_on_fail": True}
SEV_ORDER = {"FAIL": 0, "WARN": 1, "INFO": 2}
TRANSIENT = re.compile(r"scroll bar|scroll indicator", re.I)


# ---------------------------------------------------------------- state and config

def state_dir():
    d = os.getcwd()
    while True:
        cand = os.path.join(d, ".claude", "mobile-drive")
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.expanduser("~/.claude/mobile-drive")
        d = parent


def load_config(state, path=None):
    cfg = json.loads(json.dumps(DEFAULTS))
    path = path or os.path.join(state, "ui-check.json")
    if os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        for k, v in user.items():
            cfg[k] = {**cfg[k], **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
        cfg["_user"] = user
    return cfg


# ---------------------------------------------------------------- snapshot model

class Snap:
    """An agent-device `snapshot --json` capture. Units: px on Android, pt on iOS."""

    def __init__(self, raw, platform):
        data = raw.get("data", raw)
        if "nodes" not in data:
            raise ValueError("not an agent-device snapshot (no nodes): " + json.dumps(raw)[:200])
        self.platform = platform
        self.nodes = data["nodes"]
        self.app = data.get("appBundleId") or data.get("appName")
        vp = data.get("viewport") or {}
        root = self.nodes[0]["rect"] if self.nodes and self.nodes[0].get("rect") else {}
        self.W = vp.get("width") or root.get("width") or 0
        self.H = vp.get("height") or root.get("height") or 0
        self.density = (data.get("androidSnapshot") or {}).get("pixelDensity") or 1.0
        self.by_index = {n.get("index", i): n for i, n in enumerate(self.nodes)}

    def to_pt(self, v):  # px → dp on Android; identity on iOS
        return v / self.density if self.platform == "android" else v

    def ancestors(self, n):
        seen = set()
        while n.get("parentIndex") is not None and n["parentIndex"] not in seen:
            seen.add(n["parentIndex"])
            n = self.by_index.get(n["parentIndex"])
            if n is None:
                return
            yield n

    def is_system(self, n):
        if n.get("bundleId") and self.app and n["bundleId"] != self.app:
            return True
        ident = n.get("identifier") or ""
        return ident.startswith("com.android.systemui") or n.get("kind") in ("status-bar", "system-bar")

    def scroll_parent(self, n):
        for a in self.ancestors(n):
            if a.get("kind") in SCROLL_KINDS or a.get("hiddenContentBelow") or a.get("hiddenContentAbove"):
                return a
        return None

    def text(self, n):
        return (n.get("label") or n.get("value") or "").strip()

    def content(self):
        """Nodes a person reads or taps, without system UI or pure layout containers."""
        out = []
        for n in self.nodes:
            if not n.get("rect") or self.is_system(n):
                continue
            if n.get("kind") in CONTAINER_KINDS:
                continue
            if self.text(n) or n.get("hittable") or (n.get("kind") in VISUAL_KINDS and n.get("identifier")):
                out.append(n)
        return out


def rect_t(n):
    r = n["rect"]
    return r["x"], r["y"], r["width"], r["height"]


def inter(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return (w, h) if w > 0 and h > 0 else (0, 0)


def inside(a, b, slack=1.0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax >= bx - slack and ay >= by - slack and ax + aw <= bx + bw + slack and ay + ah <= by + bh + slack


def content_box(W, H, ins):
    return (ins.get("left", 0), ins.get("top", 0), W - ins.get("left", 0) - ins.get("right", 0),
            H - ins.get("top", 0) - ins.get("bottom", 0))


def norm(rect, box):
    x, y, w, h = rect
    bx, by, bw, bh = box
    return [round((x - bx) / bw, 4), round((y - by) / bh, 4), round(w / bw, 4), round(h / bh, 4)]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "item"


def short_id(ident):
    return ident.split(":id/")[-1] if ident else ident


def label_of(n):
    t = (n.get("label") or n.get("value") or "").strip()
    return f'"{t[:40]}"' if t else (f"id={short_id(n.get('identifier'))}" if n.get("identifier") else n.get("ref", "?"))


# ---------------------------------------------------------------- insets

INSET_LINE = re.compile(r"InsetsSource id=\S+ type=(\w+) frame=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\] visible=(\w+)")
CUTOUT = re.compile(r"mDisplayCutout=DisplayCutout\{insets=Rect\((-?\d+), (-?\d+) - (-?\d+), (-?\d+)\)")


def parse_dumpsys_window(text, W=None, H=None):
    """Insets in px from `adb shell dumpsys window`: status/navigation bars, display cutout, IME."""
    ins = {"top": 0, "bottom": 0, "left": 0, "right": 0, "ime": 0}
    frames = []
    for m in INSET_LINE.finditer(text):
        kind, l, t, r, b, vis = m.group(1), *map(int, m.group(2, 3, 4, 5)), m.group(6) == "true"
        if vis and r > l and b > t and kind in ("statusBars", "navigationBars", "ime"):
            frames.append((kind, l, t, r, b))
    W = W or max([f[3] for f in frames] + [0])
    H = H or max([f[4] for f in frames] + [0])
    for kind, l, t, r, b in frames:
        if kind == "ime":
            ins["ime"] = max(ins["ime"], b - t)
        elif t <= 0 and r - l >= W * 0.5:
            ins["top"] = max(ins["top"], b)
        elif b >= H and r - l >= W * 0.5:
            ins["bottom"] = max(ins["bottom"], H - t)
        elif l <= 0:
            ins["left"] = max(ins["left"], r)
        else:
            ins["right"] = max(ins["right"], W - l)
    c = CUTOUT.search(text)
    if c:
        l, t, r, b = map(int, c.groups())
        ins["left"], ins["top"] = max(ins["left"], l), max(ins["top"], t)
        ins["right"], ins["bottom"] = max(ins["right"], r), max(ins["bottom"], b)
    ins["bottom"] = max(ins["bottom"], ins["ime"])
    ins.update(unit="px", source="dumpsys window")
    return ins


def get_insets(platform, cfg, serial=None, dumpsys_file=None):
    if platform == "android":
        if dumpsys_file:
            with open(dumpsys_file) as f:
                return parse_dumpsys_window(f.read())
        cmd = ["adb"] + (["-s", serial] if serial else []) + ["shell", "dumpsys", "window"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode:
            raise RuntimeError("adb dumpsys window failed: " + out.stderr.strip()[:200])
        return parse_dumpsys_window(out.stdout)
    ins = dict(cfg["ios_insets"])
    ins.update(unit="pt", source="ui-check.json ios_insets" if "ios_insets" in cfg.get("_user", {}) else
               "default for Face ID iPhones in portrait; set ios_insets in ui-check.json for other models")
    return ins


# ---------------------------------------------------------------- PNG crop (stdlib only)

def png_read(path):
    with open(path, "rb") as f:
        b = f.read()
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, hdr = 8, [], None
    while pos < len(b):
        ln, typ = struct.unpack(">I4s", b[pos:pos + 8])
        chunk = b[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", chunk)
        elif typ == b"IDAT":
            idat.append(chunk)
        pos += 12 + ln
    w, h, depth, ctype, _, _, interlace = hdr
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
    if depth != 8 or bpp is None or interlace:
        raise ValueError(f"unsupported PNG (depth {depth}, colour type {ctype}, interlace {interlace})")
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    for y in range(h):
        ft = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            line = bytearray((a + c) & 255 for a, c in zip(line, prev))
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                p = a + prev[i] - c
                pa, pb, pc = abs(p - a), abs(p - prev[i]), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else prev[i] if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    return w, h, ctype, bpp, rows


def png_write(path, w, h, ctype, rows):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = b"".join(b"\x00" + bytes(r) for r in rows)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(data, 6)) + chunk(b"IEND", b""))


class Image:
    def __init__(self, path):
        self.w, self.h, self.ctype, self.bpp, self.rows = png_read(path)

    def crop(self, dst, x, y, w, h, max_side=400):
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(self.w, int(x + w + 0.5)), min(self.h, int(y + h + 0.5))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        step = max(1, -(-max(x1 - x0, y1 - y0) // max_side))  # nearest-neighbour downscale of big crops
        rows = []
        for yy in range(y0, y1, step):
            r = self.rows[yy]
            rows.append(b"".join(r[xx * self.bpp:(xx + 1) * self.bpp] for xx in range(x0, x1, step)))
        png_write(dst, len(range(x0, x1, step)), len(rows), self.ctype, rows)
        return dst


# ---------------------------------------------------------------- spec from a snapshot

def spec_from_snapshot(snap, insets, screen, source, cfg, image=None, crop_dir=None):
    box = content_box(snap.W, snap.H, insets)
    elements, keys, seen = [], set(), set()
    for n in snap.content():
        if not (snap.text(n) or n.get("identifier")) or TRANSIENT.search(snap.text(n)):
            continue  # nothing to match it by next time, or it comes and goes (scroll indicators)
        sig = (snap.text(n), tuple(round(v) for v in rect_t(n)))
        if sig in seen:  # nested duplicates (a cell and its button) carry the same text and frame
            continue
        seen.add(sig)
        ident = short_id(n.get("identifier"))
        if ident and ident == snap.text(n):
            ident = None  # iOS often mirrors the label into the identifier; it is not a stable id
        key = slug(ident or snap.text(n) or n.get("kind", "item"))
        base, i = key, 2
        while key in keys:
            key, i = f"{base}_{i}", i + 1
        keys.add(key)
        el = {"key": key, "kind": n.get("kind"), "rect_norm": norm(rect_t(n), box), "required": True}
        if ident:
            el["id"] = ident
        if snap.text(n):
            el["label"] = snap.text(n)
        if n.get("kind") in VISUAL_KINDS:
            el["visual"] = True
            if image and crop_dir:
                el["crop"] = crop_rect(image, snap, rect_t(n), os.path.join(crop_dir, key + ".png"))
        elements.append(el)
    elements.sort(key=lambda e: (e["rect_norm"][1], e["rect_norm"][0]))
    return {"screen": screen, "source": source, "platform": snap.platform, "captured": time.strftime("%Y-%m-%d"),
            "viewport": {"width": snap.W, "height": snap.H, "unit": "px" if snap.platform == "android" else "pt"},
            "insets": {k: insets.get(k, 0) for k in ("top", "bottom", "left", "right")},
            "tolerance": cfg["tolerance"], "placement": cfg["placement"], "elements": elements, "map": {}}


def same_pixels(ref_path, image, snap, n, limit=0.03):
    """True when the reference crop and the same region now are near-identical: no need for eyes."""
    if not (ref_path and image and os.path.exists(ref_path)):
        return False
    try:
        rw, rh, _, rbpp, rrows = png_read(ref_path)
        tmp = os.path.join(os.path.dirname(ref_path), ".cmp.png")
        if not crop_rect(image, snap, rect_t(n), tmp):
            return False
        cw, ch, _, cbpp, crows = png_read(tmp)
        os.remove(tmp)
    except Exception:
        return False
    if abs(cw - rw) > 2 or abs(ch - rh) > 2:
        return False  # different size: a different rendering, let a model look
    total = count = 0
    for y in range(0, min(rh, ch), 2):
        for x in range(0, min(rw, cw), 2):
            for c in range(3 if min(rbpp, cbpp) >= 3 else 1):
                total += abs(rrows[y][x * rbpp + c] - crows[y][x * cbpp + c])
                count += 1
    return count > 0 and total / count / 255 <= limit


def crop_rect(image, snap, rect, dst, pad=8):
    s = image.w / snap.W if snap.W else 1.0
    x, y, w, h = rect
    p = pad * (snap.density if snap.platform == "android" else 1)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        return image.crop(dst, (x - p) * s, (y - p) * s, (w + 2 * p) * s, (h + 2 * p) * s)
    except Exception:
        return None


# ---------------------------------------------------------------- audit

def match(spec, snap, insets):
    """Pair spec elements with current nodes: id, then label, then the spec's per-platform map."""
    box = content_box(snap.W, snap.H, insets)
    pool = [n for n in snap.content()]
    used, pairs = set(), {}

    def nearest(cands, el):
        rn = el["rect_norm"]
        return min(cands, key=lambda n: sum(abs(a - b) for a, b in zip(norm(rect_t(n), box)[:2], rn[:2])))

    for el in spec["elements"]:
        alt = (spec.get("map", {}).get(el["key"], {}) or {}).get(snap.platform, {})
        tries = [("id", alt.get("id")), ("label", alt.get("label")), ("id", el.get("id")), ("label", el.get("label"))]
        for how, want in tries:
            if not want:
                continue
            want_l = want.lower()
            cands = [n for n in pool if id(n) not in used and (
                (how == "id" and short_id(n.get("identifier") or "").lower() == want_l) or
                (how == "label" and snap.text(n).lower() == want_l))]
            if cands:
                n = nearest(cands, el)
                used.add(id(n))
                pairs[el["key"]] = n
                break
    return pairs


def audit(spec, snap, insets, cfg, image=None, out_dir=None, baseline_png=None, screenshot=None):
    findings, eyes = [], []
    box = content_box(snap.W, snap.H, insets)
    tol = spec.get("tolerance", cfg["tolerance"])
    place_sev = "FAIL" if spec.get("placement", cfg["placement"]) == "fail" else "WARN"
    min_t = cfg["min_target"][snap.platform]
    unit = "dp" if snap.platform == "android" else "pt"
    crops = os.path.join(out_dir, "crops") if out_dir else None
    edge_ok = {e["key"] for e in spec.get("elements", []) if e.get("edge_to_edge")}

    def add(sev, check, n, detail, key=None):
        findings.append({"severity": sev, "check": check, "key": key, "ref": n.get("ref") if n else None,
                         "what": label_of(n) if n else key, "rect": list(rect_t(n)) if n else None,
                         "detail": detail})

    # 1. the spec: presence, order, placement, visual items
    pairs = match(spec, snap, insets) if spec.get("elements") else {}
    node_key = {id(n): k for k, n in pairs.items()}
    for el in spec.get("elements", []):
        if el["key"] not in pairs:
            if el.get("required", True):
                want = " / ".join(x for x in (f"id={el['id']}" if el.get("id") else "",
                                              f'"{el["label"]}"' if el.get("label") else "") if x)
                add("FAIL", "missing", None, f"{want or el['key']} not on screen", el["key"])
            continue
        n = pairs[el["key"]]
        cur = norm(rect_t(n), box)
        ref = el["rect_norm"]
        dx, dy = cur[0] + cur[2] / 2 - ref[0] - ref[2] / 2, cur[1] + cur[3] / 2 - ref[1] - ref[3] / 2
        if abs(dx) > tol or abs(dy) > tol:
            add(place_sev, "moved", n, f"centre off by {dx:+.0%} x, {dy:+.0%} y of the safe area (tolerance "
                f"{tol:.0%})", el["key"])
        elif abs(cur[2] - ref[2]) > 2 * tol or abs(cur[3] - ref[3]) > 2 * tol:
            add(place_sev, "resized", n, f"size {cur[2]:.0%}×{cur[3]:.0%} vs {ref[2]:.0%}×{ref[3]:.0%} of the safe "
                f"area", el["key"])
        if el.get("visual") and not same_pixels(el.get("crop"), image, snap, n):
            eyes.append({"item": el["key"], "question": f"Does the current {el.get('kind') or 'element'} show the same "
                         f"icon/image, colour and orientation as the reference?", "ref": el.get("crop"),
                         "node": n})
    matched = sorted(((el, pairs[el["key"]]) for el in spec.get("elements", []) if el["key"] in pairs),
                     key=lambda p: p[0]["rect_norm"][1])
    for (a, na), (b, nb) in zip(matched, matched[1:]):
        if b["rect_norm"][1] - a["rect_norm"][1] > tol and norm(rect_t(nb), box)[1] < norm(rect_t(na), box)[1] - tol:
            add("FAIL", "order", nb, f"is above {label_of(na)}; the spec has it below", b["key"])

    # 2. rules for every screen
    content = snap.content()
    W, H = snap.W, snap.H
    bands = [("status bar / top inset", (0, 0, W, insets.get("top", 0))),
             ("navigation bar / home indicator", (0, H - insets.get("bottom", 0), W, insets.get("bottom", 0))),
             ("left inset", (0, 0, insets.get("left", 0), H)),
             ("right inset", (W - insets.get("right", 0), 0, insets.get("right", 0), H))]
    if insets.get("ime"):
        bands[1] = ("keyboard", (0, H - insets["bottom"], W, insets["bottom"]))
    for n in content:
        r = rect_t(n)
        sp = snap.scroll_parent(n)
        covered = n.get("interactionBlocked") == "covered"
        if node_key.get(id(n)) in edge_ok:
            continue
        for name, band in bands:
            iw, ih = inter(r, band)
            if band[2] > 0 and band[3] > 0 and iw > 1 and ih > 1:
                if sp is None:
                    add("FAIL", "safe-area", n, f"overlaps the {name} by {round(snap.to_pt(ih if band[2] == W else iw))}"
                        f"{unit}", node_key.get(id(n)))
                elif not covered:
                    add("WARN", "safe-area", n, f"scrollable content visible under the {name}", node_key.get(id(n)))
                break
        if not inside(r, (0, 0, W, H)) and sp is None:
            add("FAIL", "off-screen", n, "extends past the screen edge", node_key.get(id(n)))
        elif covered and sp is None:
            add("FAIL", "covered", n, "covered by another view; a tap would hit that view", node_key.get(id(n)))
        t = snap.text(n)
        if t.endswith("…") or t.endswith("..."):
            add("WARN", "truncated", n, "text ends in an ellipsis", node_key.get(id(n)))

    def is_target(n):
        return n.get("hittable") and n.get("kind") not in NOT_TARGET_KINDS and not snap.is_system(n) and \
            not TRANSIENT.search(snap.text(n))
    hit = [n for n in snap.nodes if n.get("rect") and is_target(n)]
    anc = {id(n): {id(a) for a in snap.ancestors(n)} for n in hit}
    hit_ids = {id(n) for n in hit}
    outer = [n for n in hit if not (anc[id(n)] & hit_ids)]  # a cell wins over the button it wraps
    visible = [n for n in outer if n.get("interactionBlocked") != "covered" and
               inside(rect_t(n), rect_t(snap.scroll_parent(n)) if snap.scroll_parent(n) else (0, 0, W, H))]
    for n in visible:
        _, _, w, h = rect_t(n)
        if min(snap.to_pt(w), snap.to_pt(h)) < min_t - 0.5:
            add("WARN", "tap-size", n, f"{snap.to_pt(w):.0f}×{snap.to_pt(h):.0f}{unit}, under {min_t}{unit}",
                node_key.get(id(n)))
    for i, a in enumerate(visible):
        for b in visible[i + 1:]:
            if id(a) in anc[id(b)] or id(b) in anc[id(a)]:
                continue
            ra, rb = rect_t(a), rect_t(b)
            if [round(v) for v in ra] == [round(v) for v in rb] and snap.text(a) == snap.text(b):
                continue  # the same element exposed twice
            iw, ih = inter(ra, rb)
            small = min(ra[2] * ra[3], rb[2] * rb[3]) or 1
            if iw * ih / small > 0.2:
                add("FAIL", "overlap", a, f"overlaps {label_of(b)} by {iw * ih / small:.0%} of the smaller one",
                    node_key.get(id(a)))

    # 3. pixel gate for an approved baseline
    if baseline_png and screenshot and shutil.which("agent-device"):
        diff_png = os.path.join(out_dir, "pixel-diff.png") if out_dir else None
        cmd = ["agent-device", "diff", "screenshot", "--baseline", baseline_png, screenshot, "--json"]
        res = subprocess.run(cmd + (["--out", diff_png] if diff_png else []), capture_output=True, text=True)
        try:
            d = json.loads(res.stdout)["data"]
            pct = d.get("mismatchPercentage", 0)
            if d.get("dimensionMismatch"):
                add("WARN", "pixels", None, "screenshot size differs from the baseline; capture both at the same "
                    "scale", "screen")
            elif pct > cfg["pixel_mismatch_max"]:
                add("WARN", "pixels", None, f"{pct:.2f}% of pixels differ from the baseline (limit "
                    f"{cfg['pixel_mismatch_max']}%)", "screen")
                eyes.append({"item": "screen", "question": "Apart from clock, battery and live data, does the current "
                             "screen differ from the approved baseline? Name the region.", "ref": baseline_png,
                             "node": None, "whole": True})
        except (ValueError, KeyError):
            pass

    # 4. crops for eyes: visual items, then geometry failures a look can confirm or dismiss
    if cfg.get("eyes_on_fail", True):
        for f in findings:
            if f["severity"] == "FAIL" and f["check"] in ("safe-area", "covered", "overlap", "off-screen") and f["rect"]:
                eyes.append({"item": f["key"] or f["what"], "question": f"{f['what']} {f['detail']}. Is it actually "
                             f"hidden, cut off or overlapped in the image?", "ref": None, "rect": f["rect"],
                             "finding": f})
    eyes_out = []
    for e in eyes[:cfg["max_crops"]]:
        cur = None
        if image and crops:
            if e.get("whole"):
                cur = image.crop(os.path.join(crops, "screen.cur.png"), 0, 0, image.w, image.h, max_side=1000)
            else:
                r = rect_t(e["node"]) if e.get("node") else tuple(e["rect"])
                cur = crop_rect(image, snap, r, os.path.join(crops, slug(str(e["item"])) + ".cur.png"))
        eyes_out.append({"item": e["item"], "question": e["question"], "ref": e.get("ref"), "cur": cur})

    findings.sort(key=lambda f: SEV_ORDER[f["severity"]])
    return findings, eyes_out, len(pairs)


def write_report(out_dir, spec, snap, insets, findings, eyes, matched, args):
    os.makedirs(out_dir, exist_ok=True)
    fails = sum(f["severity"] == "FAIL" for f in findings)
    warns = sum(f["severity"] == "WARN" for f in findings)
    verdict = "FAIL" if fails else "PASS"
    rep = {"screen": spec.get("screen"), "platform": snap.platform, "verdict": verdict,
           "counts": {"fail": fails, "warn": warns, "eyes": len(eyes), "matched": matched,
                      "spec_elements": len(spec.get("elements", []))},
           "spec_source": spec.get("source"), "insets": insets, "findings": findings, "eyes": eyes,
           "inputs": {k: getattr(args, k, None) for k in ("snapshot", "screenshot", "spec")}}
    with open(os.path.join(out_dir, "report.json"), "w") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, "report.md"), "w") as f:
        f.write(f"# ui-check: {spec.get('screen')} on {snap.platform} — {verdict}\n\n"
                f"Spec: {spec.get('source')} · matched {matched}/{len(spec.get('elements', []))} elements · "
                f"insets {insets.get('top')}/{insets.get('bottom')} ({insets.get('source')})\n\n"
                "| Severity | Check | Element | Detail |\n|---|---|---|---|\n")
        for x in findings:
            f.write(f"| {x['severity']} | {x['check']} | {x['what']} | {x['detail']} |\n")
        if eyes:
            f.write("\n## For eyes (ui-inspector)\n\n")
            for e in eyes:
                f.write(f"- **{e['item']}**: {e['question']} — ref: {e['ref'] or '-'} · cur: {e['cur'] or '-'}\n")
    if eyes:
        with open(os.path.join(out_dir, "eyes.txt"), "w") as f:
            for e in eyes:
                f.write(f"{e['item']} | {e['question']} | ref: {e['ref'] or '-'} | cur: {e['cur'] or '-'}\n")
    lines = [f"ui-audit {spec.get('screen')} {snap.platform}: {verdict} — {fails} fail, {warns} warn, "
             f"{len(eyes)} for eyes (matched {matched}/{len(spec.get('elements', []))})"]
    for x in findings[:10]:
        lines.append(f"{x['severity']} {x['check']:<10} {x['what']}: {x['detail']}")
    if len(findings) > 10:
        lines.append(f"… {len(findings) - 10} more in report.md")
    if eyes:
        lines.append(f"eyes: {os.path.join(out_dir, 'eyes.txt')}")
    lines.append(f"report: {os.path.join(out_dir, 'report.md')}")
    print("\n".join(lines))
    return 1 if fails else 0


# ---------------------------------------------------------------- capture with agent-device

def device_args(a):
    out = ["--platform", a.platform]
    if a.serial:
        out += ["--serial", a.serial]
    if a.udid:
        out += ["--udid", a.udid]
    return out


def capture_files(a, run_dir, cfg):
    """Snapshot, screenshot and insets of the screen shown now; files given on the command line win."""
    os.makedirs(run_dir, exist_ok=True)
    snap_p = a.snapshot or os.path.join(run_dir, "snapshot.json")
    shot_p = a.screenshot or os.path.join(run_dir, "screen.png")
    if not a.snapshot:
        res = subprocess.run(["agent-device", "snapshot", "--json"] + device_args(a),
                             capture_output=True, text=True, timeout=120)
        if res.returncode:
            raise RuntimeError("agent-device snapshot failed: " + (res.stderr or res.stdout).strip()[:300])
        with open(snap_p, "w") as f:
            f.write(res.stdout)
    if not a.screenshot and not a.no_screenshot:
        scale = ["--scale", "0.5"] if a.platform == "android" else []
        res = subprocess.run(["agent-device", "screenshot", shot_p, "--no-record"] + scale + device_args(a),
                             capture_output=True, text=True, timeout=120)
        if res.returncode:
            res = subprocess.run(["agent-device", "screenshot", shot_p] + scale + device_args(a),
                                 capture_output=True, text=True, timeout=120)
        if res.returncode:
            shot_p = None
    if a.insets:
        with open(a.insets) as f:
            insets = json.load(f)
    else:
        insets = get_insets(a.platform, cfg, a.serial, a.dumpsys)
    with open(os.path.join(run_dir, "insets.json"), "w") as f:
        json.dump(insets, f)
    with open(snap_p) as f:
        snap = Snap(json.load(f), a.platform)
    image = None
    if shot_p and os.path.exists(shot_p) and not a.no_screenshot:
        try:
            image = Image(shot_p)
        except Exception as e:
            print(f"note: screenshot not usable for crops ({e})", file=sys.stderr)
    return snap, insets, image, shot_p


def cmd_capture(a, approve=False):
    state = a.state or state_dir()
    cfg = load_config(state, a.config)
    screens = os.path.join(state, "screens")
    spec_p = os.path.join(screens, a.screen + ".spec.json")
    if os.path.exists(spec_p) and not a.force:
        print(f"{spec_p} exists (it may be hand-edited). Re-run with --force to replace it.")
        return 2
    ref_dir = os.path.join(screens, a.screen)
    snap, insets, image, shot = capture_files(a, os.path.join(ref_dir, "capture"), cfg)
    source = "baseline" if approve else f"reference-app:{a.platform}"
    spec = spec_from_snapshot(snap, insets, a.screen, source, cfg, image, os.path.join(ref_dir, "ref"))
    if approve and shot:
        spec["baseline_png"] = os.path.join(ref_dir, "baseline.png")
        shutil.copyfile(shot, spec["baseline_png"])
    os.makedirs(screens, exist_ok=True)
    with open(spec_p, "w") as f:
        json.dump(spec, f, indent=1, ensure_ascii=False)
    vis = sum(1 for e in spec["elements"] if e.get("visual"))
    print(f"spec {spec_p}: {len(spec['elements'])} elements ({vis} visual) from {source}, viewport "
          f"{snap.W}×{snap.H}, insets {insets['top']}/{insets['bottom']} ({insets['source']})")
    print("review it: drop elements that are live data (dates, counts), mark edge-to-edge ones, add a map "
          "for names that differ on the other platform")
    return 0


def cmd_check(a):
    state = a.state or state_dir()
    cfg = load_config(state, a.config)
    spec_p = a.spec or os.path.join(state, "screens", a.screen + ".spec.json")
    if os.path.exists(spec_p):
        with open(spec_p) as f:
            spec = json.load(f)
    else:
        print(f"note: no spec at {spec_p}; running the rules for every screen only", file=sys.stderr)
        spec = {"screen": a.screen, "source": "none", "elements": []}
    out = a.out or os.path.join(state, "reports", f"{time.strftime('%Y%m%d-%H%M%S')}-{a.screen}-{a.platform}")
    snap, insets, image, shot = capture_files(a, out, cfg)
    findings, eyes, matched = audit(spec, snap, insets, cfg, image, out, spec.get("baseline_png"), shot)
    a.spec = spec_p
    return write_report(out, spec, snap, insets, findings, eyes, matched, a)


def cmd_audit(a):
    state = a.state or state_dir()
    cfg = load_config(state, a.config)
    with open(a.spec) as f:
        spec = json.load(f)
    with open(a.snapshot) as f:
        snap = Snap(json.load(f), a.platform)
    if a.insets:
        with open(a.insets) as f:
            insets = json.load(f)
    else:
        insets = get_insets(a.platform, cfg, a.serial, a.dumpsys)
    image = Image(a.screenshot) if a.screenshot else None
    out = a.out or os.path.join(state, "reports", f"{time.strftime('%Y%m%d-%H%M%S')}-{spec.get('screen')}")
    findings, eyes, matched = audit(spec, snap, insets, cfg, image, out, spec.get("baseline_png"), a.screenshot)
    return write_report(out, spec, snap, insets, findings, eyes, matched, a)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("check", "capture", "approve", "audit", "insets"):
        s = sub.add_parser(name)
        if name in ("check", "capture", "approve"):
            s.add_argument("screen")
        s.add_argument("--platform", required=True, choices=["android", "ios"])
        s.add_argument("--serial")
        s.add_argument("--udid")
        s.add_argument("--state", help="state folder (default: nearest .claude/mobile-drive, else ~/.claude/mobile-drive)")
        s.add_argument("--config")
        s.add_argument("--dumpsys", help="saved `adb shell dumpsys window` output, instead of adb")
        if name != "insets":
            s.add_argument("--snapshot")
            s.add_argument("--screenshot")
            s.add_argument("--insets")
            s.add_argument("--no-screenshot", action="store_true")
        if name in ("check", "audit"):
            s.add_argument("--spec")
            s.add_argument("--out")
        if name in ("capture", "approve"):
            s.add_argument("--force", action="store_true")
    a = p.parse_args()
    try:
        if a.cmd == "insets":
            cfg = load_config(a.state or state_dir(), a.config)
            print(json.dumps(get_insets(a.platform, cfg, a.serial, a.dumpsys)))
            return 0
        if a.cmd == "audit":
            if not (a.spec and a.snapshot):
                p.error("audit needs --spec and --snapshot")
            return cmd_audit(a)
        if a.cmd == "check":
            return cmd_check(a)
        return cmd_capture(a, approve=a.cmd == "approve")
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
        print(f"ui-audit error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
