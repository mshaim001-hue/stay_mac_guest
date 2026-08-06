#!/usr/bin/env python3
"""Stress-test school_probe_killer vs school-shaped osascript probes.

Spawns N copies of the EXACT idle-logout.js shape. Counts:
  HIT  — osascript died / non-numeric / timeout (blocker worked)
  MISS — returned an integer (would allow WARN/LOGOUT)

Usage:
  python3 tools/stress_school_blocker.py [--n 2000] [--workers 2]
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KILLER_CANDIDATES = [
    ROOT / "dist" / "StayMacGuest.app" / "Contents" / "Resources" / "school_probe_killer",
    ROOT / "tools" / "school_probe_killer",
    ROOT / "dist" / "school_probe_killer",
]

SCHOOL_JS = (
    'ObjC.import("CoreGraphics");'
    "function s(t){ return $.CGEventSourceSecondsSinceLastEventType(1, t); }"
    "Math.floor(Math.min(s(1), s(3), s(10), s(22), s(25)));"
)

OURS_JS = (
    'ObjC.import("CoreGraphics");'
    "(() => { const xs = [1,3,10,22,25].map("
    "x => $.CGEventSourceSecondsSinceLastEventType(1, x)); "
    "return Math.floor(Math.min(...xs)); })()"
)


def find_killer() -> Path:
    for p in KILLER_CANDIDATES:
        if p.is_file() and (p.stat().st_mode & 0o111):
            return p
    raise SystemExit(
        "school_probe_killer not found — compile first:\n"
        "  cc -O2 -o tools/school_probe_killer tools/school_probe_killer.c"
    )


def run_probe(js: str, timeout: float = 5.0) -> str:
    try:
        out = subprocess.check_output(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", js],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        s = out.strip()
        if s.isdigit():
            return "num"
        return "fail"
    except subprocess.TimeoutExpired:
        return "timeout"
    except Exception:
        return "fail"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--ours", type=int, default=20)
    args = ap.parse_args()

    killer = find_killer()
    print(f"killer={killer}")
    print(f"school probes n={args.n} parallel={args.parallel}")

    warm = run_probe(SCHOOL_JS)
    print(f"warmup (no killer yet): {warm}")

    proc = subprocess.Popen(
        [str(killer), "--workers", str(args.workers)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.3)
    if proc.poll() is not None:
        err = proc.stderr.read() if proc.stderr else ""
        raise SystemExit(f"killer exited early: {err}")

    hits = misses = other = 0
    t0 = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futs = [pool.submit(run_probe, SCHOOL_JS) for _ in range(args.n)]
            for fut in as_completed(futs):
                r = fut.result()
                if r == "num":
                    misses += 1
                elif r in ("fail", "timeout"):
                    hits += 1
                else:
                    other += 1
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
        # reap forked workers
        subprocess.run(["pkill", "-f", "school_probe_killer"], capture_output=True)

    elapsed = time.perf_counter() - t0
    total = hits + misses + other
    print("--- school-shaped probes ---")
    print(f"HIT (blocked) = {hits}")
    print(f"MISS (numeric)= {misses}")
    print(f"other         = {other}")
    print(f"miss rate     = {misses}/{total} = {100.0 * misses / max(total, 1):.4f}%")
    print(f"elapsed       = {elapsed:.1f}s ({total / max(elapsed, 0.01):.0f} probes/s)")

    proc2 = subprocess.Popen(
        [str(killer), "--workers", str(args.workers)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
    ours_ok = ours_bad = 0
    try:
        for _ in range(args.ours):
            if run_probe(OURS_JS) == "num":
                ours_ok += 1
            else:
                ours_bad += 1
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=2)
        except Exception:
            proc2.kill()
        subprocess.run(["pkill", "-f", "school_probe_killer"], capture_output=True)

    print("--- control: our school_idle_seconds shape ---")
    print(f"survived={ours_ok} killed/fail={ours_bad}")

    if misses == 0 and ours_ok == args.ours:
        print("Verdict: PASS — 0 misses, control intact")
        return 0
    if misses == 0 and ours_ok > 0:
        print("Verdict: PASS (school) / WARN (control partial)")
        return 0
    print("Verdict: FAIL — misses > 0 or control broken")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
