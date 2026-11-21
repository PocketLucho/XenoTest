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
| `--target dc` | 4.22 MB |
| `--target dc` + MMU-mapped RAM | 2.88 MB |

SH-4 instructions per MIPS instruction on synthetic PS1-style workloads go from
9-10 (PC output) to 3.9-5.5 (`--target dc` + MMU). The script VM and packet
building reach 2.4-3.1 when interrupt polling is limited to back-edges, which is
still to be implemented.

## Next steps

1. Stop splitting functions at internal branch targets.
2. Poll interrupts on back-edges and calls only; drop unreachable resume switches.
3. Minimal KallistiOS runtime: dispatch, BIOS HLE, CD, VBlank, pad, VMU.
4. GPU command translation to PowerVR2; SPU to AICA; FMV.
