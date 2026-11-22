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

/* Movies played inside the field (the new-game opening among them) go
 * through the STR library overlay: open at 0x801D3538, then the field calls
 * the playback routine at 0x801D37CC and waits for the field's "movie
 * finished" word to become non-zero. */
#define XG_STR_OPEN         0x801D3538u
#define XG_STR_PLAY         0x801D37CCu
#define XG_FIELD_MOVIE_DONE 0x800ADB6Cu

#ifndef XG_DC_SKIP_FMV
#define XG_DC_SKIP_FMV 1   /* FMV (MDEC) is not ported yet */
#endif

/* What the movie scene does once its video ends: pick the next scene. The
 * runner that called it then starts that scene. */
static void movie_skipped(CPUState* cpu)
{
    dc_call_guest(cpu, XG_SCENE_SET, dc_read8(XG_NEXT_AFTER_MOVIE), 0, 0, 0);
}

/* Field movie skipped: mark it finished so the playback driver stops.
 * Known gap: after the new-game opening the field still waits on a black
 * screen; the hand-over from movie to field is not reproduced yet. */
static void field_movie_skipped(CPUState* cpu)
{
    (void)cpu;
    dc_write32(XG_FIELD_MOVIE_DONE, 1);
}

void dc_game_init(CPUState* cpu)
{
    (void)cpu;
    if (!XG_DC_SKIP_FMV) return;
    dc_overlay_skip_fn("movie-overlay", XG_MOVIE_ENTRY, movie_skipped);
    dc_overlay_skip("movie-str-lib-overlay", XG_STR_OPEN);
    dc_overlay_skip_fn("movie-str-lib-overlay", XG_STR_PLAY, field_movie_skipped);
}
