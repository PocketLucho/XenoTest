#!/usr/bin/env python3
"""Post-generation fixes for the recompiled C, applied in place.

libetc's VSync() waits in a loop that gives up after (n << 15) iterations,
sized for a 33 MHz R3000. The native loop runs several times faster, so it
timed out before the next VBlank arrived ("VSync: timeout"), reset the
root counters and halved the game's frame rate. The bound is raised 8x.

usage: patch_generated.py <generated dir>
"""
import glob, os, re, sys

PATCHES = [
    # 0x8004B698: sll a1, a1, 15  (VSync wait bound)
    (re.compile(r"r5 = r5 << 15;(\s*\n\s*PGXP_ALU\(0x00052BC0u, r5, _pgx1, 15u\); \}  /\* 0x8004B698:)"),
     r"r5 = r5 << 18;\1"),
]

def main():
    d = sys.argv[1]
    for pat, rep in PATCHES:
        hit = 0
        for f in sorted(glob.glob(os.path.join(d, "*_full_*.c"))):
            s = open(f, encoding="latin-1").read()
            t, n = pat.subn(rep, s)
            if n:
                open(f, "w", encoding="latin-1").write(t); hit += n
                print(f"{os.path.basename(f)}: {n}")
        if not hit and "<< 18;" not in "".join(open(f, encoding="latin-1").read() for f in glob.glob(os.path.join(d, "*_full_*.c")) if "0x8004B698:" in open(f, encoding="latin-1").read()):
            sys.exit("patch not applied: " + pat.pattern[:40])

if __name__ == "__main__":
    main()
