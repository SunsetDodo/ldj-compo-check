#!/usr/bin/env python3
"""Archive helpers: detect type, list entries with their stored timestamps,
and extract safely (no absolute paths, no '..', no symlinks).

ZIP timestamps:
  * DOS time       – local time of the zipping machine, unknown time zone, 2 s resolution
  * 0x5455 "UT"    – Unix mtime (UTC); local header may also carry atime/ctime(creation)
  * 0x000a NTFS    – mtime/atime/ctime(creation) as Windows FILETIME (UTC)
  * 0x5855 "UX"    – old Info-ZIP Unix atime/mtime (UTC)
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import struct
import tarfile
import zipfile
from pathlib import Path

from common import UTC, run

FILETIME_EPOCH = dt.datetime(1601, 1, 1, tzinfo=UTC)


def archive_kind(path) -> str | None:
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if head[:7] == b"Rar!\x1a\x07\x00" or head[:8] == b"Rar!\x1a\x07\x01\x00":
        return "rar"
    if head[:2] == b"\x1f\x8b" or head[:3] == b"BZh" or head[:6] == b"\xfd7zXZ\x00":
        return "tar"
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "tar"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "zst"
    return None


def ensure_module(mod, pip_name=None):
    """Import an optional module, installing it with pip if missing."""
    import importlib
    try:
        return importlib.import_module(mod)
    except ImportError:
        run(["pip", "install", "--quiet", "--break-system-packages", pip_name or mod], timeout=300)
        try:
            return importlib.import_module(mod)
        except ImportError:
            return None


def _filetime(v):
    if not v:
        return None
    try:
        return FILETIME_EPOCH + dt.timedelta(microseconds=v / 10)
    except OverflowError:
        return None


def _parse_extra(extra: bytes):
    """Return dict with any of mtime/atime/ctime (UTC datetimes) and their source."""
    out = {}
    i = 0
    while i + 4 <= len(extra):
        hid, size = struct.unpack_from("<HH", extra, i)
        data = extra[i + 4:i + 4 + size]
        i += 4 + size
        try:
            if hid == 0x5455 and data:
                flags = data[0]
                j = 1
                for bit, key in ((1, "mtime"), (2, "atime"), (4, "ctime")):
                    if flags & bit and j + 4 <= len(data):
                        out.setdefault(key, (dt.datetime.fromtimestamp(struct.unpack_from("<i", data, j)[0], UTC), "zip-UT"))
                        j += 4
            elif hid == 0x000A and len(data) >= 4:
                j = 4
                while j + 4 <= len(data):
                    tag, tsize = struct.unpack_from("<HH", data, j)
                    if tag == 1 and tsize >= 24:
                        m, a, c = struct.unpack_from("<QQQ", data, j + 4)
                        for key, v in (("mtime", m), ("atime", a), ("ctime", c)):
                            t = _filetime(v)
                            if t:
                                out[key] = (t, "zip-NTFS")
                    j += 4 + tsize
            elif hid == 0x5855 and len(data) >= 8:
                a, m = struct.unpack_from("<ii", data, 0)
                out.setdefault("mtime", (dt.datetime.fromtimestamp(m, UTC), "zip-UX"))
                out.setdefault("atime", (dt.datetime.fromtimestamp(a, UTC), "zip-UX"))
        except (OverflowError, OSError, ValueError, struct.error):
            continue
    return out


def _local_extra(fh, zinfo):
    try:
        fh.seek(zinfo.header_offset)
        hdr = fh.read(30)
        if hdr[:4] != b"PK\x03\x04":
            return b""
        n, e = struct.unpack_from("<HH", hdr, 26)
        fh.seek(zinfo.header_offset + 30 + n)
        return fh.read(e)
    except (OSError, struct.error):
        return b""


def list_entries(path):
    """List archive members with timestamps. Each entry:
    {name, size, is_dir, mtime, mtime_src, ctime, ctime_src, tz_known}"""
    kind = archive_kind(path)
    entries = []
    if kind == "zip":
        with zipfile.ZipFile(path) as zf, open(path, "rb") as fh:
            for zi in zf.infolist():
                ts = _parse_extra(zi.extra)
                for k, v in _parse_extra(_local_extra(fh, zi)).items():
                    ts.setdefault(k, v)
                e = {"name": zi.filename, "size": zi.file_size, "is_dir": zi.is_dir()}
                if "mtime" in ts:
                    e["mtime"], e["mtime_src"], e["tz_known"] = ts["mtime"][0], ts["mtime"][1], True
                else:
                    try:
                        e["mtime"] = dt.datetime(*zi.date_time, tzinfo=UTC)
                    except ValueError:
                        e["mtime"] = None
                    e["mtime_src"], e["tz_known"] = "zip-DOS (local time, zone unknown)", False
                if "ctime" in ts:
                    e["ctime"], e["ctime_src"] = ts["ctime"][0], ts["ctime"][1]
                entries.append(e)
    elif kind == "tar":
        with tarfile.open(path) as tf:
            for ti in tf.getmembers():
                entries.append({"name": ti.name, "size": ti.size, "is_dir": ti.isdir(),
                                "mtime": dt.datetime.fromtimestamp(ti.mtime, UTC) if ti.mtime else None,
                                "mtime_src": "tar", "tz_known": True})
    elif kind == "7z":
        py7zr = ensure_module("py7zr")
        if py7zr is None:
            return None
        with py7zr.SevenZipFile(path, "r") as z:
            for f in z.files:
                e = {"name": f.filename, "size": getattr(f, "uncompressed", None),
                     "is_dir": f.is_directory, "tz_known": True}
                for attr, key in (("lastwritetime", "mtime"), ("creationtime", "ctime")):
                    v = getattr(f, attr, None)
                    if v is not None:
                        try:
                            t = v.as_datetime() if hasattr(v, "as_datetime") else \
                                dt.datetime.fromtimestamp(v.totimestamp(), UTC)
                            e[key] = t if t.tzinfo else t.replace(tzinfo=UTC)
                            e[f"{key}_src"] = "7z"
                        except Exception:  # noqa: BLE001
                            pass
                entries.append(e)
    elif kind == "rar":
        rarfile = ensure_module("rarfile")
        if rarfile is None:
            return None
        with rarfile.RarFile(path) as rf:
            for ri in rf.infolist():
                e = {"name": ri.filename, "size": ri.file_size, "is_dir": ri.is_dir(),
                     "mtime": getattr(ri, "mtime", None) or (dt.datetime(*ri.date_time) if ri.date_time else None),
                     "mtime_src": "rar", "tz_known": bool(getattr(ri, "mtime", None) and ri.mtime.tzinfo)}
                if e["mtime"] and not e["mtime"].tzinfo:
                    e["mtime"] = e["mtime"].replace(tzinfo=UTC)
                if getattr(ri, "ctime", None):
                    e["ctime"], e["ctime_src"] = ri.ctime, "rar"
                entries.append(e)
    else:
        return None
    return entries


def _safe_target(dest: Path, name: str):
    name = name.replace("\\", "/")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or name.startswith("/") or ":" in parts[0]:
        return None
    return dest.joinpath(*parts)


def extract(path, dest) -> tuple[bool, str]:
    """Extract an archive into dest. Returns (ok, message)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    kind = archive_kind(path)
    try:
        if kind == "zip":
            with zipfile.ZipFile(path) as zf:
                for zi in zf.infolist():
                    tgt = _safe_target(dest, zi.filename)
                    if tgt is None or (zi.external_attr >> 16) & 0o170000 == 0o120000:
                        continue
                    if zi.is_dir():
                        tgt.mkdir(parents=True, exist_ok=True)
                        continue
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(zi) as src, open(tgt, "wb") as out:
                        shutil.copyfileobj(src, out)
            return True, "zip"
        if kind == "tar":
            with tarfile.open(path) as tf:
                for ti in tf.getmembers():
                    tgt = _safe_target(dest, ti.name)
                    if tgt is None or ti.issym() or ti.islnk() or ti.isdev():
                        continue
                    if ti.isdir():
                        tgt.mkdir(parents=True, exist_ok=True)
                        continue
                    src = tf.extractfile(ti)
                    if src is None:
                        continue
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    with open(tgt, "wb") as out:
                        shutil.copyfileobj(src, out)
            return True, "tar"
        if kind == "7z":
            py7zr = ensure_module("py7zr")
            if py7zr is None:
                return _extract_with_tools(path, dest, "py7zr not installed")
            with py7zr.SevenZipFile(path, "r") as z:
                names = [n for n in z.getnames() if _safe_target(dest, n) is not None]
                z.extract(path=dest, targets=names)
            return True, "7z"
        if kind == "rar":
            return _extract_with_tools(path, dest, "rar")
        if kind == "zst":
            return False, "zstd-compressed file (not an archive this tool extracts)"
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
        return False, f"{kind or 'unknown'} extraction failed: {exc}"
    return False, "not a recognised archive"


def _extract_with_tools(path, dest, why):
    for cmd in (["bsdtar", "-xf", str(path), "-C", str(dest)],
                ["unar", "-q", "-o", str(dest), str(path)],
                ["7z", "x", "-y", f"-o{dest}", str(path)],
                ["unrar", "x", "-o+", str(path), str(dest) + os.sep]):
        if shutil.which(cmd[0]):
            r = run(cmd, timeout=900)
            if r.returncode == 0:
                return True, cmd[0]
    return False, f"no extractor available for this archive ({why}); ask for a .zip instead"


def maybe_extract_nested(dest: Path, depth=2):
    """If an extraction produced a single archive (optionally inside one folder),
    extract that too."""
    for _ in range(depth):
        files = [p for p in dest.rglob("*") if p.is_file()]
        if len(files) != 1 or archive_kind(files[0]) is None:
            return
        inner = files[0]
        target = inner.parent / (inner.name + "_extracted")
        ok, _ = extract(inner, target)
        if not ok:
            return
        dest = target
