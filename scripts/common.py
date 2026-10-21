#!/usr/bin/env python3
"""Shared helpers for the ldjam-compo-check scripts.

Only the standard library is required here; `requests` is imported lazily
for network calls so offline stages still run without it.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

UTC = _dt.timezone.utc
USER_AGENT = "Mozilla/5.0 (compatible; ldjam-compo-check/1.0; rules review)"
API_BASE = "https://api.ldjam.com/vx"


# --------------------------------------------------------------------------- time
def parse_time(value):
    """Parse ISO strings, 'YYYY-MM-DD HH:MM:SS', EXIF 'YYYY:MM:DD HH:MM:SS',
    epoch seconds/ms (int, float or digit string). Naive results are UTC."""
    if value is None or value == "" or value == 0:
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:
            v /= 1000.0
        try:
            return _dt.datetime.fromtimestamp(v, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if re.fullmatch(r"\d{9,13}(\.\d+)?", s):
        return parse_time(float(s))
    m = re.fullmatch(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(.*)", s)
    if m:  # EXIF style
        s = f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}{m[7]}"
    if re.match(r"\d{4}-\d{2}-\d{2} \d", s):
        s = s.replace(" ", "T", 1)
    s = re.sub(r"Z$", "+00:00", s)
    s = re.sub(r" ?UTC$", "+00:00", s)
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        t = _dt.datetime.fromisoformat(s)
    except ValueError:
        t = None
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d",
                    "%d %B %Y @ %H:%M%z", "%d %b %Y @ %H:%M%z"):
            try:
                t = _dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if t is None:
            return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def iso(t):
    t = parse_time(t)
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z") if t else None


def fmt_time(t):
    t = parse_time(t)
    return t.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if t else "unknown"


def hours_between(a, b):
    a, b = parse_time(a), parse_time(b)
    if not a or not b:
        return None
    return (b - a).total_seconds() / 3600.0


class Window:
    """The Ludum Dare timing model: Compo = 48h, then a 1h Submission Hour."""

    def __init__(self, start, compo_hours=48.0, submission_hours=1.0, jam_hours=72.0,
                 compo_end=None, submission_end=None):
        self.start = parse_time(start)
        if not self.start:
            raise ValueError("event start time is required")
        self.compo_end = parse_time(compo_end) or self.start + _dt.timedelta(hours=compo_hours)
        self.submission_end = parse_time(submission_end) or self.compo_end + _dt.timedelta(hours=submission_hours)
        self.jam_end = self.start + _dt.timedelta(hours=jam_hours)

    @classmethod
    def from_game(cls, game, start=None, compo_end=None, submission_end=None):
        ev = (game or {}).get("event") or {}
        return cls(start or ev.get("event_start"),
                   compo_end=compo_end or ev.get("compo_end"),
                   submission_end=submission_end or ev.get("submission_end"))

    def phase(self, t):
        """before / during / submission_hour / after / None (unknown)."""
        t = parse_time(t)
        if t is None:
            return None
        if t < self.start:
            return "before"
        if t <= self.compo_end:
            return "during"
        if t <= self.submission_end:
            return "submission_hour"
        return "after"

    def to_dict(self):
        return {"start": iso(self.start), "compo_end": iso(self.compo_end),
                "submission_end": iso(self.submission_end), "jam_end": iso(self.jam_end)}

    def describe(self):
        return (f"{fmt_time(self.start)} → {fmt_time(self.compo_end)} "
                f"(Submission Hour until {self.submission_end.strftime('%H:%M UTC')})")


# --------------------------------------------------------------------------- io
def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)


def read_text(path, limit=None):
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit) if limit else fh.read()
    except OSError:
        return ""
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            if enc == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def is_probably_text(data: bytes) -> bool:
    if not data:
        return True
    if b"\x00" in data[:8192]:
        return data[:2] in (b"\xff\xfe", b"\xfe\xff")
    sample = data[:8192]
    bad = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return bad / max(1, len(sample)) < 0.05


def human_int(n):
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def pct(a, b):
    return f"{(100.0 * a / b):.0f}%" if b else "–"


def log(*args):
    print(*args, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- hashing
def git_blob_sha(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def git_blob_shas_tolerant(data: bytes):
    """Blob SHA of the bytes as-is plus CRLF->LF normalised variant (autocrlf)."""
    shas = {git_blob_sha(data)}
    if b"\r\n" in data and is_probably_text(data):
        shas.add(git_blob_sha(data.replace(b"\r\n", b"\n")))
    return shas


# --------------------------------------------------------------------------- processes
def run(cmd, cwd=None, timeout=600, env=None, check=False, text=True):
    e = dict(os.environ)
    e.setdefault("GIT_TERMINAL_PROMPT", "0")
    if env:
        e.update(env)
    try:
        p = subprocess.run(cmd, cwd=cwd, timeout=timeout, env=e, capture_output=True,
                           text=text, errors="replace" if text else None)
    except subprocess.TimeoutExpired:
        if check:
            raise
        return subprocess.CompletedProcess(cmd, 124, "", f"timeout after {timeout}s")
    except FileNotFoundError as exc:
        if check:
            raise
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))} failed: {p.stderr.strip()[:500]}")
    return p


def git(repo, *args, timeout=600, check=False):
    return run(["git", "-c", "core.quotepath=off", "-C", str(repo), *args], timeout=timeout, check=check)


# --------------------------------------------------------------------------- network
class NetworkBlocked(Exception):
    """The host could not be reached (sandbox egress policy, DNS, proxy refusal)."""


def http_session():
    import requests  # lazy
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if not ca and os.path.exists("/root/.ccr/ca-bundle.crt"):
        ca = "/root/.ccr/ca-bundle.crt"
    if ca:
        s.verify = ca
    return s


def http_get(session, url, timeout=30, **kw):
    import requests
    try:
        r = session.get(url, timeout=timeout, **kw)
    except (requests.exceptions.ProxyError, requests.exceptions.SSLError,
            requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        raise NetworkBlocked(f"{url}: {type(exc).__name__}: {str(exc)[:200]}") from exc
    return r


def http_post(session, url, timeout=30, **kw):
    import requests
    try:
        r = session.post(url, timeout=timeout, **kw)
    except (requests.exceptions.ProxyError, requests.exceptions.SSLError,
            requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        raise NetworkBlocked(f"{url}: {type(exc).__name__}: {str(exc)[:200]}") from exc
    return r


def download(session, url, dest, max_bytes=2_000_000_000, timeout=120, **kw):
    """Stream a URL to `dest`. Returns (path, content_type, final_url)."""
    import requests
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    try:
        with session.get(url, stream=True, timeout=timeout, allow_redirects=True, **kw) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code} for {url}")
            total = 0
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    total += len(chunk)
                    if total > max_bytes:
                        raise RuntimeError(f"download exceeds {max_bytes} bytes")
                    fh.write(chunk)
            return dest, r.headers.get("content-type", ""), r.url
    except (requests.exceptions.ProxyError, requests.exceptions.SSLError,
            requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        raise NetworkBlocked(f"{url}: {type(exc).__name__}: {str(exc)[:200]}") from exc


URL_RE = re.compile(r"""(?:https?://|www\.)[^\s<>"'`)\]]+""", re.I)


def extract_urls(text):
    if not text:
        return []
    out = []
    for m in URL_RE.finditer(text):
        u = m.group(0).rstrip(".,;:!?*_")
        if u.lower().startswith("www."):
            u = "https://" + u
        if u not in out:
            out.append(u)
    return out


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "game"
