#!/usr/bin/env python3
"""Stage 0 – resolve a Ludum Dare (ldjam.com) game page into game.json.

Fetches the game node, its event, authors, links (with platform tags), rating
category opt-outs, the description, comments, the author's posts around the
event and the author's other entries.

Network-restricted sessions: if api.ldjam.com cannot be reached, the script
writes fetch_plan.json and exits with code 3. Fetch each listed URL another way
(e.g. a web-fetch tool), save the JSON bodies under <out>/raw/ with the listed
file names, and rerun with the same arguments; files already in raw/ are used
instead of the network.

Usage:
  ldjam_page.py https://ldjam.com/events/ludum-dare/60/some-game [--out DIR]
                [--event-start ISO] [--skip-extras]
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (API_BASE, NetworkBlocked, Window, extract_urls, fmt_time,  # noqa: E402
                    http_get, http_session, iso, load_json, log, parse_time,
                    save_json, slugify)

# Fallback for the platform tags used by game links (from /vx/tag/get/platform).
PLATFORM_TAGS = {
    42332: ("source-code", "Source Code"), 42336: ("web-html5", "HTML5 (web)"),
    42337: ("microsoft-windows", "Microsoft Windows"), 42339: ("apple-macos", "Apple macOS"),
    42341: ("linux", "Linux"), 42342: ("google-android", "Google Android"),
    42346: ("apple-ios", "Apple iOS"), 42438: ("web-flash", "Flash (web)"),
    42440: ("web", "Web (other)"), 42512: ("other", "Other Device"),
    42516: ("document", "Document (PDF/etc)"), 42517: ("interactive-document", "Interactive Document"),
    206164: ("interactive-video", "Interactive Video"), 206169: ("interactive-audio", "Interactive Audio"),
    206182: ("microsoft-dos", "Microsoft DOS"), 206184: ("unix", "Unix/BSD (other)"),
    206185: ("other-os", "Other OS"), 206175: ("other-computer", "Other Computer"),
    206176: ("other-mobile", "Other Mobile Device"), 206196: ("virtual-machine", "Virtual Machine (binary)"),
    206200: ("binary", "Binary (other)"), 262146: ("core", "Core"), 262148: ("roblox", "Roblox"),
    262150: ("playdate", "Playdate"), 262153: ("steam", "Steam (Deck)"),
    262157: ("steam-other", "Steam (other)"), 276478: ("love", "LÖVE"), 276479: ("pico-8", "PICO-8"),
}

# Used only when the event node is unavailable (order used by ldjam.com events).
DEFAULT_GRADES = [("grade-01", "Overall"), ("grade-02", "Fun"), ("grade-03", "Innovation"),
                  ("grade-04", "Theme"), ("grade-05", "Graphics"), ("grade-06", "Audio"),
                  ("grade-07", "Humor"), ("grade-08", "Mood")]

MEDIA_HOST_RE = re.compile(r"(youtube\.com|youtu\.be|twitch\.tv|vimeo\.com|streamable\.com|"
                           r"bsky\.app|twitter\.com|x\.com|mastodon|tiktok\.com)", re.I)


def parse_input(url: str):
    """Return ('path', '/events/...') or ('id', 123)."""
    url = url.strip()
    if re.fullmatch(r"\$?\d+", url):
        return "id", int(url.lstrip("$"))
    if not re.match(r"https?://", url):
        url = "https://" + url
    u = urlparse(url)
    host = u.netloc.lower()
    path = re.sub(r"/+$", "", u.path)
    if host.endswith("ldj.am"):
        m = re.match(r"/\$(\d+)", path)
        if m:
            return "id", int(m.group(1))
    if "ldjam.com" not in host and "ludumdare.com" not in host:
        raise SystemExit(f"Not an ldjam.com URL: {url}")
    if not path.startswith("/events/"):
        raise SystemExit(f"Expected a game URL like https://ldjam.com/events/ludum-dare/60/<game>: {url}")
    return "path", path


def nodes_from(resp):
    if not resp:
        return []
    if isinstance(resp, list):
        return [n for n in resp if isinstance(n, dict)]
    if isinstance(resp, dict):
        if isinstance(resp.get("node"), list):
            return [n for n in resp["node"] if isinstance(n, dict)]
        if isinstance(resp.get("node"), dict):
            return [resp["node"]]
        if "id" in resp and "type" in resp:
            return [resp]
    return []


class Fetcher:
    """Reads raw/<name>.json if present, otherwise calls the API and saves it."""

    def __init__(self, raw_dir: Path, offline=False):
        self.raw = raw_dir
        self.raw.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.session = None
        self.blocked = False
        self.missing = []  # (name, url, purpose, required)

    def get(self, name, url, purpose, required=False):
        path = self.raw / f"{name}.json"
        data = load_json(path)
        if data is not None:
            return data
        if self.offline or self.blocked:
            self.missing.append((name, url, purpose, required))
            return None
        try:
            if self.session is None:
                self.session = http_session()
            r = http_get(self.session, url, timeout=40)
            if r.status_code == 403 and "ldjam" not in r.text[:500].lower():
                raise NetworkBlocked(f"HTTP 403 from proxy for {url}")
            data = r.json()
        except NetworkBlocked as exc:
            log(f"[network] {exc}")
            self.blocked = True
            self.missing.append((name, url, purpose, required))
            return None
        except ValueError:
            log(f"[warn] non-JSON response from {url}")
            return None
        save_json(path, data)
        return data


def tag_info(tag_id, api_tags):
    try:
        tid = int(tag_id)
    except (TypeError, ValueError):
        return {"id": tag_id, "slug": str(tag_id), "name": str(tag_id)}
    if tid in api_tags:
        return api_tags[tid]
    if tid in PLATFORM_TAGS:
        slug, name = PLATFORM_TAGS[tid]
        return {"id": tid, "slug": slug, "name": name}
    return {"id": tid, "slug": f"tag-{tid}", "name": f"tag {tid}"}


def as_list(v):
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str) and "+" in v:
        return [x for x in v.split("+") if x]
    return [v]


def link_kind(url, tag_slugs, name):
    u = (url or "").lower()
    n = (name or "").lower()
    if "source-code" in tag_slugs or re.search(r"\b(source|src|code|repo|repository|project files?)\b", n):
        return "source"
    if re.search(r"(github\.com|gitlab\.com|codeberg\.org|bitbucket\.org|sr\.ht|gitea|forgejo|\.git$)", u):
        return "source"
    if "itch.io" in u:
        return "itch"
    if any(s.startswith("web") for s in tag_slugs):
        return "web"
    return "build"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="ldjam.com game URL, ldj.am/$id short link, or node id")
    ap.add_argument("--out", help="work directory (default ./ldcheck-<slug>)")
    ap.add_argument("--event-start", help="override event start (ISO, UTC)")
    ap.add_argument("--compo-end", help="override Compo end (ISO, UTC)")
    ap.add_argument("--skip-extras", action="store_true",
                    help="skip comments, author posts and author history")
    ap.add_argument("--offline", action="store_true", help="never use the network; only raw/*.json")
    args = ap.parse_args()

    kind, ref = parse_input(args.url)
    guess = slugify(ref.rstrip("/").split("/")[-1] if kind == "path" else f"node-{ref}")
    out = Path(args.out or f"ldcheck-{guess}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    f = Fetcher(out / "raw", offline=args.offline)

    # ---- 1. game + event + authors -------------------------------------------------
    if kind == "path":
        walk = f.get("walk", f"{API_BASE}/node2/walk/1{ref}?node&parent&author&authors",
                     "game node, its event (parent) and authors", required=True)
    else:
        walk = f.get("walk", f"{API_BASE}/node2/get/{ref}?parent&author&authors",
                     "game node, its event (parent) and authors", required=True)
    if isinstance(walk, dict) and walk.get("status") not in (None, 200):
        raise SystemExit(f"ldjam API returned status {walk.get('status')} for this URL "
                         f"(not found or not published?): {walk.get('message', '')}")
    nodes = nodes_from(walk)
    game = None
    if isinstance(walk, dict) and walk.get("node_id"):
        game = next((n for n in nodes if n.get("id") == walk["node_id"]), None)
    if game is None:
        game = next((n for n in nodes if n.get("type") == "item"), None)
    if game is None:
        if walk is not None and not f.missing:
            raise SystemExit("The ldjam API response contains no game node. Check the URL "
                             "(it must be the game's page, not the event or a post).")
        write_plan_and_exit(out, f, args)
        return
    if isinstance(walk, dict) and walk.get("extra"):
        log(f"[warn] URL did not fully resolve; unresolved path parts: {walk.get('extra')}")
    if game.get("subtype") not in (None, "game"):
        log(f"[warn] node {game.get('id')} is a '{game.get('subtype')}', not a game")

    event = next((n for n in nodes if n.get("id") == game.get("parent")), None)
    if event is None:
        ev = f.get("event", f"{API_BASE}/node2/get/{game.get('parent')}",
                   "event node (start time, rating categories)", required=True)
        event = next(iter(nodes_from(ev)), None)
    meta = game.get("meta") or {}
    author_ids = [int(a) for a in as_list(meta.get("author")) if str(a).isdigit()] or \
                 ([int(game["author"])] if game.get("author") else [])
    users = {n["id"]: n for n in nodes if n.get("type") == "user"}
    missing_users = [a for a in author_ids if a not in users]
    if missing_users:
        ua = f.get("authors", f"{API_BASE}/node2/get/{'+'.join(map(str, missing_users))}",
                   "author user nodes (names)")
        for n in nodes_from(ua):
            users[n.get("id")] = n

    # ---- 2. tags --------------------------------------------------------------------
    api_tags = {}
    tags = f.get("tags", f"{API_BASE}/tag/get/platform", "platform tag names for links")
    for t in (tags or {}).get("tag", []) if isinstance(tags, dict) else []:
        try:
            api_tags[int(t["id"])] = {"id": int(t["id"]), "slug": t.get("slug"), "name": t.get("name")}
        except (KeyError, TypeError, ValueError):
            pass

    # ---- 3. extras --------------------------------------------------------------------
    comments, posts, history = [], [], []
    gid = game.get("id")
    if not args.skip_extras and gid:
        c = f.get("comments", f"{API_BASE}/comment/getbynode/{gid}", "comments on the game page")
        clist = (c or {}).get("comment", []) if isinstance(c, dict) else (c or [])
        cauth = sorted({int(x.get("author")) for x in clist if str(x.get("author", "")).isdigit()} - set(users))
        if cauth:
            cu = f.get("comment_authors", f"{API_BASE}/node2/get/{'+'.join(map(str, cauth[:200]))}",
                       "names of commenters")
            for n in nodes_from(cu):
                users[n.get("id")] = n
        for x in clist:
            aid = int(x["author"]) if str(x.get("author", "")).isdigit() else None
            comments.append({
                "id": x.get("id"), "author_id": aid,
                "author_name": (users.get(aid) or {}).get("name"),
                "is_game_author": aid in author_ids,
                "created": iso(x.get("created")), "modified": iso(x.get("modified")),
                "body": x.get("body", ""),
            })
        for aid in author_ids[:3]:
            feed = f.get(f"posts_feed_{aid}", f"{API_BASE}/node/feed/{aid}/author/post?limit=100",
                         "the author's blog posts (progress posts during the event)")
            ids = [x.get("id") for x in (feed or {}).get("feed", [])] if isinstance(feed, dict) else []
            if ids:
                pn = f.get(f"posts_{aid}", f"{API_BASE}/node2/get/{'+'.join(map(str, ids[:100]))}",
                           "post contents")
                posts.extend(summarise_posts(nodes_from(pn), event, game))
            gfeed = f.get(f"games_feed_{aid}", f"{API_BASE}/node/feed/{aid}/authors/item/game?limit=50",
                          "the author's other Ludum Dare entries")
            gids = [x.get("id") for x in (gfeed or {}).get("feed", [])] if isinstance(gfeed, dict) else []
            if gids:
                gn = f.get(f"games_{aid}", f"{API_BASE}/node2/get/{'+'.join(map(str, gids[:50]))}",
                           "other entries by the author")
                for n in nodes_from(gn):
                    if n.get("id") == gid:
                        continue
                    history.append({"id": n.get("id"), "name": n.get("name"), "path": n.get("path"),
                                    "format": n.get("subsubtype"), "published": iso(n.get("published")),
                                    "url": f"https://ldjam.com{n.get('path')}" if n.get("path") else None})

    # ---- 4. normalise -------------------------------------------------------------------
    ev_meta = (event or {}).get("meta") or {}
    start = args.event_start or ev_meta.get("event-start")
    win = None
    warnings = []
    if start:
        win = Window(start, compo_end=args.compo_end)
    else:
        warnings.append("Event start time unknown: rerun with --event-start (ISO, UTC).")

    links = []
    for i in range(1, 10):
        url = meta.get(f"link-0{i}")
        if not url:
            continue
        tinfo = [tag_info(t, api_tags) for t in as_list(meta.get(f"link-0{i}-tag"))]
        slugs = [t["slug"] for t in tinfo]
        name = meta.get(f"link-0{i}-name") or ""
        links.append({"index": i, "url": url, "name": name, "tags": tinfo,
                      "kind_hint": link_kind(url, slugs, name), "from": "ldjam-link"})
    body = game.get("body") or ""
    desc_urls = [u for u in extract_urls(body) if u not in {l["url"] for l in links}]

    grades = []
    keys = sorted(k for k in ev_meta if re.fullmatch(r"grade-\d+", k))
    if keys:
        for k in keys:
            grades.append({"key": k, "name": ev_meta.get(k),
                           "optional": str(ev_meta.get(f"{k}-optional", "0")) == "1",
                           "required_note": str(ev_meta.get(f"{k}-required", "0")) == "1",
                           "opted_out": str(meta.get(f"{k}-out", "0")) not in ("0", "", "None", "False"),
                           "assumed": False})
    else:
        warnings.append("Rating categories not available from the event node; using the standard order.")
        for k, name in DEFAULT_GRADES:
            grades.append({"key": k, "name": name, "optional": name != "Overall",
                           "required_note": False,
                           "opted_out": str(meta.get(f"{k}-out", "0")) not in ("0", "", "None", "False"),
                           "assumed": True})

    authors = []
    for a in author_ids:
        u = users.get(a) or {}
        authors.append({"id": a, "name": u.get("name"), "slug": u.get("slug"),
                        "url": f"https://ldjam.com/users/{u.get('slug')}" if u.get("slug") else None,
                        "created": iso(u.get("created"))})

    fmt = game.get("subsubtype") or ""
    if fmt and fmt != "compo":
        warnings.append(f"This entry is in the '{fmt}' format, not the Compo: Compo-only rules "
                        f"(solo, everything made during the 48h, source required) do not apply.")
    if len(authors) > 1:
        warnings.append(f"{len(authors)} authors are listed; the Compo is solo-only.")
    if win and game.get("published"):
        pub = parse_time(game.get("published"))
        if pub and pub > win.submission_end and fmt == "compo":
            warnings.append(f"Published {fmt_time(pub)}, after the Compo Submission Hour.")

    data = {
        "input_url": args.url,
        "fetched_at": iso(datetime.datetime.now(datetime.timezone.utc)),
        "fetch_mode": "offline-files" if (f.offline or f.blocked) else "api",
        "game": {
            "id": gid, "name": game.get("name"), "slug": game.get("slug"), "path": game.get("path"),
            "url": f"https://ldjam.com{game.get('path')}" if game.get("path") else args.url,
            "format": fmt, "published": iso(game.get("published")), "created": iso(game.get("created")),
            "modified": iso(game.get("modified")), "authors": authors, "body": body,
            "cover": meta.get("cover"), "dont_rate_me": str(meta.get("dont-rate-me", "0")) == "1",
            "meta_keys": sorted(meta.keys()),
        },
        "event": {
            "id": (event or {}).get("id"), "name": (event or {}).get("name"),
            "path": (event or {}).get("path"), "theme": ev_meta.get("event-theme"),
            "event_start": iso(start) if start else None, "event_end_meta": iso(ev_meta.get("event-end")),
            "compo_end": iso(win.compo_end) if win else None,
            "submission_end": iso(win.submission_end) if win else None,
            "jam_end": iso(win.jam_end) if win else None,
            "window_source": "override" if args.event_start else ("event-start meta" if start else None),
        },
        "links": links,
        "description_urls": desc_urls,
        "media_links": [u for u in desc_urls if MEDIA_HOST_RE.search(u)],
        "grades": grades,
        "comments": comments,
        "author_posts": posts,
        "author_history": sorted(history, key=lambda h: h.get("published") or ""),
        "warnings": warnings,
        "missing_optional_data": [{"file": f"raw/{n}.json", "url": u, "purpose": p}
                                  for (n, u, p, req) in f.missing if not req],
    }
    save_json(out / "game.json", data)
    if f.missing:
        save_plan(out, f)
    print_summary(out, data, win)


def summarise_posts(nodes, event, game):
    out = []
    ev_id = (event or {}).get("id")
    ev_start = parse_time(((event or {}).get("meta") or {}).get("event-start"))
    for n in nodes:
        created = parse_time(n.get("published") or n.get("created"))
        in_event = n.get("parent") in (ev_id, game.get("id")) or n.get("superparent") == ev_id
        if not in_event and ev_start and created:
            in_event = abs((created - ev_start).total_seconds()) < 4 * 24 * 3600
        if not in_event:
            continue
        body = n.get("body") or ""
        media = re.findall(r"!\[[^\]]*\]\(([^)]+)\)|(///content/[^\s)]+)", body)
        media = [a or b for a, b in media]
        out.append({"id": n.get("id"), "name": n.get("name"), "path": n.get("path"),
                    "url": f"https://ldjam.com{n.get('path')}" if n.get("path") else None,
                    "published": iso(created), "images": len(media),
                    "gifs": sum(1 for m in media if m.lower().endswith((".gif", ".mp4", ".webm"))),
                    "media_links": [u for u in extract_urls(body) if MEDIA_HOST_RE.search(u)],
                    "body_excerpt": body[:600]})
    return sorted(out, key=lambda p: p.get("published") or "")


def save_plan(out, f):
    plan = [{"save_as": f"raw/{n}.json", "url": u, "purpose": p, "required": req}
            for (n, u, p, req) in f.missing]
    save_json(out / "fetch_plan.json", plan)


def write_plan_and_exit(out, f, args):
    save_plan(out, f)
    log("\nCould not reach api.ldjam.com. Fetch these URLs another way and save each JSON body:")
    for n, u, p, req in f.missing:
        log(f"  {'REQUIRED' if req else 'optional'}  raw/{n}.json  <-  {u}   ({p})")
    log(f"\nThen rerun: {Path(sys.argv[0]).name} {args.url} --out {out}")
    print(f"NEEDS_FETCH {out / 'fetch_plan.json'}")
    sys.exit(3)


def print_summary(out, d, win):
    g, e = d["game"], d["event"]
    print(f"Game: {g['name']}  [{g['format'] or '?'}]  by {', '.join(a['name'] or str(a['id']) for a in g['authors'])}")
    print(f"Event: {e.get('name')}  theme: {e.get('theme')}")
    print(f"Window: {win.describe() if win else 'UNKNOWN (pass --event-start)'}")
    print(f"Published: {fmt_time(g['published'])}   last modified: {fmt_time(g['modified'])}")
    print("Links:")
    for l in d["links"]:
        print(f"  [{l['kind_hint']}] {l['url']}  ({', '.join(t['name'] for t in l['tags']) or 'no tag'})"
              f"{'  – ' + l['name'] if l['name'] else ''}")
    if d["description_urls"]:
        print(f"URLs in description: {len(d['description_urls'])}")
    opted = [x["name"] for x in d["grades"] if x["opted_out"]]
    print(f"Opted out of: {', '.join(opted) if opted else 'nothing'}")
    print(f"Comments: {len(d['comments'])}  author posts near event: {len(d['author_posts'])}  "
          f"other entries by author: {len(d['author_history'])}")
    for w in d["warnings"]:
        print(f"WARNING: {w}")
    if d["missing_optional_data"]:
        print(f"Optional data not fetched ({len(d['missing_optional_data'])} URLs) – see fetch_plan.json")
    print(f"Wrote {out / 'game.json'}")


if __name__ == "__main__":
    main()
