#!/usr/bin/env python3
"""Minimal Godot .pck reader (pack format v1 = Godot 3, v2 = Godot 4.0-4.3,
v3 = Godot 4.4+ with a relocatable directory). Also finds a pack embedded at
the end of an exported executable. Used to list a build's resources so the
build can be matched to a commit."""
from __future__ import annotations

import struct
from pathlib import Path

MAGIC = 0x43504447  # "GDPC"


def _find_start(fh, size):
    fh.seek(0)
    if struct.unpack("<I", fh.read(4))[0] == MAGIC:
        return 0
    if size >= 12:  # embedded: [pack][uint64 pack_size]["GDPC"]
        fh.seek(size - 4)
        if struct.unpack("<I", fh.read(4))[0] == MAGIC:
            fh.seek(size - 12)
            psize = struct.unpack("<Q", fh.read(8))[0]
            start = size - 12 - psize
            if start >= 0:
                fh.seek(start)
                if struct.unpack("<I", fh.read(4))[0] == MAGIC:
                    return start
    return None


def read_pck(path, read_data_for=None, max_data=2_000_000):
    """Return {version, godot, files:[{path, offset, size, md5, data?}]} or {error}.
    `read_data_for(path)->bool` selects entries whose bytes should be returned."""
    p = Path(path)
    try:
        size = p.stat().st_size
        with open(p, "rb") as fh:
            start = _find_start(fh, size)
            if start is None:
                return {"error": "no Godot pack found"}
            fh.seek(start + 4)
            ver, major, minor, patch = struct.unpack("<IIII", fh.read(16))
            file_base, flags = 0, 0
            if ver == 1:
                fh.read(16 * 4)
            elif ver == 2:
                flags, file_base = struct.unpack("<IQ", fh.read(12))
                fh.read(16 * 4)
            elif ver == 3:
                flags, file_base, dir_offset = struct.unpack("<IQQ", fh.read(20))
                fh.read(16 * 4)
                fh.seek((start if flags & 2 else 0) + dir_offset)
            else:
                return {"error": f"unsupported pack version {ver}"}
            if flags & 1:
                return {"error": "pack directory is encrypted", "version": ver,
                        "godot": f"{major}.{minor}.{patch}"}
            rel_base = bool(flags & 2)
            (count,) = struct.unpack("<I", fh.read(4))
            files = []
            for _ in range(min(count, 200_000)):
                (plen,) = struct.unpack("<I", fh.read(4))
                path_s = fh.read(plen).rstrip(b"\0").decode("utf-8", "replace")
                off, fsize = struct.unpack("<QQ", fh.read(16))
                md5 = fh.read(16).hex()
                fflags = struct.unpack("<I", fh.read(4))[0] if ver >= 2 else 0
                if ver == 1:
                    abs_off = start + off
                else:
                    abs_off = (start if rel_base else 0) + file_base + off
                files.append({"path": path_s, "offset": abs_off, "size": fsize, "md5": md5,
                              "encrypted": bool(fflags & 1)})
            if read_data_for:
                for f in files:
                    if not f["encrypted"] and f["size"] <= max_data and read_data_for(f["path"]):
                        fh.seek(f["offset"])
                        f["data"] = fh.read(f["size"])
            return {"version": ver, "godot": f"{major}.{minor}.{patch}", "files": files}
    except (OSError, struct.error) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def source_paths(pck):
    """Map pack entries back to project source paths (res:// stripped)."""
    out = set()
    for f in pck.get("files", []):
        p = f["path"]
        if p.startswith("res://"):
            p = p[6:]
        if p.startswith((".godot/", ".import/")) or p in ("project.binary", "uid_cache.bin"):
            continue
        for suf in (".import", ".remap"):
            if p.endswith(suf):
                p = p[: -len(suf)]
        if p.endswith(".gdc"):
            p = p[:-1]
        out.add(p)
    return out
