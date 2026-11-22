/* xg_dc_game.c - Xenogears glue for psxrecomp's Dreamcast runtime.
 *
 * Linked with psxrecomp/runtime/dc; overrides its weak dc_game_init().
 * Addresses are those of the NTSC-U Disc 1 main EXE (SLUS_006.64) and its
 * movie overlay.
 */
#include "dc_overlay.h"

/* Main-EXE scene control: scene_set(n) selects the scene that the scene
 * runner starts next; the byte holds the scene the opening movie hands over
 * to (1 = field on a new game). */
#define XG_SCENE_SET        0x8001996Cu
#define XG_NEXT_AFTER_MOVIE 0x8004FE46u
#define XG_MOVIE_ENTRY      0x800737ECu   /* movie overlay scene entry */

#ifndef XG_DC_SKIP_FMV
#define XG_DC_SKIP_FMV 1   /* FMV (MDEC) is not ported yet */
#endif

/* What the movie scene does once its video ends: pick the next scene. The
 * runner that called it then starts that scene. */
static void movie_skipped(CPUState* cpu)
{
    dc_call_guest(cpu, XG_SCENE_SET, dc_read8(XG_NEXT_AFTER_MOVIE), 0, 0, 0);
}

void dc_game_init(CPUState* cpu)
{
    (void)cpu;
    if (XG_DC_SKIP_FMV) dc_overlay_skip_fn("movie-overlay", XG_MOVIE_ENTRY, movie_skipped);
}
