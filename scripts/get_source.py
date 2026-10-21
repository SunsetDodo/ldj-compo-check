#!/usr/bin/env python3
"""Stage 1 – find and retrieve the source code (and, if possible, the played build).

Reads <workdir>/game.json (from ldjam_page.py) and writes <workdir>/stage1.json
with one of three statuses:

  SOURCE_FOUND          source retrieved into <workdir>/source/
  FAILED_TO_RETRIEVE    a source location exists (or may exist) but could not be
                        downloaded here -> ask the user for the files
  NO_SOURCE_AVAILABLE   no source link, no source upload, nothing in the description

Sources handled: GitHub/GitLab/Codeberg/Bitbucket/sourcehut git repos (full
history, so the Compo-deadline commit can be found), direct archive links,
Google Drive / Dropbox links, and itch.io pages (source uploads are detected by
name; downloads use ITCH_API_KEY when set, otherwise itch's public download flow).

The played build (itch.io web build or a downloadable build, preferring uploads
labelled as the Compo/Jam version over post-jam versions) is fetched into
<workdir>/build/ so stage 2 can match it to a commit.

Usage:
  get_source.py --workdir DIR [--local PATH ...] [--url URL ...]
                [--itch-manual itch.json] [--no-build] [--build-local PATH]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archives import archive_kind, extract, maybe_extract_nested  # noqa: E402
from common import (NetworkBlocked, download, git, http_get,  # noqa: E402
                    http_post, http_session, load_json, parse_time, iso, run,
                    save_json)

GIT_HOST_RE = re.compile(r"^https?://(www\.)?(github\.com|gitlab\.com|codeberg\.org|bitbucket\.org|"
                         r"git\.sr\.ht|gitea\.com|framagit\.org|notabug\.org|gitee\.com|"
                         r"gist\.github\.com)/", re.I)
ARCHIVE_EXT_RE = re.compile(r"\.(zip|rar|7z|tar\.gz|tgz|tar\.xz|tar|love|sb3|yyz|p8|p8\.png)(\?|#|$)", re.I)
SOURCE_WORD_RE = re.compile(r"(source|src|sources|code|repo|repository|project[ _-]?files?|"
                            r"unity[ _-]?project|godot[ _-]?project|\bproject\b)", re.I)
POST_VERSION_RE = re.compile(r"(post[ _-]?(jam|compo|ld|ludum|event|deadline|release)|updated?|"
                             r"\bv\d+\.\d+|patch(ed)?|fixed|extended|remaster|deluxe|director|"
                             r"after[ _-]?(jam|compo)|polish(ed)?|improved|new[ _-]?version)", re.I)
COMPO_VERSION_RE = re.compile(r"(compo|jam[ _-]?version|\bld[ _-]?\d{2}\b|ludum|original|"
                              r"submission|submitted|48[ _-]?h|event[ _-]?version|rated)", re.I)
MANUAL_HOSTS = {"mega.nz": "MEGA", "mega.co.nz": "MEGA", "mediafire.com": "MediaFire",
                "onedrive.live.com": "OneDrive", "1drv.ms": "OneDrive", "sharepoint.com": "OneDrive",
                "gamejolt.com": "Game Jolt", "newgrounds.com": "Newgrounds", "box.com": "Box",
                "icloud.com": "iCloud", "pcloud.link": "pCloud", "wetransfer.com": "WeTransfer"}
PLATFORM_SOURCE = {"puzzlescript.net": "PuzzleScript (source is a GitHub gist)",
                   "scratch.mit.edu": "Scratch (project is its source)",
                   "lexaloffle.com": "PICO-8 BBS cart (cart contains the source)",
                   "philome.la": "Twine (HTML contains the story source)",
                   "bitsy.org": "Bitsy (HTML contains the source)"}


# ---------------------------------------------------------------- classification
def classify_url(url: str, label="", tags=(), origin="ldjam-link"):
    u = url.strip()
    low = u.lower()
    host = urlparse(u if "://" in u else "https://" + u).netloc.lower().removeprefix("www.")
    tagged_source = "source-code" in tags
    named_source = bool(SOURCE_WORD_RE.search(label or ""))
    c = {"url": u, "label": label, "tags": list(tags), "origin": origin, "kind": "other", "score": 0}
    path_segs = [s for s in urlparse(u if "://" in u else "https://" + u).path.split("/") if s]
    if GIT_HOST_RE.match(low if "://" in low else "https://" + low) and \
            (len(path_segs) >= 2 or "gist.github.com" in host):
        if "github.com" in host and "/releases/download/" in low:
            c["kind"] = "archive"
        elif "github.com" in host and re.search(r"/archive/.+\.(zip|tar\.gz)$", low):
            c["kind"] = "git"
            c["url"] = re.sub(r"/archive/.*$", "", u)
        else:
            c["kind"] = "git"
        c["score"] = 3
    elif host.endswith("itch.io") and not host.startswith("itch.io"):
        c["kind"] = "itch"
        c["score"] = 1
    elif host in ("itch.io",) and "/jam/" in low:
        c["kind"] = "other"
    elif "drive.google.com" in host or "docs.google.com" in host:
        c["kind"] = "gdrive"
        c["score"] = 1
    elif "dropbox.com" in host or "dropboxusercontent.com" in host:
        c["kind"] = "dropbox"
        c["score"] = 1
    elif any(host.endswith(h) for h in MANUAL_HOSTS):
        c["kind"] = "manual_host"
        c["host_name"] = next(v for h, v in MANUAL_HOSTS.items() if host.endswith(h))
        c["score"] = 1
    elif any(host.endswith(h) for h in PLATFORM_SOURCE):
        c["kind"] = "platform"
        c["platform"] = next(v for h, v in PLATFORM_SOURCE.items() if host.endswith(h))
        c["score"] = 2
    elif ARCHIVE_EXT_RE.search(low):
        c["kind"] = "archive"
        c["score"] = 1
    if tagged_source:
        c["score"] += 3
    if named_source:
        c["score"] += 2
    if c["kind"] == "archive" and SOURCE_WORD_RE.search(unquote(urlparse(u).path)):
        c["score"] += 2
    if origin != "ldjam-link" and c["kind"] in ("other",):
        c["score"] = 0
    if tagged_source and c["kind"] == "other":
        c["kind"] = "manual_host"   # a tagged source link we can't classify: let the user fetch it
        c["host_name"] = host
    return c


# ---------------------------------------------------------------- git
def parse_git_url(url):
    """-> (clone_url, ref_and_path or None, display)"""
    u = url.split("#")[0].split("?")[0].rstrip("/")
    m = re.match(r"https?://(www\.)?github\.com/([^/]+)/([^/]+)(?:/(tree|blob|commit|releases/tag|tags)/(.+))?", u, re.I)
    if m:
        owner, repo = m.group(2), re.sub(r"\.git$", "", m.group(3))
        rest = m.group(5) if m.group(4) else None
        if m.group(4) == "commit" and rest:
            rest = rest.split("/")[0]
        return f"https://github.com/{owner}/{repo}.git", rest, f"github.com/{owner}/{repo}"
    m = re.match(r"https?://gist\.github\.com/(?:[^/]+/)?([0-9a-f]+)", u, re.I)
    if m:
        return f"https://gist.github.com/{m.group(1)}.git", None, f"gist {m.group(1)}"
    m = re.match(r"https?://(www\.)?gitlab\.com/(.+?)(?:/-/(?:tree|blob|commit)/(.+))?$", u, re.I)
    if m:
        path = re.sub(r"\.git$", "", m.group(2))
        return f"https://gitlab.com/{path}.git", m.group(3), f"gitlab.com/{path}"
    m = re.match(r"https?://(codeberg\.org|gitea\.com|framagit\.org|notabug\.org|gitee\.com)/([^/]+)/([^/]+)(?:/(?:src|tree)/(?:branch/|commit/|tag/)?(.+))?", u, re.I)
    if m:
        repo = re.sub(r"\.git$", "", m.group(3))
        return f"https://{m.group(1)}/{m.group(2)}/{repo}.git", m.group(4), f"{m.group(1)}/{m.group(2)}/{repo}"
    m = re.match(r"https?://bitbucket\.org/([^/]+)/([^/]+)(?:/src/(.+))?", u, re.I)
    if m:
        repo = re.sub(r"\.git$", "", m.group(2))
        return f"https://bitbucket.org/{m.group(1)}/{repo}.git", m.group(3), f"bitbucket.org/{m.group(1)}/{repo}"
    m = re.match(r"https?://git\.sr\.ht/(~[^/]+)/([^/]+)(?:/tree/(.+))?", u, re.I)
    if m:
        return f"https://git.sr.ht/{m.group(1)}/{m.group(2)}", m.group(3), f"git.sr.ht/{m.group(1)}/{m.group(2)}"
    return u if u.endswith(".git") else u + ".git", None, u


def resolve_ref_path(repo, rest):
    """Split 'branch/with/slashes/sub/dir' into (ref, subpath) using the repo's refs."""
    if not rest:
        return None, None
    segs = [s for s in rest.split("/") if s]
    refs = set()
    r = git(repo, "for-each-ref", "--format=%(refname:short)")
    for line in r.stdout.splitlines():
        refs.add(line.strip())
        refs.add(line.strip().removeprefix("origin/"))
    for i in range(len(segs), 0, -1):
        cand = "/".join(segs[:i])
        if cand in refs or re.fullmatch(r"[0-9a-f]{7,40}", cand):
            if git(repo, "rev-parse", "--verify", "--quiet", cand + "^{commit}").returncode == 0:
                return cand, "/".join(segs[i:]) or None
            if git(repo, "rev-parse", "--verify", "--quiet", "origin/" + cand + "^{commit}").returncode == 0:
                return "origin/" + cand, "/".join(segs[i:]) or None
    return None, None


def clone_git(cand, dest: Path, with_lfs=False):
    clone_url, rest, display = parse_git_url(cand["url"])
    if dest.exists():
        shutil.rmtree(dest)
    env = {"GIT_LFS_SKIP_SMUDGE": "0" if with_lfs else "1"}
    r = run(["git", "clone", "--quiet", clone_url, str(dest)], timeout=1200, env=env)
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        low = err.lower()
        if "not found" in low or "could not read username" in low or "authentication" in low \
                or "403" in low and "github.com" in clone_url:
            why = "repository not found or private"
        elif "timeout" in low:
            why = "clone timed out (repository too large?)"
        elif "proxy" in low or "could not resolve host" in low or "connect" in low:
            why = "host not reachable from this environment"
        else:
            why = "git clone failed"
        return {"ok": False, "error": f"{why}: {err[-300:]}", "reason_code": why}
    run(["git", "-C", str(dest), "fetch", "--quiet", "--tags", "origin"], timeout=300)
    ref, subpath = resolve_ref_path(dest, rest) if rest else (None, None)
    if rest and ref is None:
        # a /blob/<ref>/<file> link that we could not split: keep whole repo
        subpath = None
    head = git(dest, "rev-parse", "HEAD").stdout.strip()
    branch = git(dest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if subpath and not (dest / subpath).exists() and ref:
        # path may only exist on that ref
        pass
    if subpath and "." in subpath.split("/")[-1] and ref:
        # link to a single file: analyse its whole repo
        subpath = "/".join(subpath.split("/")[:-1]) or None
    ga = dest / ".gitattributes"
    lfs = ga.exists() and "filter=lfs" in ga.read_text(errors="replace")
    return {"ok": True, "method": "git", "url": cand["url"], "clone_url": clone_url,
            "display": display, "repo_path": str(dest), "path": str(dest), "ref": ref or branch or "HEAD",
            "linked_ref": ref, "subpath": subpath, "head": head, "default_branch": branch,
            "uses_lfs": lfs}


# ---------------------------------------------------------------- downloads
def gdrive_direct(url):
    m = re.search(r"/file/d/([\w-]+)", url) or re.search(r"[?&]id=([\w-]+)", url)
    return f"https://drive.google.com/uc?export=download&id={m.group(1)}" if m else None


def dropbox_direct(url):
    if "dl=0" in url:
        return url.replace("dl=0", "dl=1")
    return url + ("&" if "?" in url else "?") + "dl=1"


def fetch_archive(session, url, dl_dir: Path, ex_dir: Path, name_hint=None):
    name = name_hint or unquote(Path(urlparse(url).path).name) or "download"
    name = re.sub(r"[^\w.\-]+", "_", name)[:120] or "download"
    dest = dl_dir / name
    try:
        path, ctype, final = download(session, url, dest)
    except NetworkBlocked as exc:
        return {"ok": False, "error": f"host not reachable from this environment ({exc})",
                "reason_code": "network"}
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc), "reason_code": "http"}
    kind = archive_kind(path)
    if kind is None:
        head = Path(path).read_bytes()[:2000].lower()
        if b"<html" in head or b"<!doctype html" in head:
            # Google Drive large-file confirmation page
            if "drive.google" in url or "drive.usercontent" in final:
                m = re.search(rb'action="([^"]+)"', head + Path(path).read_bytes()[2000:200000].lower())
                if m:
                    form = Path(path).read_text(errors="replace")
                    inputs = dict(re.findall(r'name="(\w+)" value="([^"]*)"', form))
                    action = html.unescape(m.group(1).decode())
                    q = "&".join(f"{k}={v}" for k, v in inputs.items())
                    return fetch_archive(session, f"{action}?{q}", dl_dir, ex_dir, name_hint)
            return {"ok": False, "error": "got a web page instead of a file (login, confirmation "
                    "or a landing page)", "reason_code": "html"}
        if Path(path).suffix.lower() in (".p8", ".sb3"):
            return {"ok": True, "method": "file", "url": url, "archive_path": str(path),
                    "path": str(Path(path).parent)}
        return {"ok": False, "error": f"downloaded file is not a recognised archive ({ctype})",
                "reason_code": "not_archive", "archive_path": str(path)}
    target = ex_dir / Path(name).stem
    ok, msg = extract(path, target)
    if not ok:
        return {"ok": False, "error": msg, "reason_code": "extract", "archive_path": str(path)}
    maybe_extract_nested(target)
    return {"ok": True, "method": "archive", "url": url, "archive_path": str(path),
            "archive_kind": kind, "path": str(target)}


PROJECT_MARKERS = re.compile(r"(^|/)(project\.godot|ProjectSettings/ProjectVersion\.txt|[^/]+\.yyp|"
                             r"game\.project|[^/]+\.uproject|[^/]+\.c3p|[^/]+\.capx|[^/]+\.sb3|"
                             r"[^/]+\.p8|[^/]+\.p8\.png|conf\.lua|main\.lua|[^/]+\.gmx|Cargo\.toml|"
                             r"package\.json|[^/]+\.csproj|CMakeLists\.txt|Makefile|[^/]+\.hxml|"
                             r"[^/]+\.twee|[^/]+\.ink|[^/]+\.rpy)$", re.I)


def source_markers(root: Path, limit=20):
    """Evidence that a folder holds source (not just a build): project files or
    readable, non-minified code files."""
    from filetypes import classify
    found = []
    for p in root.rglob("*"):
        if not p.is_file() or ".git" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        if PROJECT_MARKERS.search(rel):
            found.append(rel)
        else:
            info = classify(rel)
            if info["category"] == "code" and not info["generated"] and not info["third_party"]:
                try:
                    head = p.read_bytes()[:20000]
                except OSError:
                    continue
                lines = head.splitlines() or [b""]
                if max(len(l) for l in lines) < 2000:
                    found.append(rel)
        if len(found) >= limit:
            break
    return found


# ---------------------------------------------------------------- itch.io
def itch_page_url(url):
    u = urlparse(url if "://" in url else "https://" + url)
    parts = [p for p in u.path.split("/") if p]
    return f"https://{u.netloc}/{parts[0]}" if parts else None


def parse_itch_html(page_html, page_url):
    h = page_html
    out = {"page": page_url, "uploads": [], "web_build": None, "inspected": True}
    m = re.search(r'<meta name="csrf_token" value="([^"]+)"', h)
    out["csrf"] = m.group(1) if m else None
    for key in ("Updated", "Published", "Release date"):
        m = re.search(rf"<td>{key}</td>\s*<td>\s*<abbr title=\"([^\"]+)\"", h)
        if m:
            t = parse_time(m.group(1).replace(" @ ", " ").replace(" UTC", "+00:00"))
            if t is None:
                try:
                    import datetime as _d
                    t = _d.datetime.strptime(m.group(1), "%d %B %Y @ %H:%M UTC").replace(tzinfo=_d.timezone.utc)
                except ValueError:
                    t = None
            out[key.lower().replace(" ", "_")] = iso(t) if t else m.group(1)
    # uploads
    for m in re.finditer(r'<div class="upload\b(.*?)(?=<div class="upload\b|<div class="purchase_banner|</div>\s*</div>\s*</div>\s*<div class="(?:right_col|left_col|more_information)|\Z)', h, re.S):
        chunk = m.group(0)
        uid = re.search(r'data-upload_id="(\d+)"', chunk)
        name = re.search(r'<strong[^>]*class="name"[^>]*title="([^"]+)"', chunk) or \
            re.search(r'<strong[^>]*class="name"[^>]*>([^<]+)<', chunk)
        if not (uid or name):
            continue
        size = re.search(r'class="file_size"[^>]*>\s*<span>([^<]+)<', chunk)
        plats = re.findall(r'title="Download for ([^"]+)"', chunk)
        nm = html.unescape(name.group(1)) if name else ""
        out["uploads"].append({"id": uid.group(1) if uid else None, "name": nm,
                               "size": size.group(1).strip() if size else None, "platforms": plats,
                               "is_source": bool(SOURCE_WORD_RE.search(nm)),
                               "is_post_version": bool(POST_VERSION_RE.search(nm)),
                               "is_compo_version": bool(COMPO_VERSION_RE.search(nm))})
    # web build
    unesc = html.unescape(h)
    m = re.search(r'((?:https?:)?//(?:html-classic|html)\.itch\.zone/html/(\d+)/[^"\'\s<>]*)', unesc)
    if m:
        src = m.group(1)
        out["web_build"] = {"url": ("https:" + src) if src.startswith("//") else src,
                            "upload_id": m.group(2)}
    # description links (git repos are often linked here)
    m = re.search(r'<div class="formatted_description user_formatted">(.*?)</div>\s*(?:<div|</div>)', h, re.S)
    desc = m.group(1) if m else ""
    links = [html.unescape(x) for x in re.findall(r'href="([^"]+)"', desc)]
    out["description_links"] = [l for l in links if l.startswith("http")]
    out["description_text"] = re.sub(r"<[^>]+>", " ", html.unescape(desc))[:4000]
    out["has_download_now"] = "buy_btn" in h or "Download Now" in h
    return out


def inspect_itch(session, url):
    page = itch_page_url(url)
    if not page:
        return {"page": url, "inspected": False, "error": "unrecognised itch.io URL"}
    try:
        r = http_get(session, page, timeout=40)
    except NetworkBlocked as exc:
        return {"page": page, "inspected": False, "error": "itch.io is not reachable from this environment",
                "detail": str(exc)[:300], "reason_code": "network"}
    if r.status_code >= 400:
        return {"page": page, "inspected": False, "error": f"HTTP {r.status_code}",
                "reason_code": "http"}
    if "password" in r.text[:20000].lower() and "game_password" in r.text:
        return {"page": page, "inspected": False, "error": "page is password protected"}
    info = parse_itch_html(r.text, page)
    return info


def itch_download_url(session, page, upload_id, csrf, api_key=None):
    if api_key:
        try:
            r = http_get(session, f"https://itch.io/api/1/{api_key}/upload/{upload_id}/download", timeout=30)
            j = r.json()
            if j.get("url"):
                return j["url"], None
            return None, f"itch API: {j.get('errors') or j}"
        except (NetworkBlocked, ValueError) as exc:
            return None, f"itch API failed: {exc}"
    if not csrf:
        return None, "no csrf token on page"
    try:
        r = http_post(session, f"{page}/file/{upload_id}?source=view_game&as_props=1&after_download_lightbox=true",
                      data={"csrf_token": csrf}, timeout=30)
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if j.get("url"):
            return j["url"], None
        r = http_post(session, f"{page}/download_url", data={"csrf_token": csrf}, timeout=30)
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        dl_page = j.get("url")
        if not dl_page:
            return None, "itch did not return a download page (paid game or login required?)"
        key = re.search(r"/download/([\w-]+)", dl_page)
        r = http_get(session, dl_page, timeout=30)
        m = re.search(r'<meta name="csrf_token" value="([^"]+)"', r.text)
        csrf2 = m.group(1) if m else csrf
        r = http_post(session, f"{page}/file/{upload_id}?source=game_download&key={key.group(1) if key else ''}",
                      data={"csrf_token": csrf2}, timeout=30)
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if j.get("url"):
            return j["url"], None
        return None, "itch download flow did not return a file URL"
    except NetworkBlocked as exc:
        return None, f"itch.io not reachable ({exc})"
    except ValueError as exc:
        return None, f"unexpected itch response: {exc}"


def crawl_web_build(session, index_url, dest: Path, max_total=400_000_000):
    """Download an HTML5 build's index plus the files it references (Godot .pck, JS)."""
    dest.mkdir(parents=True, exist_ok=True)
    got, total, notes = [], 0, []
    try:
        r = http_get(session, index_url, timeout=40)
    except NetworkBlocked as exc:
        return {"ok": False, "error": f"web build host not reachable ({exc})"}
    if r.status_code >= 400:
        return {"ok": False, "error": f"HTTP {r.status_code} for {index_url}"}
    (dest / "index.html").write_bytes(r.content)
    got.append("index.html")
    text = r.text
    engine = "unknown"
    wanted = []
    m = re.search(r"GODOT_CONFIG\s*=\s*(\{.*?\});", text, re.S)
    if m or "godot" in text.lower():
        engine = "godot"
        cfg = {}
        try:
            cfg = json.loads(m.group(1)) if m else {}
        except ValueError:
            pass
        exe = cfg.get("executable") or (re.search(r'"executable"\s*:\s*"([^"]+)"', text) or [None, "index"])[1]
        wanted.append(cfg.get("mainPack") or f"{exe}.pck")
        wanted.append(f"{exe}.js")
    elif "createUnityInstance" in text or "UnityLoader" in text:
        engine = "unity"
        notes.append("Unity WebGL build: data files are packed; only the loader page is kept.")
    for pck in re.findall(r"""['"]([\w./-]+\.pck)['"]""", text):
        engine = "godot"
        wanted.append(pck)
    for src in re.findall(r'<script[^>]+src="([^"]+)"', text):
        if not src.startswith(("http://", "https://", "//")):
            wanted.append(src)
    seen = set()
    for rel in wanted:
        if rel in seen:
            continue
        seen.add(rel)
        url = urljoin(index_url, rel)
        tgt = dest / rel.split("?")[0].lstrip("/")
        try:
            path, _, _ = download(session, url, tgt, max_bytes=max_total - total)
            total += Path(path).stat().st_size
            got.append(rel)
        except (NetworkBlocked, RuntimeError) as exc:
            notes.append(f"could not fetch {rel}: {exc}")
    # JS games: pull assets referenced by relative path from fetched scripts
    if engine == "unknown":
        for js in [p for p in dest.rglob("*.js")][:20]:
            for rel in set(re.findall(r'["\']([\w./-]+\.(?:png|jpe?g|gif|webp|ogg|mp3|wav|json|txt|glb|gltf|ttf|woff2?))["\']',
                                      js.read_text(errors="replace"))):
                if rel.startswith(("http", "//")) or rel in seen or total > max_total:
                    continue
                seen.add(rel)
                try:
                    path, _, _ = download(session, urljoin(index_url, rel), dest / rel.lstrip("./"),
                                          max_bytes=max_total - total)
                    total += Path(path).stat().st_size
                    got.append(rel)
                except (NetworkBlocked, RuntimeError):
                    pass
    return {"ok": True, "kind": "web", "engine": engine, "path": str(dest), "url": index_url,
            "files": got, "bytes": total, "notes": notes}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--local", nargs="*", default=[], help="user-provided source: folder, repo or archive")
    ap.add_argument("--url", nargs="*", default=[], help="extra candidate URLs (e.g. from the user)")
    ap.add_argument("--itch-manual", help="JSON describing an itch page that could not be fetched "
                    "here: {page, uploads:[{name,size,platforms}], description_links:[], web_build:bool, updated}")
    ap.add_argument("--itch-api-key", default=os.environ.get("ITCH_API_KEY"))
    ap.add_argument("--build-local", help="user-provided played build (archive or folder)")
    ap.add_argument("--no-build", action="store_true", help="do not fetch the played build")
    ap.add_argument("--lfs", action="store_true", help="fetch Git LFS objects")
    args = ap.parse_args()

    wd = Path(args.workdir).resolve()
    game = load_json(wd / "game.json") or {}
    src_dir, build_dir = wd / "source", wd / "build"
    result = {"status": None, "reason": None, "candidates": [], "source": None, "itch": [],
              "played_build": None, "manual_actions": [], "warnings": []}
    fmt = (game.get("game") or {}).get("format")
    if fmt and fmt != "compo":
        result["warnings"].append(f"Entry format is '{fmt}', not Compo – source code is not mandatory.")

    # ---- user-provided source short-circuits the search
    if args.local:
        got = []
        for p in args.local:
            p = Path(p).expanduser().resolve()
            if not p.exists():
                result["warnings"].append(f"--local path not found: {p}")
                continue
            if p.is_dir():
                got.append({"ok": True, "method": "git" if (p / ".git").exists() else "folder",
                            "repo_path": str(p) if (p / ".git").exists() else None,
                            "path": str(p), "url": "user-provided", "ref": "HEAD"})
            else:
                dl = src_dir / "downloads"
                dl.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dl / p.name)
                target = src_dir / "extracted" / p.stem
                ok, msg = extract(dl / p.name, target)
                if ok:
                    maybe_extract_nested(target)
                    got.append({"ok": True, "method": "archive", "archive_path": str(dl / p.name),
                                "path": str(target), "url": "user-provided", "archive_kind": archive_kind(p)})
                else:
                    result["warnings"].append(f"could not extract {p.name}: {msg}")
        if got:
            src = got[0]
            # a zipped project that still contains its .git folder -> use git mode
            gitdirs = [d.parent for d in Path(src["path"]).rglob(".git") if d.is_dir()]
            if src["method"] != "git" and gitdirs:
                src.update(method="git", repo_path=str(gitdirs[0]), ref="HEAD",
                           note="archive contains a .git folder; using its history")
            result.update(status="SOURCE_FOUND", reason="source provided by the user", source=src)
            if len(got) > 1:
                result["additional_sources"] = got[1:]
            finish(wd, result, args, game, build_dir)
            return

    # ---- collect candidates
    cands = []
    for l in game.get("links", []):
        cands.append(classify_url(l["url"], l.get("name", ""), [t["slug"] for t in l.get("tags", [])]))
    for u in game.get("description_urls", []):
        c = classify_url(u, origin="ldjam-description")
        if c["score"] > 0 or c["kind"] == "itch":
            cands.append(c)
    for u in args.url:
        c = classify_url(u, "user-provided", origin="user")
        c["score"] = max(c["score"], 4)
        cands.append(c)

    session = None
    try:
        session = http_session()
    except ImportError:
        result["warnings"].append("python 'requests' missing – only git sources can be fetched")

    # ---- inspect itch pages: source uploads, played build, extra links
    itch_infos = []
    manual = load_json(args.itch_manual) if args.itch_manual else None
    for c in [c for c in cands if c["kind"] == "itch"]:
        page = itch_page_url(c["url"])
        if manual and itch_page_url(manual.get("page", "")) == page:
            info = dict(manual, inspected=True, manual=True)
            for up in info.get("uploads", []):
                nm = up.get("name", "")
                up.setdefault("is_source", bool(SOURCE_WORD_RE.search(nm)))
                up.setdefault("is_post_version", bool(POST_VERSION_RE.search(nm)))
                up.setdefault("is_compo_version", bool(COMPO_VERSION_RE.search(nm)))
        elif session:
            info = inspect_itch(session, c["url"])
        else:
            info = {"page": page, "inspected": False, "error": "no HTTP client"}
        info["from_url"] = c["url"]
        itch_infos.append(info)
        if info.get("inspected"):
            for up in info.get("uploads", []):
                if up.get("is_source"):
                    cands.append({"url": page, "label": up["name"], "tags": [], "origin": "itch-upload",
                                  "kind": "itch_upload", "upload": up, "score": 4 + (1 if up.get("is_compo_version") else 0)
                                  - (1 if up.get("is_post_version") else 0), "itch_page": page})
            for u in info.get("description_links", []):
                cc = classify_url(u, origin="itch-description")
                if cc["score"] > 0:
                    cands.append(cc)
    result["itch"] = [{k: v for k, v in i.items() if k != "csrf"} for i in itch_infos]

    # de-duplicate by URL+kind, keep highest score
    best = {}
    for c in cands:
        key = (c["kind"], c["url"].rstrip("/").lower(), (c.get("upload") or {}).get("id"))
        if key not in best or c["score"] > best[key]["score"]:
            best[key] = c
    cands = sorted(best.values(), key=lambda c: -c["score"])
    result["candidates"] = cands

    source_cands = [c for c in cands if c["score"] >= 2 or c["kind"] in ("git", "itch_upload", "platform")]
    # untagged archive links are probed last: some builds (LÖVE, HTML5, Python, PICO-8) contain the source
    probe_cands = [c for c in cands if c not in source_cands and c["kind"] in ("archive", "gdrive", "dropbox")]
    for c in probe_cands:
        c["probe_only"] = True
    uninspected_itch = [i for i in itch_infos if not i.get("inspected")]

    # ---- try retrieval in priority order
    attempts = []
    for c in source_cands + probe_cands:
        res = None
        if c["kind"] == "git":
            res = clone_git(c, src_dir / "repo", with_lfs=args.lfs)
        elif c["kind"] in ("archive", "gdrive", "dropbox") and session:
            url = gdrive_direct(c["url"]) if c["kind"] == "gdrive" else \
                dropbox_direct(c["url"]) if c["kind"] == "dropbox" else c["url"]
            if url:
                res = fetch_archive(session, url, src_dir / "downloads", src_dir / "extracted")
        elif c["kind"] == "itch_upload" and session:
            info = next((i for i in itch_infos if i.get("page") == c["itch_page"]), {})
            if info.get("manual") or not c["upload"].get("id"):
                res = {"ok": False, "error": "itch.io page could only be read, not downloaded from, here",
                       "reason_code": "network"}
            else:
                url, err = itch_download_url(session, c["itch_page"], c["upload"]["id"], info.get("csrf"),
                                             args.itch_api_key)
                res = fetch_archive(session, url, src_dir / "downloads", src_dir / "extracted",
                                    name_hint=c["upload"]["name"]) if url else \
                    {"ok": False, "error": err, "reason_code": "itch"}
        elif c["kind"] in ("manual_host", "platform"):
            res = {"ok": False, "error": f"{c.get('host_name') or c.get('platform')} links need a manual download",
                   "reason_code": "manual"}
        if res is None:
            continue
        if res.get("ok") and res.get("method") in ("archive", "file"):
            found = source_markers(Path(res["path"]))
            res["source_markers"] = found
            if not found:
                res = dict(res, ok=False, reason_code="build_only",
                           error="downloaded fine but contains no source code or project files (build only)")
        c["attempted"], c["ok"] = True, res.get("ok")
        c["error"] = res.get("error")
        if c.get("probe_only") and not res.get("ok"):
            continue          # a probe that isn't source is not a failure to retrieve source
        attempts.append((c, res))
        if res.get("ok"):
            how = "a download that contains the project" if c.get("probe_only") else res.get("method")
            result.update(status="SOURCE_FOUND", reason=f"retrieved via {how} from {c['url']}",
                          source=res)
            gitdirs = [d.parent for d in Path(res.get("path", "")).rglob(".git") if d.is_dir()] \
                if res.get("method") == "archive" else []
            if gitdirs:
                res.update(method="git", repo_path=str(gitdirs[0]), ref="HEAD",
                           note="archive contains a .git folder; using its history")
            break

    real_failures = [(c, r) for c, r in attempts if r.get("reason_code") != "build_only"]
    if result["status"] is None:
        if real_failures or uninspected_itch:
            attempts = real_failures
            result["status"] = "FAILED_TO_RETRIEVE"
            reasons = [f"{c['url']} → {r.get('error')}" for c, r in attempts]
            reasons += [f"{i['page']} → could not inspect ({i.get('error')})" for i in uninspected_itch]
            result["reason"] = "; ".join(reasons)
            for c, r in attempts:
                if c["kind"] == "itch_upload":
                    result["manual_actions"].append(
                        f"Download the upload '{c['upload']['name']}' from {c['itch_page']} and attach it here.")
                elif r.get("reason_code") == "repository not found or private":
                    result["manual_actions"].append(
                        f"{c['url']} is missing or private. If you can't open it either, the entry has "
                        f"NO SOURCE AVAILABLE; otherwise attach a zip of the repository.")
                else:
                    result["manual_actions"].append(f"Download {c['url']} yourself and attach the file "
                                                    f"(or a zip of the folder) here.")
            for i in uninspected_itch:
                result["manual_actions"].append(
                    f"Check {i['page']} for a source download (e.g. 'source.zip'); attach it if present, "
                    f"or tell me there is none. (Or paste the page's upload list.)")
        else:
            result["status"] = "NO_SOURCE_AVAILABLE"
            build_only = [c["url"] for c, r in attempts if r.get("reason_code") == "build_only"]
            if build_only:
                result["warnings"].append("Links presented as source only contain builds: " + ", ".join(build_only))
            looked = [f"{len(game.get('links', []))} ldjam link(s)", "the description"]
            if itch_infos:
                looked.append(f"{len(itch_infos)} itch.io page(s) (uploads: " +
                              ", ".join(u['name'] for i in itch_infos for u in i.get('uploads', [])) + ")")
            result["reason"] = "No source link, source upload or repository found in " + ", ".join(looked) + "."
            embedded = [l for l in game.get("links", []) if any(t["slug"] in ("web-html5", "pico-8", "love")
                                                                  for t in l.get("tags", []))]
            if embedded:
                result["warnings"].append(
                    "Some engines ship their source inside the build (plain JS/HTML5, PICO-8, LÖVE .love, "
                    "Twine, Bitsy). If this is one of them the build may count as the source – check "
                    "before concluding.")
    finish(wd, result, args, game, build_dir, session, itch_infos)


def stage1_certainty(result, game, itch_infos):
    """Attach a probability to the stage-1 outcome, with the reasons behind it."""
    st, basis = result["status"], []
    src = result.get("source") or {}
    if st == "SOURCE_FOUND":
        if src.get("url") == "user-provided":
            v, stmt = 0.9, "the provided files are the entry's source"
            basis.append("provided by the user – confirm it is the code of this entry")
        elif src.get("method") == "git":
            v, stmt = 0.95, "the retrieved repository is the entry's source"
            basis.append("git repository linked from the entry")
        else:
            v, stmt = 0.85, "the retrieved files are the entry's source"
            basis.append("archive linked from the entry contains project/code files")
            if len(src.get("source_markers") or []) <= 2:
                v -= 0.15
                basis.append("only a few code/project files found inside")
    elif st == "NO_SOURCE_AVAILABLE":
        v, stmt = 0.8, "the entry provides no source code"
        basis.append(f"checked {len(game.get('links', []))} ldjam link(s), {len(game.get('description_urls', []))} "
                     f"URL(s) in the description")
        if itch_infos and all(i.get("inspected") for i in itch_infos):
            v += 0.1
            basis.append("all itch.io pages inspected: no source-like upload or repository link")
        if any(i.get("manual") for i in itch_infos):
            v -= 0.1
            basis.append("itch.io upload list came from a page summary, which can miss items")
        if any("contain builds" in w or "only contain builds" in w for w in result["warnings"]):
            v += 0.05
            basis.append("links presented as source were downloaded and contain only builds")
        if any("ship their source inside the build" in w for w in result["warnings"]):
            v -= 0.3
            basis.append("web/PICO-8/LÖVE builds can embed readable source – check the build")
        if any("played build contains readable code" in w for w in result["warnings"]):
            v -= 0.4
            basis.append("the played build contains readable code/project files")
    else:
        stmt = "a source exists but could not be retrieved here"
        codes = [c.get("error", "") for c in result.get("candidates", []) if c.get("attempted")]
        if any("not found or private" in (e or "") for e in codes):
            v = 0.45
            basis.append("a linked repository returns 'not found' – it may be private or deleted")
        elif any(c.get("kind") in ("git", "itch_upload") or "source-code" in c.get("tags", [])
                 for c in result.get("candidates", [])):
            v = 0.85
            basis.append("a source link/upload exists; only the download failed")
        else:
            v = 0.5
            basis.append("pages that may offer source could not be inspected")
    v = max(0.05, min(0.97, v))
    result["certainty"] = {"value": round(v, 2), "statement": stmt, "basis": basis}


def finish(wd, result, args, game, build_dir, session=None, itch_infos=()):
    if not args.no_build:
        result["played_build"] = get_played_build(args, game, build_dir, session, itch_infos)
    pb = result.get("played_build") or {}
    if result["status"] == "NO_SOURCE_AVAILABLE" and pb.get("status") == "ok" and pb.get("path"):
        sm = source_markers(Path(pb["path"]))
        if sm:
            result["warnings"].append(
                f"The played build contains readable code/project files ({', '.join(sm[:5])}); it may "
                f"count as the source. To analyse it: get_source.py --workdir {wd} --local {pb['path']}")
    stage1_certainty(result, game, list(itch_infos))
    save_json(wd / "stage1.json", result)
    c = result["certainty"]
    print(f"STATUS: {result['status']}  (certainty {int(100 * c['value'])}% that {c['statement']})")
    print(f"Reason: {result['reason']}")
    for b in c["basis"]:
        print(f"  basis: {b}")
    if result.get("source"):
        s = result["source"]
        print(f"Source: {s.get('method')} {s.get('display') or s.get('url')} -> {s.get('repo_path') or s.get('path')}"
              + (f" (ref {s.get('ref')}, subpath {s.get('subpath')})" if s.get("method") == "git" else ""))
    for a in result["manual_actions"]:
        print(f"ACTION: {a}")
    pb = result.get("played_build") or {}
    if pb:
        print(f"Played build: {pb.get('kind')} {pb.get('status')} {pb.get('path') or pb.get('error') or ''}")
    for w in result["warnings"]:
        print(f"WARNING: {w}")
    print(f"Wrote {wd / 'stage1.json'}")


def get_played_build(args, game, build_dir: Path, session, itch_infos):
    if args.build_local:
        p = Path(args.build_local).expanduser().resolve()
        if p.is_dir():
            return {"status": "ok", "kind": "user-provided", "path": str(p)}
        target = build_dir / "user" / p.stem
        ok, msg = extract(p, target)
        return {"status": "ok" if ok else "failed", "kind": "user-provided",
                "path": str(target) if ok else None, "error": None if ok else msg}
    if session is None:
        return {"status": "skipped", "error": "no HTTP client"}
    notes = []
    for info in itch_infos:
        if not info.get("inspected") or info.get("manual"):
            continue
        ups = info.get("uploads", [])
        post = [u["name"] for u in ups if u.get("is_post_version")]
        compo = [u for u in ups if u.get("is_compo_version") and not u.get("is_source")]
        if post:
            notes.append(f"itch page lists post-event uploads: {', '.join(post)}")
        # 1. a downloadable upload explicitly labelled as the Compo/Jam version
        for up in compo:
            if up.get("id"):
                url, err = itch_download_url(session, info["page"], up["id"], info.get("csrf"), args.itch_api_key)
                if url:
                    res = fetch_archive(session, url, build_dir / "downloads", build_dir / "extracted", up["name"])
                    if res.get("ok"):
                        return {"status": "ok", "kind": "itch-download", "upload": up, "path": res["path"],
                                "itch_updated": info.get("updated"), "notes": notes}
        # 2. the embedded web build (what most raters play)
        if info.get("web_build"):
            res = crawl_web_build(session, info["web_build"]["url"], build_dir / "web")
            if res.get("ok"):
                return {"status": "ok", "kind": "itch-web", "path": res["path"], "engine": res["engine"],
                        "files": res["files"], "url": res["url"], "upload_id": info["web_build"]["upload_id"],
                        "itch_updated": info.get("updated"), "notes": notes + res.get("notes", [])}
            notes.append(res.get("error", "web build fetch failed"))
        # 3. any non-source, non-post upload (prefer Linux/Windows zips)
        rank = sorted([u for u in ups if not u.get("is_source") and not u.get("is_post_version") and u.get("id")],
                      key=lambda u: (0 if any(p in ("Linux", "Windows") for p in u.get("platforms", [])) else 1))
        for up in rank[:2]:
            url, err = itch_download_url(session, info["page"], up["id"], info.get("csrf"), args.itch_api_key)
            if url:
                res = fetch_archive(session, url, build_dir / "downloads", build_dir / "extracted", up["name"])
                if res.get("ok"):
                    return {"status": "ok", "kind": "itch-download", "upload": up, "path": res["path"],
                            "itch_updated": info.get("updated"), "notes": notes}
            notes.append(f"{up['name']}: {err}")
    # web builds hosted elsewhere (e.g. GitHub Pages)
    for l in game.get("links", []):
        if any(t["slug"].startswith("web") for t in l.get("tags", [])) and "itch.io" not in l["url"]:
            res = crawl_web_build(session, l["url"], build_dir / "web-external")
            if res.get("ok"):
                return {"status": "ok", "kind": "web-external", "path": res["path"], "engine": res["engine"],
                        "url": l["url"], "notes": notes + res.get("notes", [])}
            notes.append(res.get("error", ""))
    return {"status": "not_retrieved", "notes": notes,
            "hint": "Optional: attach the build players use (e.g. the itch.io web build or Windows zip) "
                    "so it can be matched to a commit."}


if __name__ == "__main__":
    main()
