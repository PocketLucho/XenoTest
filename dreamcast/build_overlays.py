#!/usr/bin/env python3
"""Build every Disc 1 overlay for psxrecomp's Dreamcast target.

For each catalog image this script:
  1. extracts the authenticated raw bytes and native seeds from the user's own
     disc (tools/extract_disc_overlays.py checks the SHA-256 of each image),
  2. wraps the bytes in a PS-X EXE header so psxrecomp-game can read them,
  3. runs `psxrecomp-game --overlay --target dc`,
  4. compiles one translation unit per overlay with the dispatcher renamed to
     ov_<n>_dispatch, then hides every other symbol with objcopy so the
     func_XXXXXXXX names of overlays that share a load address do not collide,
  5. writes dc_overlay_registry.c: one descriptor per overlay with its load
     range, sixteen sample code words and its dispatcher. The runtime picks the
     overlay whose samples match guest RAM (runtime/dc/dc_overlay.c).

Patched discs (fan translations) move files and change some overlays. The
script reads the disc's own file table (LBA 24) to find each image: an image
whose bytes still match the authenticated SHA-256 is extracted from its new
sector by the normal extractor. With --allow-patched an image that a patch
changed is taken from the disc as is, if the catalog's function starts still
look like function starts in it (the patch kept the code layout).

Everything it writes is derived from the disc and stays in --work. Nothing
here contains game bytes, and the outputs must not be committed.
"""
import argparse, hashlib, json, os, re, shutil, struct, subprocess, sys, tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAME_ID = "dc0b2dd786203d4cce5927c5a3fc85a18f39a3f7406078860076ebb0bbae7119"
MANIFEST_ID = "8dc1984c78754d909617eabd6501aab7186555a65ef8fc4cf3f2dca497bf533a"
CFLAGS = ["-std=gnu11", "-O2", "-m4", "-ml", "-DPSX_NO_DEBUG_TOOLS", "-w"]


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.stderr.write(r.stdout[-4000:] + r.stderr[-4000:])
        raise SystemExit(f"failed: {' '.join(map(str, cmd))}")
    return r


def psx_exe(raw: bytes, load: int, entry: int) -> bytes:
    h = bytearray(0x800)
    h[0:8] = b"PS-X EXE"
    size = (len(raw) + 0x7FF) & ~0x7FF
    struct.pack_into("<IIII", h, 0x10, entry, 0, load, size)
    return bytes(h) + raw + b"\0" * (size - len(raw))


def code_ranges(path: Path):
    out = []
    for line in path.read_text().splitlines():
        p = line.split()
        if p and p[0] == "R":
            out.append((int(p[1], 16), int(p[2], 16)))
    return out


def samples(raw: bytes, load: int, ranges, n=16):
    """n words spread over the code ranges (instructions, never data)."""
    words = [a for lo, ln in ranges for a in range(lo, lo + ln, 4)]
    if not words:
        return []
    step = max(1, len(words) // n)
    pick = words[::step][:n]
    return [(a, struct.unpack_from("<I", raw, a - load)[0]) for a in pick]


TOOLS = ROOT / "tools"
OVL = ROOT / "annotations" / "overlays"


def _x():
    sys.path.insert(0, str(TOOLS))
    import extract_disc_overlays as x
    return x


def file_table(disc):
    """Xenogears' file table: 7-byte records (24-bit LBA, 32-bit size) from LBA 24."""
    t = disc.read_user_data(24, 2048 * 16)
    return [(t[i] | t[i + 1] << 8 | t[i + 2] << 16, struct.unpack_from("<i", t, i + 3)[0])
            for i in range(0, len(t) - 6, 7)]


def payload(x, disc, src, lba, size):
    st = disc.read_user_data(lba, size)
    return x.lzss_decompress(st) if src["storage"] == "lzss" else st + bytes(max(0, src["size"] - len(st)))


def plausible_starts(raw, load, starts):
    def w(o): return struct.unpack_from("<I", raw, o)[0] if 0 <= o <= len(raw) - 4 else 0
    good = sum(1 for a in starts if (w(a - load) >> 16) == 0x27BD or 0x03E00008 in (w(a - load - 4), w(a - load - 8)))
    return good / max(1, len(starts))


def locate(a):
    """Write a manifest with this disc's sectors; return {image_id: patched payload}."""
    x = _x()
    disc = x.open_disc(a.disc)
    ents = file_table(disc)
    text = (OVL / "disc1-images.toml").read_text()
    man = tomllib.loads(text)
    cat = {i["source_record_id"]: i for i in tomllib.loads((OVL / "index.toml").read_text())["images"]}
    found, patched, deltas = {}, {}, []
    for src in man["images"]:
        def ok(lba):
            try:
                return hashlib.sha256(payload(x, disc, src, lba, src["stored_size"])).hexdigest() == src["sha256"]
            except Exception:
                return False
        if ok(src["source_sector"]):
            found[src["id"]] = src["source_sector"]
        else:
            c = sorted((abs(l - src["source_sector"]), l) for l, z in ents if z == src["stored_size"] and ok(l))
            if c:
                found[src["id"]] = c[0][1]
        if src["id"] in found:
            deltas.append((src["source_sector"], found[src["id"]] - src["source_sector"]))
    for src in man["images"]:
        if src["id"] in found:
            continue
        delta = min(deltas, key=lambda d: abs(d[0] - src["source_sector"]))[1]
        lba = src["source_sector"] + delta
        size = next((z for l, z in ents if l == lba and z > 0), None)
        img = cat[src["id"]]
        if size is None or not a.allow_patched:
            print(f"skip {img['id']}: changed on this disc" + ("" if a.allow_patched else " (see --allow-patched)"))
            continue
        raw = payload(x, disc, src, lba, size)[:src["size"]]
        starts = x._function_starts((ROOT / img["annotations"]).resolve())
        score = plausible_starts(raw, int(src["load_address"], 0), [int(v, 0) for v in starts])
        if len(raw) != src["size"] or score < 0.8:
            print(f"skip {img['id']}: patched and code layout differs ({score:.0%} plausible starts)")
            continue
        print(f"note {img['id']}: patched by this disc, code layout kept ({score:.0%}), built unauthenticated")
        patched[img["id"]] = (raw, starts)
    for sid, lba in found.items():
        text = re.sub(r'(id = "%s"\n(?:[^\[]*?\n)?source_sector = )\d+' % re.escape(sid), lambda m: m.group(1) + str(lba), text)
    a.manifest = a.work / "disc1-images.located.toml"
    a.manifest.write_text(text)
    a.found = {cat[k]["id"] for k in found}
    return patched


def write_patched(d, raw, starts, img):
    roots = tomllib.loads((OVL / "aot-dispatch-roots.toml").read_text())["images"]
    iso = next((r["dispatch_entry_pcs"] for r in roots if r["id"] == img["image_id"]), [])
    iso = sorted({f"0x{int(v, 0):08X}" for v in iso} - set(starts))
    (d / "image.bin").write_bytes(raw)
    (d / "image.seeds").write_text("\n".join([*(f"call_root {e}" for e in starts),
                                             *(f"isolated_root {e}" for e in iso)]) + "\n")


def build_one(idx, img, a):
    work = a.work
    iid = img["image_id"]
    stem = iid.replace("-", "_")
    d = work / iid
    d.mkdir(parents=True, exist_ok=True)
    raw_p, seeds_p = d / "image.bin", d / "image.seeds"
    if iid in a.patched:
        write_patched(d, *a.patched[iid], img)
    elif not raw_p.exists():
        run([sys.executable, "-I", str(TOOLS / "extract_disc_overlays.py"), "--disc", str(a.disc),
             "--manifest", str(a.manifest), "--image-id", iid, "--raw-out", str(raw_p),
             "--seeds-out", str(seeds_p)], cwd=ROOT)
    raw = raw_p.read_bytes()
    load, entry = int(img["load_address"], 16), int(img["entry_pc"], 16)
    exe = d / f"{stem}.psx"
    exe.write_bytes(psx_exe(raw, load, entry))
    gen = d / "gen"
    full = gen / f"{stem}.psx_full.c"
    if not full.exists():
        if gen.exists():
            shutil.rmtree(gen)
        run([str(a.recomp), str(exe), "--seeds", str(seeds_p), "--out-dir", str(gen), "--overlay",
             "--target", "dc", "--project-root", str(a.psxrecomp), "--game-identity-sha256", GAME_ID,
             "--manifest-digest-sha256", MANIFEST_ID], cwd=ROOT, env={**os.environ, "PSX_CPS": "1"})
    sym = f"ov_{idx}_dispatch"
    tu = d / "ov.c"
    tu.write_text(f'#include "psx_xg_render_auth_hook_types.h"\n#define psx_dispatch_game_compiled {sym}\n'
                  f'#include "{full}"\n#include "{gen / (stem + ".psx_dispatch.c")}"\n')
    obj, out = d / "ov.o", a.work / "obj" / f"ov_{idx:02d}.o"
    run([a.cc, *CFLAGS, f"-I{a.psxrecomp}/runtime/include", f"-I{gen}", "-c", str(tu), "-o", str(obj)])
    run([a.objcopy, f"--keep-global-symbol={sym}", str(obj), str(out)])
    rng = code_ranges(gen / f"{stem}.psx_full.ranges")
    return dict(idx=idx, id=iid, load=load, size=len(raw), sym=sym, samples=samples(raw, load, rng))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--disc", type=Path, required=True, help="your own Disc 1 .cue")
    ap.add_argument("--work", type=Path, required=True, help="private output directory")
    ap.add_argument("--psxrecomp", type=Path, default=ROOT / "psxrecomp")
    ap.add_argument("--recomp", type=Path, help="psxrecomp-game binary")
    ap.add_argument("--cc", default="sh4-linux-gnu-gcc")
    ap.add_argument("--objcopy", default="sh4-linux-gnu-objcopy")
    ap.add_argument("--only", help="comma-separated image ids")
    ap.add_argument("--allow-patched", action="store_true",
                    help="also build overlays a patch changed, if their code layout is unchanged")
    ap.add_argument("-j", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()
    a.work, a.disc, a.psxrecomp = a.work.resolve(), a.disc.resolve(), a.psxrecomp.resolve()
    a.recomp = (a.recomp or a.psxrecomp / "recompiler/build/psxrecomp-game").resolve()
    (a.work / "obj").mkdir(parents=True, exist_ok=True)
    plan = json.loads(run([sys.executable, "-I", str(ROOT / "tools/extract_disc_overlays.py"),
                           "--list-json"], cwd=ROOT).stdout)
    a.patched = locate(a)
    plan = [p for p in plan if p["image_id"] in a.found or p["image_id"] in a.patched]
    for p in [p for p in plan if (int(p["load_address"], 16) & 0x1FFFFFFF) + p["size"] > 0x200000]:
        print(f"skip {p['image_id']}: loads above 2 MB (development-kit RAM only)")
    plan = [p for p in plan if (int(p["load_address"], 16) & 0x1FFFFFFF) + p["size"] <= 0x200000]
    if a.only:
        keep = set(a.only.split(","))
        plan = [p for p in plan if p["image_id"] in keep]
    with ThreadPoolExecutor(a.j) as ex:
        res = list(ex.map(lambda t: build_one(t[0], t[1], a), enumerate(plan)))
    lines = ['/* Generated by dreamcast/build_overlays.py from the user\'s disc. Do not commit. */',
             '#include "dc_overlay.h"']
    lines += [f"extern int {r['sym']}(CPUState*, uint32_t);" for r in res]
    lines.append("const DcOverlay g_dc_overlays[] = {")
    for r in res:
        s = ", ".join(f"{{0x{x:08X}u, 0x{w:08X}u}}" for x, w in r["samples"])
        lines.append(f'  {{"{r["id"]}", 0x{r["load"]:08X}u, 0x{r["size"]:X}u, {len(r["samples"])}, {{{s}}}, {r["sym"]}}},')
    lines += ["};", f"const int g_dc_overlay_count = {len(res)};", ""]
    (a.work / "dc_overlay_registry.c").write_text("\n".join(lines))
    for r in res:
        print(f"{r['id']:45s} {r['load']:08X} +{r['size']:X}")
    print(f"{len(res)} overlays -> {a.work}/obj, registry {a.work}/dc_overlay_registry.c")


if __name__ == "__main__":
    main()
