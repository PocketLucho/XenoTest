#!/usr/bin/env python3
"""Link the Dreamcast (KallistiOS) build: one main ELF plus one file per overlay.

The 16 MB Dreamcast cannot hold every compiled overlay at once (about 12 MB of
code), so overlays become files that the runtime loads on demand into code
slots. Overlays the game loads at the same PS1 address share a slot, since
they are never resident together.

Steps:
  1. link each overlay object alone to measure it (text + data + bss);
  2. write the slot table and a registry whose overlays name a file and a slot;
  3. link the main ELF (generated main-EXE code, runtime, game glue, slots),
     keeping every symbol the overlays call;
  4. link each overlay at its slot address against the main ELF's symbols and
     write it as a raw file OV/OV_nn.BIN.

Everything it writes is built from your own disc; keep it private.

Usage (inside a KallistiOS environment, i.e. after `source environ.sh`):
  link_kos.py --main-objs DIR --runtime-objs DIR --overlay-registry REG.c \\
              --overlay-objs DIR --out DIR
"""
import argparse, os, re, subprocess, sys
from pathlib import Path

PREFIX = os.environ.get("KOS_CC_BASE", "/opt/toolchains/dc/sh-elf") + "/bin/sh-elf-"
HDR_MAGIC = 0x564F4758


def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode:
        sys.stderr.write(r.stdout[-6000:] + r.stderr[-6000:])
        raise SystemExit(f"failed: {' '.join(map(str, cmd))[:400]}")
    return r.stdout


def kos_cc(*args):
    """kos-cc is a shell script that reads the KallistiOS environment."""
    return run(["kos-cc", *args])


ENTRY = re.compile(r'\{"([a-z0-9-]+)", 0x([0-9A-Fa-f]+)u, 0x([0-9A-Fa-f]+)u, (\d+), (\{.*?\}\}), (ov_(\d+)_dispatch)\}')

LDSCRIPT = """
SECTIONS {{
  . = 0x{base:08X};
  .ovhdr : {{ KEEP(*(.ovhdr)) }}
  .text : {{ *(.text .text.*) }}
  .rodata : {{ *(.rodata .rodata.* .rodata1) }}
  .data : {{ *(.data .data.* .sdata .sdata.*) }}
  . = ALIGN(4);
  _ov_file_end = .;
  .bss : {{ *(.bss .bss.* .sbss .sbss.* COMMON) }}
  . = ALIGN(32);
  _ov_end = .;
  __ov_end = .;
  /DISCARD/ : {{ *(.comment) *(.note*) *(.eh_frame*) *(.debug*) }}
}}
"""


def header_obj(work: Path, idx: int, cflags):
    c = work / f"hdr_{idx:02d}.c"
    c.write_text(
        "#include <stdint.h>\n"
        f"extern int ov_{idx}_dispatch();\nextern char _ov_end[];\n"
        "__attribute__((section(\".ovhdr\"), used))\n"
        f"const uint32_t ov_hdr[4] = {{0x{HDR_MAGIC:08X}u, (uint32_t)ov_{idx}_dispatch, (uint32_t)_ov_end, 0}};\n")
    o = work / f"hdr_{idx:02d}.o"
    kos_cc(*cflags, "-c", c, "-o", o)
    return o


def link_overlay(work, idx, obj, hdr, base, main_elf=None):
    ld = work / f"ov_{idx:02d}.ld"
    ld.write_text(LDSCRIPT.format(base=base))
    elf = work / f"ov_{idx:02d}.{'final' if main_elf else 'probe'}.elf"
    cmd = [PREFIX + "ld", "-EL", "-m", "shlelf", "-T", ld, "-o", elf, hdr, obj, "--no-warn-rwx-segments"]
    if main_elf:
        cmd += [f"--just-symbols={main_elf}"]
    else:
        cmd += ["--unresolved-symbols=ignore-all"]
    run(cmd)
    syms = {}
    for line in run([PREFIX + "nm", elf]).splitlines():
        p = line.split()
        if len(p) == 3:
            syms[p[2]] = int(p[0], 16)
    return elf, syms


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--main-objs", type=Path, required=True, help="objects of the generated main-EXE code")
    ap.add_argument("--runtime-objs", type=Path, required=True, help="runtime + game glue objects (KOS build)")
    ap.add_argument("--overlay-registry", type=Path, required=True, help="registry written by build_overlays.py")
    ap.add_argument("--overlay-objs", type=Path, required=True, help="overlay objects (KOS build)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    out, work = a.out, a.out / "work"
    (out / "OV").mkdir(parents=True, exist_ok=True)
    work.mkdir(exist_ok=True)
    cflags = os.environ["KOS_CFLAGS"].split() + ["-g0"]

    ents = []
    for m in ENTRY.finditer(a.overlay_registry.read_text()):
        ents.append(dict(id=m[1], load=int(m[2], 16), size=int(m[3], 16), ns=int(m[4]), samples=m[5],
                         sym=m[6], idx=int(m[7])))
    objs = {}
    for o in a.overlay_objs.glob("ov_*.o"):
        for line in run([PREFIX + "nm", "-g", "--defined-only", o]).splitlines():
            if line.endswith("_dispatch"):
                objs[line.split()[-1].lstrip("_")] = o   # sh-elf adds a leading "_"
    ents = [e for e in ents if e["sym"] in objs]

    # 1. measure
    for e in ents:
        e["hdr"] = header_obj(work, e["idx"], cflags)
        _, syms = link_overlay(work, e["idx"], objs[e["sym"]], e["hdr"], 0x8C000000)
        e["bytes"] = syms["_ov_end"] - 0x8C000000
    groups = {}
    for e in ents:
        groups.setdefault(e["load"] & 0x1FFFFFFF, []).append(e)
    slots = []
    for k, (load, g) in enumerate(sorted(groups.items())):
        size = (max(e["bytes"] for e in g) + 4095) & ~4095
        slots.append(size)
        for e in g:
            e["slot"] = k
    print(f"{len(ents)} overlays in {len(slots)} slots, {sum(slots) / 1048576:.2f} MB of slot memory")

    # 2. slots + registry
    reg = ['#include "dc_overlay.h"']
    reg += [f"static uint8_t s_slot{k}[{n}] __attribute__((aligned(32)));" for k, n in enumerate(slots)]
    reg.append("const DcOvSlot g_dc_ov_slots[] = {")
    reg += [f"  {{s_slot{k}, {n}u}}," for k, n in enumerate(slots)]
    reg += ["};", f"const int g_dc_ov_slot_count = {len(slots)};", "const DcOverlay g_dc_overlays[] = {"]
    for e in ents:
        reg.append(f'  {{"{e["id"]}", 0x{e["load"]:08X}u, 0x{e["size"]:X}u, {e["ns"]}, {e["samples"]}, 0, '
                   f'"OV_{e["idx"]:02d}.BIN", {e["slot"]}}},')
    reg += ["};", f"const int g_dc_overlay_count = {len(ents)};", ""]
    (work / "kos_registry.c").write_text("\n".join(reg))
    rt_inc = [f"-I{p}" for p in os.environ.get("XG_RUNTIME_INC", "").split(":") if p]
    kos_cc(*cflags, "-std=gnu11", *rt_inc, "-c", work / "kos_registry.c", "-o", work / "kos_registry.o")

    # 3. main ELF, keeping what overlays call
    need = set()
    for e in ents:
        for line in run([PREFIX + "nm", "-u", objs[e["sym"]]]).splitlines():
            need.add(line.split()[-1])
    keep = [f"-Wl,-u,{s}" for s in sorted(need)]
    main_objs = sorted(a.main_objs.glob("*.o")) + sorted(a.runtime_objs.glob("*.o"))
    elf = out / "xg_dc.elf"
    run(["kos-cc", "-o", elf, *main_objs, work / "kos_registry.o", *keep, "-lm"])
    msyms = {}
    for line in run([PREFIX + "nm", elf]).splitlines():
        p = line.split()
        if len(p) == 3:
            msyms[p[2]] = int(p[0], 16)

    # 4. overlays at their slots
    for e in ents:
        base = msyms[f"_s_slot{e['slot']}"]
        oelf, syms = link_overlay(work, e["idx"], objs[e["sym"]], e["hdr"], base, elf)
        binf = out / "OV" / f"OV_{e['idx']:02d}.BIN"
        run([PREFIX + "objcopy", "-O", "binary", "--remove-section=.bss", oelf, binf])
        assert syms["_ov_end"] - base <= slots[e["slot"]], e["id"]
    size = run([PREFIX + "size", elf]).splitlines()[1].split()
    text, data, bss = map(int, size[:3])
    print(f"main ELF: text {text / 1048576:.2f} MB, data {data / 1024:.0f} KB, bss {bss / 1048576:.2f} MB, "
          f"total {(text + data + bss) / 1048576:.2f} MB of 16 MB")


if __name__ == "__main__":
    main()
