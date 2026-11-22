#!/usr/bin/env python3
"""Make a Dreamcast GD-ROM image (.gdi) of the port with your own disc.

For emulators (Flycast, Redream...) and optical-drive emulators on real
hardware (GDEMU, MODE...). Run it on your machine; the result contains your
own copy of the game, so do not share it.

Layout: track 1 (small data track), track 2 (short silent audio track) and
track 3 at LBA 45000 holding an ISO9660 filesystem with the Dreamcast boot
sector (IP.BIN) and:
  1ST_READ.BIN   the port (plain binary of xg_dc.elf; GD-ROMs are not scrambled)
  PSX.EXE        the game's main executable (from your disc)
  DISC.BIN       your raw 2352-byte disc 1 image (read by the CD emulation)
  OV/OV_nn.BIN   compiled overlays (from link_kos.py)

Only the Python standard library is needed.

  python make_gdi.py --build kosout --exe SLUS_006.64 --disc disc1.bin --out gdi
"""
import argparse, shutil, struct, time
from pathlib import Path

SECTOR = 2048


def both16(v): return struct.pack("<H", v) + struct.pack(">H", v)
def both32(v): return struct.pack("<I", v) + struct.pack(">I", v)


def dir_date(t):
    return bytes([t.tm_year - 1900, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, 0])


def vol_date(t):
    return time.strftime("%Y%m%d%H%M%S00", t).encode() + b"\x00"


def dir_record(name: bytes, lba, size, is_dir, t):
    rec = bytearray(33 + len(name))
    rec[0] = len(rec) + (len(rec) & 1)
    rec[2:10] = both32(lba)
    rec[10:18] = both32(size)
    rec[18:25] = dir_date(t)
    rec[25] = 2 if is_dir else 0
    rec[28:32] = both16(1)
    rec[32] = len(name)
    rec[33:] = name
    if len(rec) & 1:
        rec += b"\x00"
    return bytes(rec)


def sectors(n): return (n + SECTOR - 1) // SECTOR


class Iso:
    """Minimal ISO9660 writer: a root directory and at most one level of
    subdirectories, level-1 names, LBAs offset by `base` (the track start)."""

    def __init__(self, base=0, boot=b"", volume="XENOTEST"):
        self.base, self.boot, self.volume = base, boot, volume
        self.dirs = {"": []}            # dir name -> [(name, source path or bytes)]

    def add(self, path: str, src):
        d, _, name = path.rpartition("/")
        self.dirs.setdefault(d, []).append((name.upper(), src))

    @staticmethod
    def size_of(src): return len(src) if isinstance(src, (bytes, bytearray)) else Path(src).stat().st_size

    def write(self, out: Path):
        t = time.gmtime()
        subdirs = [d for d in self.dirs if d]
        # sector map: 0-15 system area, 16 PVD, 17 terminator, 18/19 path tables,
        # then one sector per directory, then file data
        lba = 20
        dir_lba = {"": lba}
        lba += 1
        for d in subdirs:
            dir_lba[d] = lba
            lba += 1
        files = []
        for d in [""] + subdirs:
            for name, src in self.dirs[d]:
                n = self.size_of(src)
                files.append((d, name, src, lba, n))
                lba += max(1, sectors(n))
        total = lba

        def dir_bytes(d):
            parent = ""
            recs = [dir_record(b"\x00", self.base + dir_lba[d], SECTOR, True, t),
                    dir_record(b"\x01", self.base + dir_lba[parent], SECTOR, True, t)]
            if d == "":
                recs += [dir_record(s.encode(), self.base + dir_lba[s], SECTOR, True, t) for s in sorted(subdirs)]
            for fd, name, src, flba, n in files:
                if fd == d:
                    recs.append(dir_record(f"{name};1".encode(), self.base + flba, n, False, t))
            data = b"".join(recs)
            assert len(data) <= SECTOR, "directory larger than one sector"
            return data.ljust(SECTOR, b"\x00")

        def path_table(big):
            p32 = (lambda v: struct.pack(">I", v)) if big else (lambda v: struct.pack("<I", v))
            p16 = (lambda v: struct.pack(">H", v)) if big else (lambda v: struct.pack("<H", v))
            out = bytearray(b"\x01\x00" + p32(self.base + dir_lba[""]) + p16(1) + b"\x00\x00")
            for s in sorted(subdirs):
                nm = s.encode()
                out += bytes([len(nm), 0]) + p32(self.base + dir_lba[s]) + p16(1) + nm + (b"\x00" if len(nm) & 1 else b"")
            return bytes(out)

        lpt, mpt = path_table(False), path_table(True)
        pvd = bytearray(SECTOR)
        pvd[0:7] = b"\x01CD001\x01"
        pvd[8:40] = b"SEGA SEGAKATANA".ljust(32)
        pvd[40:72] = self.volume.encode().ljust(32)
        pvd[80:88] = both32(self.base + total)
        pvd[120:124] = both16(1)
        pvd[124:128] = both16(1)
        pvd[128:132] = both16(SECTOR)
        pvd[132:140] = both32(len(lpt))
        pvd[140:144] = struct.pack("<I", self.base + 18)
        pvd[148:152] = struct.pack(">I", self.base + 19)
        pvd[156:190] = dir_record(b"\x00", self.base + dir_lba[""], SECTOR, True, t)
        for off, ln in ((190, 128), (318, 128), (446, 128), (574, 128), (702, 37), (739, 37), (776, 37)):
            pvd[off:off + ln] = b" " * ln
        pvd[813:830] = vol_date(t)
        pvd[830:847] = vol_date(t)
        pvd[847:864] = b"0" * 16 + b"\x00"
        pvd[864:881] = b"0" * 16 + b"\x00"
        pvd[881] = 1
        term = bytearray(SECTOR)
        term[0:7] = b"\xffCD001\x01"

        with open(out, "wb") as f:
            f.write(self.boot.ljust(16 * SECTOR, b"\x00")[:16 * SECTOR])
            f.write(pvd)
            f.write(term)
            f.write(lpt.ljust(SECTOR, b"\x00"))
            f.write(mpt.ljust(SECTOR, b"\x00"))
            for d in [""] + subdirs:
                f.write(dir_bytes(d))
            for _, _, src, flba, n in files:
                assert f.tell() == flba * SECTOR
                if isinstance(src, (bytes, bytearray)):
                    f.write(src)
                else:
                    with open(src, "rb") as s:
                        shutil.copyfileobj(s, f, 1 << 20)
                pad = max(1, sectors(n)) * SECTOR - n
                f.write(b"\x00" * pad)
        return total


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build", type=Path, required=True, help="link_kos.py output (IP.BIN, 1ST_READ.BIN, OV/)")
    ap.add_argument("--exe", type=Path, required=True, help="the game's main EXE from your disc 1")
    ap.add_argument("--disc", type=Path, required=True, help="your raw disc 1 image (.bin, 2352-byte sectors)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.disc.stat().st_size % 2352:
        raise SystemExit("disc image is not raw 2352-byte sectors")

    t1 = Iso(volume="XENOTEST")
    t1.add("README.TXT", b"Xenogears Dreamcast port test. The game is in the high-density area.\r\n")
    n1 = t1.write(a.out / "track01.iso")
    n1_pad = max(n1, 300)
    with open(a.out / "track01.iso", "ab") as f:
        f.write(b"\x00" * (n1_pad - n1) * SECTOR)
    t2_lba = n1_pad + 150
    (a.out / "track02.raw").write_bytes(b"\x00" * 2352 * 302)

    t3 = Iso(base=45000, boot=(a.build / "IP.BIN").read_bytes(), volume="XENOTEST")
    t3.add("1ST_READ.BIN", a.build / "1ST_READ.BIN")
    t3.add("PSX.EXE", a.exe)
    for ov in sorted((a.build / "OV").glob("OV_*.BIN")):
        t3.add(f"OV/{ov.name}", ov)
    t3.add("DISC.BIN", a.disc)
    n3 = t3.write(a.out / "track03.iso")
    if 45000 + n3 > 549150:
        raise SystemExit(f"track 3 is {n3} sectors, more than a GD-ROM holds")
    (a.out / "xenotest.gdi").write_text(
        f"3\n1 0 4 2048 track01.iso 0\n2 {t2_lba} 0 2352 track02.raw 0\n3 45000 4 2048 track03.iso 0\n")
    print(f"{a.out / 'xenotest.gdi'}: track 3 {n3 * SECTOR / 1048576:.0f} MB")


if __name__ == "__main__":
    main()
