# Dreamcast port experiments

Experimental work toward running Xenogears on the Sega Dreamcast (SH-4 200 MHz,
16 MB RAM, PowerVR2, KallistiOS). Nothing here changes the PC build.

You still need your own Xenogears disc: no game code or data is stored here.

## psxrecomp `--target dc`

`patches/psxrecomp-0001-dc-target.patch` adds an experimental output mode to the
recompiler (made against psxrecomp `65b1b4c`, the commit this repo pins). It
rewrites every generated translation unit for a small 32-bit host:

- direct guest-memory access, with an optional MMU-mapped mode (`-DDC_GUEST_RAM_MMU`);
- MIPS GPRs and HI/LO kept in C locals, written back only where the guest
  register file can be observed;
- one shared cold interrupt stub per function;
- PC tooling hooks and per-instruction cycle timing compiled out.

Details and measurements: `psxrecomp/docs/DREAMCAST_TARGET.md` after applying.

```sh
git submodule update --init psxrecomp
dreamcast/apply-psxrecomp-patches.sh
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
| `--target dc` | 2.12 MB |
| `--target dc` + MMU-mapped RAM | 1.04 MB |

SH-4 instructions per MIPS instruction on synthetic PS1-style workloads go from
9-10 (PC output) to 3.3-5.2 (`--target dc` + MMU). Interrupts are polled only on
backward branches (loops), which cut the poll sites in the main EXE from 17,771
to 2,181. Set `PSXRECOMP_DC_IRQ_ALL_BRANCHES=1` to keep every poll.

The recompiler no longer splits functions at "branches" decoded from data
sections. Those had added 792 fake entry points and emitted the same bytes up to
24 times; with the fix only 15 splits remain and the generated code is about
half the size. The PC output does not change.

## Next steps

1. Fold the remaining split entries and alias hosts into one body.
2. Drop unreachable resume switches.
3. Minimal KallistiOS runtime: dispatch, BIOS HLE, CD, VBlank, pad, VMU.
4. GPU command translation to PowerVR2; SPU to AICA; FMV.
