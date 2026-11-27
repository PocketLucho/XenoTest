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
#define XG_FIELD_MOVIE_BUSY 0x800B00E4u   /* field waits (VSync loop) while non-zero */
#define XG_FIELD_MOVIE_FRAME 0x800B06A0u  /* last frame reported by the player */
#define XG_FIELD_MOVIE_LEN  0x800C3A2Eu   /* halfword: frames in this movie */
#define XG_STR_CLOSE        0x801D43B0u

#ifndef XG_DC_SKIP_FMV
#define XG_DC_SKIP_FMV 0   /* 1: skip the movies */
#endif

/* What the movie scene does once its video ends: pick the next scene. The
 * runner that called it then starts that scene. */
static void movie_skipped(CPUState* cpu)
{
    dc_call_guest(cpu, XG_SCENE_SET, dc_read8(XG_NEXT_AFTER_MOVIE), 0, 0, 0);
}

/* Field movie skipped: mark it finished so the playback driver stops, and
 * clear the "playing" word that the field's movie routine spins on (the STR
 * library's end-of-movie callback clears it on a real run). */
static void field_movie_skipped(CPUState* cpu)
{
    (void)cpu;
    dc_write32(XG_FIELD_MOVIE_DONE, 1);
    dc_write32(XG_FIELD_MOVIE_BUSY, 0);
    /* the field leaves its movie loop once the reported frame reaches the
     * movie's length */
    dc_write32(XG_FIELD_MOVIE_FRAME, dc_read16(XG_FIELD_MOVIE_LEN));
}


/* The movie library's run-length (VLC) decoder, hand-written MIPS in the
 * STR overlay (two copies: tables at fixed addresses, or passed in a2).
 * As recompiled code it took most of a movie frame's time; this is the same
 * routine in C, step for step. It decodes the bitstream at a0 into run-length
 * codes at a1, stops after the size set by its companion routine (state kept
 * for the next call with a0 = 0, v0 = 1), and returns v0 = 0 at the end. */
#define XG_VLC1        0x801D4CC8u
#define XG_VLC1_SIZE   0x801D4C94u
#define XG_VLC1_STATE  0x801D5008u
#define XG_VLC2        0x801D5060u
#define XG_VLC2_SIZE   0x801D502Cu
#define XG_VLC2_STATE  0x801D539Cu

static inline uint16_t* vh(uint32_t a) { return (uint16_t*)(void*)dc_ram_ptr(a); }
static inline uint32_t vw(uint32_t a) { return *(uint32_t*)(void*)dc_ram_ptr(a); }

void dc_wait_cd_frame(CPUState* cpu);
static void vlc_run(CPUState* cpu, uint32_t sizeaddr, uint32_t st, uint32_t a2, uint32_t a3)
{
    /* The guest routine was slow enough for the frame's remaining sectors to
     * arrive while it ran; this one is not. */
    dc_wait_cd_frame(cpu);
    uint32_t a0 = cpu->gpr[4], a1 = cpu->gpr[5], v0, v1, t0, t1, t2, t3, t4, t5, t6, t7, t8, t9;
    t1 = vw(sizeaddr);
#define REFILL() do { if (v1 & 0x10u) { v1 &= 0xFu; v0 |= (uint32_t)*vh(a0) << v1; a0 += 2; } else v1 &= 0xFu; } while (0)
    if (a0 == 0) {
        uint32_t* s = (uint32_t*)(void*)dc_ram_ptr(st);
        a0 = s[0]; a1 = s[1]; v0 = s[2]; v1 = s[3]; t4 = s[4]; t5 = s[5]; t7 = s[6]; t8 = s[7]; t9 = s[8];
        t6 = a1 + t1 * 2u;
        goto next;
    }
    t5 = t7 = t8 = t9 = 0;
    t6 = a1 + t1 * 2u;
    t0 = *vh(a0); t1 = *vh(a0 + 2); t4 = (uint32_t)*vh(a0 + 4) << 10; t2 = *vh(a0 + 6);
    v0 = (uint32_t)*vh(a0 + 8) << 16 | *vh(a0 + 10);
    if ((int32_t)(t2 - 3u) >= 0) t5 = 1;
    a0 += 12; v1 = 0;
    *vh(a1) = (uint16_t)t0; *vh(a1 + 2) = (uint16_t)t1; a1 += 2;
block:
    t0 = v0 >> 22;
    if (t5 == 0) {
        a1 += 2;
        if (t0 == 0x1FFu) goto end;
        v0 <<= 10; v1 += 10; REFILL();
        *vh(a1) = (uint16_t)(t4 | t0);
    } else {
        a1 += 2;
        if (t0 == 0x3FFu) goto end;
        uint32_t tab = (int32_t)(t5 - 3u) < 0 ? a2 - 0x400u : a2 - 0x800u;
        uint32_t e = tab + ((v0 >> 24) << 2);
        t1 = *vh(e); t2 = *vh(e + 2); t0 = 0;
        v0 <<= (t1 & 31u);
        if (t2) {
            uint32_t sh = (32u - t2) & 31u;
            t0 = v0 >> sh;
            int neg = (int32_t)v0 < 0;
            v0 <<= (t2 & 31u);
            if (!neg) t0 -= 0xFFFFFFFFu >> sh;
            v1 += t2;
        }
        v1 += t1; REFILL();
        if ((int32_t)(t5 - 2u) > 0) { t1 = t9 + t0; t9 += t0; }
        else if (t5 == 2) { t1 = t8 + t0; t8 += t0; }
        else { t1 = t7 + t0; t7 += t0; }
        t1 = ((t1 << 2) & 0x3FFu) | t4;
        t5++;
        *vh(a1) = (uint16_t)t1;
        if (t5 == 7) t5 -= 6;
    }
    t3 = a1 - t6; a1 += 2;
    if ((int32_t)t3 >= 0) goto save;
next:
    for (;;) {
        uint32_t e = a2 + ((v0 >> 19) << 3), sh;
        t1 = vw(e);
        if (t1 == 0) {
            v0 <<= 8; v1 += 8; REFILL();
            e = a3 + ((v0 >> 23) << 2);
            t1 = vw(e); t3 = 0;
        } else t3 = vw(e + 4);
        sh = t1 & 0xFFu;
        v0 <<= (sh & 31u); v1 += sh; REFILL();
        t1 >>= 16;
        if (t1 == 0x7C1Fu) goto escape;
        *vh(a1) = (uint16_t)t1;
        if (t1 == 0xFE00u) goto block;
        a1 += 2;
        if (t3 == 0) continue;
        t2 = t3 & 0xFFFFu;
        if (t2 == 0x7C1Fu) goto escape;
        *vh(a1) = (uint16_t)t2;
        if (t2 == 0xFE00u) goto block;
        t2 = t3 >> 16; a1 += 2;
        if (t2 == 0) continue;
        if (t2 == 0x7C1Fu) goto escape;
        *vh(a1) = (uint16_t)t2;
        if (t2 == 0xFE00u) goto block;
        a1 += 2;
        continue;
escape:
        *vh(a1) = (uint16_t)(v0 >> 16); a1 += 2;
        t0 = *vh(a0); a0 += 2;
        v0 = (v0 << 16) | (t0 << (v1 & 31u));
    }
end:
    for (int i = 0; i < 65; i++) { *vh(a1) = 0xFE00u; a1 += 2; }
    cpu->cop0[12] |= 0x20000u;
    cpu->gpr[2] = 0;
    return;
save: {
        uint32_t* s = (uint32_t*)(void*)dc_ram_ptr(st);
        s[0] = a0; s[1] = a1; s[2] = v0; s[3] = v1; s[4] = t4; s[5] = t5; s[6] = t7; s[7] = t8; s[8] = t9;
        cpu->gpr[2] = 1;
    }
#undef REFILL
}

#ifdef XG_VLC_CHECK
/* Runs the guest routine and the C one on the same input and logs the first
 * call where they differ. */
#include <stdio.h>
#include <string.h>
extern int g_dc_hle_off;
void dc_host_log(const char* s);
static uint8_t s_ck[2][0x10000 + 64]; static uint8_t s_cst[2][40];
static void vlc_check(CPUState* cpu, uint32_t fn, uint32_t sizeaddr, uint32_t st, void (*mine)(CPUState*))
{
    static int bad, n;
    uint32_t a0 = cpu->gpr[4], a1 = cpu->gpr[5], a2 = cpu->gpr[6], a3 = cpu->gpr[7];
    uint32_t len = vw(sizeaddr) * 2u + 160u, dst = a0 ? a1 : vw(st + 4);
    if (len > 0x10000u) len = 0x10000u;
    static uint8_t save[0x10000 + 64], sst[40];
    memcpy(save, dc_ram_ptr(dst), len); memcpy(sst, dc_ram_ptr(st), 36);
    g_dc_hle_off = 1;
    uint32_t r0 = dc_call_guest(cpu, fn, a0, a1, a2, a3);
    g_dc_hle_off = 0;
    memcpy(s_ck[0], dc_ram_ptr(dst), len); memcpy(s_cst[0], dc_ram_ptr(st), 36);
    memcpy(dc_ram_ptr(dst), save, len); memcpy(dc_ram_ptr(st), sst, 36);
    cpu->gpr[4] = a0; cpu->gpr[5] = a1; cpu->gpr[6] = a2; cpu->gpr[7] = a3;
    mine(cpu);
    uint32_t r1 = cpu->gpr[2];
    memcpy(s_ck[1], dc_ram_ptr(dst), len); memcpy(s_cst[1], dc_ram_ptr(st), 36);
    n++;
    if (bad < 3 && (r0 != r1 || memcmp(s_ck[0], s_ck[1], len) || memcmp(s_cst[0], s_cst[1], 36))) {
        bad++; char b[160]; uint32_t i = 0; while (i < len && s_ck[0][i] == s_ck[1][i]) i++;
        snprintf(b, sizeof b, "[vlc] call %d differs: a0=%08X dst=%08X v0 %u/%u first byte diff at +%u of %u, state %s\n", n, (unsigned)a0, (unsigned)dst, (unsigned)r0, (unsigned)r1, (unsigned)i, (unsigned)len,
                 memcmp(s_cst[0], s_cst[1], 36) ? "differs" : "same");
        dc_host_log(b);
        for (int k = 0; k < 9; k++) { uint32_t x, y; memcpy(&x, s_cst[0] + k * 4, 4); memcpy(&y, s_cst[1] + k * 4, 4);
            snprintf(b, sizeof b, "[vlc]  st%d %08X %08X\n", k, (unsigned)x, (unsigned)y); dc_host_log(b); }
        if (i < len) { uint32_t o = i & ~15u; for (int k = 0; k < 3; k++, o += 16) { snprintf(b, sizeof b, "[vlc]  +%u g:%08X %08X %08X %08X c:%08X %08X %08X %08X\n", (unsigned)o,
            *(uint32_t*)(s_ck[0]+o), *(uint32_t*)(s_ck[0]+o+4), *(uint32_t*)(s_ck[0]+o+8), *(uint32_t*)(s_ck[0]+o+12),
            *(uint32_t*)(s_ck[1]+o), *(uint32_t*)(s_ck[1]+o+4), *(uint32_t*)(s_ck[1]+o+8), *(uint32_t*)(s_ck[1]+o+12)); dc_host_log(b); } }
    } else if (n % 500 == 0) { char b[64]; snprintf(b, sizeof b, "[vlc] %d calls checked, %d bad\n", n, bad); dc_host_log(b); }
}
static void vlc1_c(CPUState* cpu) { vlc_run(cpu, XG_VLC1_SIZE, XG_VLC1_STATE, 0x801D802Cu, 0x801E802Cu); }
static void vlc1(CPUState* cpu) { vlc_check(cpu, XG_VLC1, XG_VLC1_SIZE, XG_VLC1_STATE, vlc1_c); }
#else
static void vlc1(CPUState* cpu) { vlc_run(cpu, XG_VLC1_SIZE, XG_VLC1_STATE, 0x801D802Cu, 0x801E802Cu);
#ifdef XG_VLC_DELAY
  { uint64_t dc_host_ticks_us(void); uint64_t t = dc_host_ticks_us(); while (dc_host_ticks_us() - t < XG_VLC_DELAY) ; }
#endif
}
#endif
static void vlc2_c(CPUState* cpu)
{
    uint32_t a2 = cpu->gpr[6] + 0x800u;
    vlc_run(cpu, XG_VLC2_SIZE, XG_VLC2_STATE, a2, a2 + 0x10000u);
}
#ifdef XG_VLC_CHECK
static void vlc2(CPUState* cpu) { vlc_check(cpu, XG_VLC2, XG_VLC2_SIZE, XG_VLC2_STATE, vlc2_c); }
#else
static void vlc2(CPUState* cpu) { vlc2_c(cpu); }
#endif

void dc_game_init(CPUState* cpu)
{
    (void)cpu;
#ifndef XG_DC_NO_VLC_HLE
    dc_overlay_hle("movie-str-lib-overlay", XG_VLC1, vlc1);
    dc_overlay_hle("movie-str-lib-overlay", XG_VLC2, vlc2);
#endif
    if (!XG_DC_SKIP_FMV) return;
    dc_overlay_skip_fn("movie-overlay", XG_MOVIE_ENTRY, movie_skipped);
    dc_overlay_skip("movie-str-lib-overlay", XG_STR_OPEN);
    dc_overlay_skip_fn("movie-str-lib-overlay", XG_STR_PLAY, field_movie_skipped);
    dc_overlay_skip("movie-str-lib-overlay", XG_STR_CLOSE);
}
