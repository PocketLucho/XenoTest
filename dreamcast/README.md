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
Movies played from the field (the new-game opening among them) go through the
STR library overlay; its open and playback routines are skipped too and the
field's "movie finished" word is set. After the new-game opening the field
still waits on a black screen: the hand-over from movie to field is not
reproduced yet.

Controller under qemu: the runtime fills the BIOS pad buffers from a script.
The title menu only accepts input once it is on screen, so leave time between
presses, for example Cross to open the menu, Up to select "Nuevo"/"New Game",
Cross to start: `DC_PAD="4500-4530:4000,5600-5630:0010,6200-6230:4000"`.
In 3D scenes a game frame takes many VBlanks under qemu, so hold buttons
longer there (about 400 VBlanks, e.g. `53000-53400:4000`) or the press falls
between two pad reads. With that, dialogue advances with Cross and a new game
reaches the first Gear battle.
Compile it with the runtime (`-Ipsxrecomp/runtime/include -Ipsxrecomp/runtime/dc`).

## Dreamcast build (KallistiOS)

In Flycast (HLE BIOS) a new game runs through the prologue to Lahan and the
first Gear battle (Cross to open the title menu, Up for "Nuevo", Cross).
The title and menus run at full speed; 3D scenes are still slow (a few
frames per second) since the PS1 GPU is drawn in software on the SH4.
Not tried on a console yet. The serial console prints a speed report every
10 s ("[perf]"); building `dc_host_kos.c` with `-DDC_PROFILE` adds PC samples.

1. Toolchain: KallistiOS's `utils/kos-chain` (sh-elf gcc 15.2, newlib 4.6),
   then build KallistiOS. No ARM toolchain needed: KOS ships a prebuilt sound
   driver.
2. Main EXE code and runtime with `kos-cc` (`-DPSX_NO_DEBUG_TOOLS`,
   `-Wno-error=implicit-function-declaration` for GCC 14+).
3. Overlays: `build_overlays.py ... --cc sh-elf-gcc --objcopy sh-elf-objcopy
   --objdir objkos --cflags "$KOS_CFLAGS -std=gnu11 -w -g0
   -Wno-error=implicit-function-declaration -DPSX_NO_DEBUG_TOOLS"`.
4. `link_kos.py` links `xg_dc.elf` and one file per overlay. All compiled
   overlays are about 12 MB, more than the console's 16 MB allows next to
   everything else, so they are loaded on demand into code slots: overlays
   that the game loads at the same PS1 address share a slot. With disc 1
   that is 22 overlays in 9 slots (5 MB); the whole program takes 10.7 MB.
5. `makeip` gives `IP.BIN`; `sh-elf-objcopy -O binary -R .stack xg_dc.elf
   1ST_READ.BIN` gives the program. Do not `scramble` it: a GD-ROM boots
   `1ST_READ.BIN` as is (only CD-R self-boot discs are scrambled), and a
   scrambled one jumps into garbage ("SH4 exception when blocked" in Flycast).
6. `make_gdi.py` writes a GD-ROM image (.gdi) for emulators and optical-drive
   emulators, with your EXE and raw disc image inside, in the high-density
   area (track 3). KallistiOS's `/cd` only mounts the low-density track 1 of
   a GD-ROM, so the runtime reads track 3's ISO9660 itself. The image
   contains your copy of the game: keep it to yourself.

Controls: A = Cross, B = Circle, X = Square, Y = Triangle, triggers = L1/R1.
Video is a first version: the PS1 display area copied into a 640x480
framebuffer each VBlank (no PowerVR yet, no movies).

## Next steps

1. Fold the remaining split entries and alias hosts into one body.
2. Drop unreachable resume switches.
3. Runtime: a first version is in `psxrecomp/runtime/dc`. Under qemu-sh4 the
   main EXE boots, reads the disc, skips the opening FMV and shows the title
   screen through the runtime's GTE and software GPU; with the pad it reaches
   the title menu and starts a new game. Next: the movie-to-field hand-over,
   a KallistiOS platform layer, the PowerVR2 renderer and VMU.
4. GPU command translation to PowerVR2; SPU to AICA; FMV.
