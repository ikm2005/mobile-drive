#!/usr/bin/env python3
"""Make a ui-check screen spec from a Figma frame, with no model in the loop.

  FIGMA_TOKEN=… figma-spec.py <screen> --file <file key> --node <node id> [--images] [--force]
  figma-spec.py <screen> --from-json <saved /v1/files/:key/nodes response> [--node <id>]

The token is read from the environment only. It is never printed, logged or written anywhere;
set it yourself (a Figma personal access token with read access to the file).

The frame becomes the viewport (Figma units = points). Its children become spec elements:
TEXT layers (label = the text), layers named `#some_id` (id = some_id), and component instances
with an id-like name (`item_add`). Layers named `_…` or hidden are skipped. A child named like
"Status Bar" or "Home Indicator" sets the insets and is not an element. Vectors, image fills and
layers named *icon*/*image*/*logo* are marked `visual`; `--images` downloads them as reference
crops (`<state>/screens/<screen>/ref/<key>.png`) for the ui-inspector agent.

Writes <state>/screens/<screen>.spec.json (see the ui-check skill's spec-format.md).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.figma.com/v1"
ID_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
VISUAL_NAME = re.compile(r"icon|image|logo|avatar|illustration", re.I)
STATUS = re.compile(r"status ?bar", re.I)
HOME = re.compile(r"home ?indicator", re.I)


def state_dir():
    d = os.getcwd()
    while True:
        cand = os.path.join(d, ".claude", "mobile-drive")
        if os.path.isdir(cand):
            return cand
        if os.path.dirname(d) == d:
            return os.path.expanduser("~/.claude/mobile-drive")
        d = os.path.dirname(d)


def get(url):
    token = os.environ.get("FIGMA_TOKEN")
    if not token:
        raise SystemExit("FIGMA_TOKEN is not set. Set it in your shell (never paste it into the chat), "
                         "or use the Figma MCP get_metadata route described in the ui-check skill.")
    req = urllib.request.Request(url, headers={"X-Figma-Token": token})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "item"


def descendants(n):
    for c in n.get("children") or []:
        if c.get("visible") is not False:
            yield c
            yield from descendants(c)


def frame_from_response(resp, node_id=None):
    nodes = resp.get("nodes") or {}
    if not nodes:
        raise SystemExit("no nodes in the Figma response")
    key = node_id if node_id in nodes else next(iter(nodes))
    return nodes[key]["document"]


def build_spec(frame, screen, file_key, cfg):
    fb = frame["absoluteBoundingBox"]
    fx, fy, W, H = fb["x"], fb["y"], fb["width"], fb["height"]
    insets = dict(cfg.get("figma", {}).get("insets") or {"top": 0, "bottom": 0, "left": 0, "right": 0})
    found_bars = []
    elements, keys, visual_ids = [], set(), {}

    def rect(n):
        b = n.get("absoluteBoundingBox") or {}
        return b.get("x", fx) - fx, b.get("y", fy) - fy, b.get("width", 0), b.get("height", 0)

    def add(n, el):
        k, i = el["key"], 2
        while el["key"] in keys:
            el["key"], i = f"{k}_{i}", i + 1
        keys.add(el["key"])
        x, y, w, h = rect(n)
        el["_rect"] = (x, y, w, h)
        elements.append(el)
        if el.get("visual"):
            visual_ids[el["key"]] = n["id"]

    def walk(n):
        for c in n.get("children") or []:
            if c.get("visible") is False or (c.get("name") or "").startswith("_"):
                continue
            name = c.get("name") or ""
            x, y, w, h = rect(c)
            if STATUS.search(name):
                insets["top"] = max(insets.get("top", 0), y + h)
                found_bars.append("status bar")
                continue
            if HOME.search(name):
                insets["bottom"] = max(insets.get("bottom", 0), H - y)
                found_bars.append("home indicator")
                continue
            t = c.get("type")
            group = t in ("INSTANCE", "COMPONENT", "FRAME", "GROUP")
            visual = t in ("VECTOR", "BOOLEAN_OPERATION", "STAR", "ELLIPSE") and w > 0 or \
                bool(VISUAL_NAME.search(name)) or any(f.get("type") == "IMAGE" for f in c.get("fills") or [])
            ident = name[1:] if name.startswith("#") else (name if group and ID_NAME.match(name) else None)
            texts = [x for x in descendants(c) if x.get("type") == "TEXT" and (x.get("characters") or "").strip()]
            if t == "TEXT" and (c.get("characters") or "").strip():
                text = c["characters"].strip()
                add(c, {"key": slug(ident or text), "kind": "text", "label": text, "required": True,
                        **({"id": ident} if ident else {})})
            elif visual:
                add(c, {"key": slug(ident or name), "kind": "image", "visual": True, "required": True,
                        **({"id": ident} if ident else {})})
            elif group and (ident or len(texts) == 1) and t != "GROUP":
                # a component with an id, or with one text in it (a button): one element, its frame
                label = texts[0]["characters"].strip() if len(texts) == 1 else None
                add(c, {"key": slug(ident or label), "kind": "button", "required": True,
                        **({"id": ident} if ident else {}), **({"label": label} if label else {})})
            else:
                walk(c)

    walk(frame)
    box = (insets.get("left", 0), insets.get("top", 0), W - insets.get("left", 0) - insets.get("right", 0),
           H - insets.get("top", 0) - insets.get("bottom", 0))
    for el in elements:
        x, y, w, h = el.pop("_rect")
        el["rect_norm"] = [round((x - box[0]) / box[2], 4), round((y - box[1]) / box[3], 4),
                           round(w / box[2], 4), round(h / box[3], 4)]
    elements.sort(key=lambda e: (e["rect_norm"][1], e["rect_norm"][0]))
    src = f"figma:{file_key or '?'}/{frame.get('id')}"
    spec = {"screen": screen, "source": src, "platform": "design", "captured": time.strftime("%Y-%m-%d"),
            "viewport": {"width": W, "height": H, "unit": "pt"},
            "insets": {k: insets.get(k, 0) for k in ("top", "bottom", "left", "right")},
            "insets_from": ", ".join(found_bars) or ("ui-check.json figma.insets" if cfg.get("figma", {}).get("insets")
                                                     else "none found: set figma.insets in ui-check.json"),
            "tolerance": cfg.get("tolerance", 0.04), "placement": cfg.get("placement", "warn"),
            "elements": elements, "map": {}}
    return spec, visual_ids


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("screen")
    p.add_argument("--file", help="Figma file key (from the file URL)")
    p.add_argument("--node", help="frame node id, e.g. 12:345 (from ?node-id=12-345 in the URL)")
    p.add_argument("--from-json", help="a saved /v1/files/:key/nodes response instead of calling the API")
    p.add_argument("--images", action="store_true", help="download visual layers as reference crops")
    p.add_argument("--state")
    p.add_argument("--force", action="store_true")
    p.add_argument("--out", help="write the spec here instead of <state>/screens/<screen>.spec.json")
    a = p.parse_args()
    state = a.state or state_dir()
    cfg_p = os.path.join(state, "ui-check.json")
    cfg = json.load(open(cfg_p)) if os.path.exists(cfg_p) else {}
    file_key = a.file or cfg.get("figma", {}).get("file_key")
    node = (a.node or "").replace("-", ":") or None
    out = a.out or os.path.join(state, "screens", a.screen + ".spec.json")
    if os.path.exists(out) and not a.force:
        print(f"{out} exists (it may be hand-edited). Re-run with --force to replace it.")
        return 2
    if a.from_json:
        with open(a.from_json) as f:
            resp = json.load(f)
    else:
        if not (file_key and node):
            p.error("--file and --node are needed (or figma.file_key in ui-check.json, or --from-json)")
        resp = get(f"{API}/files/{file_key}/nodes?ids={urllib.parse.quote(node)}")
    spec, visual_ids = build_spec(frame_from_response(resp, node), a.screen, file_key, cfg)
    if a.images and visual_ids and not a.from_json:
        ref = os.path.join(state, "screens", a.screen, "ref")
        os.makedirs(ref, exist_ok=True)
        ids = ",".join(visual_ids.values())
        urls = get(f"{API}/images/{file_key}?ids={urllib.parse.quote(ids)}&format=png&scale=1").get("images") or {}
        for el in spec["elements"]:
            u = urls.get(visual_ids.get(el["key"], ""))
            if u:
                dst = os.path.join(ref, el["key"] + ".png")
                urllib.request.urlretrieve(u, dst)
                el["crop"] = dst
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(spec, f, indent=1, ensure_ascii=False)
    vis = sum(1 for e in spec["elements"] if e.get("visual"))
    print(f"spec {out}: {len(spec['elements'])} elements ({vis} visual) from {spec['source']}, frame "
          f"{spec['viewport']['width']}×{spec['viewport']['height']}pt, insets {spec['insets']['top']}/"
          f"{spec['insets']['bottom']} ({spec['insets_from']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
