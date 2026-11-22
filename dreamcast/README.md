# Dreamcast port experiments

Experimental work toward running Xenogears on the Sega Dreamcast (SH-4 200 MHz,
16 MB RAM, PowerVR2, KallistiOS). Nothing here changes the PC build.

You still need your own Xenogears disc: no game code or data is stored here.

## psxrecomp `--target dc`

The `psxrecomp` submodule now points at the
[PocketLucho/psxrecomp](https://github.com/PocketLucho/psxrecomp) fork, branch
`dc-target`, which adds an experimental output mode to the recompiler on top of
upstream `65b1b4c`. It rewrites every generated translation unit for a small
32-bit host:

- direct guest-memory access, with an optional MMU-mapped mode (`-DDC_GUEST_RAM_MMU`);
- MIPS GPRs and HI/LO kept in C locals, written back only where the guest
  register file can be observed;
- one shared cold interrupt stub per function;
- PC tooling hooks and per-instruction cycle timing compiled out.

Details and measurements: `psxrecomp/docs/DREAMCAST_TARGET.md`.

```sh
git submodule sync psxrecomp
git submodule update --init psxrecomp
cmake -S psxrecomp/recompiler -B psxrecomp/recompiler/build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build psxrecomp/recompiler/build --target psxrecomp-game
# game/slus_006.64 = your own disc-1 EXE
psxrecomp/recompiler/build/psxrecomp-game --config game.toml --target dc \
  --game-identity-sha256 <id> --manifest-digest-sha256 <id>
```

The identities come from
`python3 tools/native_render_manifest.py metadata-declared native_renderer/xg_render_manifest.toml`.

Build the generated shards for SH-4 with, for example,
`sh4-linux-gnu-gcc -std=gnu11 -O2 -m4 -ml -DPSX_NO_DEBUG_TOOLS [-DDC_GUEST_RAM_MMU] -Ipsxrecomp/runtime/include -Igenerated`.

## Results so far (main EXE, disc 1)

| Output | SH-4 code size |
|---|---|
| PC output, no cycle timing | 5.45 MB |
| `--target dc` | 1.69 MB |
| `--target dc` + MMU-mapped RAM | 0.65 MB |

SH-4 instructions per MIPS instruction on synthetic PS1-style workloads go from
9-10 (PC output) to 3.1-4.9 (`--target dc` + MMU). Interrupts are polled only on
backward branches (loops), which cut the poll sites in the main EXE from 17,771
to 2,181. Set `PSXRECOMP_DC_IRQ_ALL_BRANCHES=1` to keep every poll.

The recompiler no longer splits functions at "branches" decoded from data
sections. Those had added 792 fake entry points and emitted the same bytes up to
24 times; with the fix only 15 splits remain and the generated code is about
half the size. The PC output does not change.

Functions no longer carry the savestate resume switch, and only call returns are
resumable entry points (18,505 switch cases down to 2,983). The DC runtime must
therefore service interrupts in place; `PSXRECOMP_DC_RESUME_ALL_LEADERS=1` keeps
every block resumable.

## Overlays

`dreamcast/build_overlays.py` builds the 24 Disc 1 overlays for `--target dc`
from your own disc and writes everything into a private work directory
(do not commit it):

```sh
python3 dreamcast/build_overlays.py --disc path/to/disc1.cue --work build-dc/overlays \
    --psxrecomp psxrecomp --allow-patched
```

It extracts each image (checking its SHA-256), compiles it with
`psxrecomp-game --overlay --target dc`, renames its dispatcher, hides its other
symbols with `objcopy` and writes `dc_overlay_registry.c`. Link
`build-dc/overlays/obj/*.o` and the registry with the main EXE and
`psxrecomp/runtime/dc`; at run time the runtime picks the overlay that is in RAM
(`runtime/dc/dc_overlay.c`).

Patched discs (fan translations) are supported: the script reads the disc's file
table to find files a patch moved. With `--allow-patched` it also builds
overlays the patch changed, when the catalog's function starts still line up
with the code (the patch only changed text or data). Those are reported as
unauthenticated.

## Game glue

`dreamcast/xg_dc_game.c` overrides the runtime's `dc_game_init()`. For now it
skips the opening FMV (no MDEC yet): calls to the movie overlay's scene entry
return at once after selecting the scene the movie would hand over to, so the
game goes straight to the field. Build with `-DXG_DC_SKIP_FMV=0` to keep it.
Compile it with the runtime (`-Ipsxrecomp/runtime/include -Ipsxrecomp/runtime/dc`).

## Next steps

1. Fold the remaining split entries and alias hosts into one body.
2. Drop unreachable resume switches.
3. Runtime: a first version is in `psxrecomp/runtime/dc`. Under qemu-sh4 the
   main EXE boots, reads the disc, skips the opening FMV and runs the field
   overlay's frame loop. Next: GTE, GPU, a KallistiOS platform layer, pad
   and VMU.
4. GPU command translation to PowerVR2; SPU to AICA; FMV.
