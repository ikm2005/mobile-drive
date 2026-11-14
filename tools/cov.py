#!/usr/bin/env python3
"""Line coverage of scripts/*.py from the Python tests, with the standard library only.

  python3 tools/cov.py [--min PCT]

Runs every tools/test_*.py in-process (as __main__, SystemExit caught; a test's output, minus its
`ok` lines, is shown only when it fails) under sys.settrace, and records the lines executed in files
under scripts/. The lines that count come from compiling each script and walking its code objects'
co_lines(). Prints one line per script with the functions never entered, then a TOTAL line. Exit 1
when a test failed, or when --min is given and TOTAL is below it. Scripts run only through a subprocess are not counted.
"""
import argparse
import contextlib
import glob
import io
import os
import runpy
import sys
import tempfile
import threading
import traceback
import types

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")


def code_objects(path):
    with open(path, encoding="utf-8") as f:
        top = compile(f.read(), path, "exec")
    stack = [top]
    while stack:
        co = stack.pop()
        yield co
        stack.extend(c for c in co.co_consts if isinstance(c, types.CodeType))


def executable(path):
    """(set of executable line numbers, {(name, first line): qualified name} of named functions)."""
    lines, funcs = set(), {}
    for co in code_objects(path):
        lines.update(ln for _, _, ln in co.co_lines() if ln)
        if not co.co_name.startswith("<"):  # named functions only, not lambdas or comprehensions
            funcs[(co.co_name, co.co_firstlineno)] = getattr(co, "co_qualname", co.co_name)
    return lines, funcs


class Recorder:
    def __init__(self, targets):
        self.targets = set(targets)
        self.paths = {}  # co_filename → real path or None, so realpath runs once per file name
        self.lines = {t: set() for t in self.targets}
        self.entered = {t: set() for t in self.targets}

    def target(self, filename):
        if filename not in self.paths:
            real = os.path.realpath(filename) if filename and not filename.startswith("<") else None
            self.paths[filename] = real if real in self.targets else None
        return self.paths[filename]

    def trace(self, frame, event, arg):
        path = self.target(frame.f_code.co_filename)
        if path is None:
            return None
        self.entered[path].add((frame.f_code.co_name, frame.f_code.co_firstlineno))
        hits = self.lines[path]
        hits.add(frame.f_lineno)

        def local(fr, ev, _):
            if ev == "line":
                hits.add(fr.f_lineno)
            return local
        return local


def run_test(path, rec):
    buf = io.StringIO()
    # a test may change argv, sys.path, cwd, the environment or tempfile's cached folder: undo it after
    saved = sys.argv, list(sys.path), os.getcwd(), dict(os.environ), tempfile.tempdir
    sys.argv = [path]
    sys.path.insert(0, os.path.dirname(path))
    rc = 0
    sys.settrace(rec.trace)
    threading.settrace(rec.trace)
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            runpy.run_path(path, run_name="__main__")
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:  # a crashing test is a failed test, not a crashed runner
        buf.write(traceback.format_exc())
        rc = 1
    finally:
        sys.settrace(None)
        threading.settrace(None)
        sys.argv, sys.path[:] = saved[0], saved[1]
        os.chdir(saved[2])
        os.environ.clear()
        os.environ.update(saved[3])
        tempfile.tempdir = saved[4]
    return rc, buf.getvalue()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--min", type=float, help="fail when TOTAL line coverage is below this percentage")
    a = p.parse_args()
    scripts = sorted(os.path.realpath(f) for f in glob.glob(os.path.join(SCRIPTS, "*.py")))
    tests = sorted(os.path.realpath(f) for f in glob.glob(os.path.join(ROOT, "tools", "test_*.py")))
    rec = Recorder(scripts)
    failed = []
    for t in tests:
        rc, out = run_test(t, rec)
        name = os.path.relpath(t, ROOT)
        if rc:
            failed.append(name)
            print(f"FAIL {name} (exit {rc}):")
            shown = [x for x in out.rstrip().splitlines() if not x.startswith("ok")]  # the failures, not the passes
            print("\n".join("      " + x for x in shown[-40:]))
        else:
            print(f"ok   {name}")
    hit_all = total_all = 0
    width = max((len(os.path.relpath(s, ROOT)) for s in scripts), default=0)
    for s in scripts:
        lines, funcs = executable(s)
        hit = len(rec.lines[s] & lines)
        hit_all, total_all = hit_all + hit, total_all + len(lines)
        pct = 100.0 * hit / len(lines) if lines else 100.0
        never = [q for k, q in sorted(funcs.items(), key=lambda kv: kv[0][1]) if k not in rec.entered[s]]
        print(f"{os.path.relpath(s, ROOT):<{width}}  {hit}/{len(lines)} = {pct:.1f}%" +
              (f"  never entered: {', '.join(never)}" if never else ""))
    total = 100.0 * hit_all / total_all if total_all else 100.0
    print(f"{'TOTAL':<{width}}  {hit_all}/{total_all} = {total:.1f}%")
    if failed:
        print(f"{len(failed)} test file(s) failed: {', '.join(failed)}")
    if a.min is not None and total < a.min:
        print(f"coverage {total:.1f}% is below --min {a.min:g}%")
    return 1 if failed or (a.min is not None and total < a.min) else 0


if __name__ == "__main__":
    sys.exit(main())
