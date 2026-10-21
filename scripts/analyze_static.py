#!/usr/bin/env python3
"""Stage 2 – static analysis of the retrieved source against the event window.

* Git sources: finds the Compo cutoff commit (last commit whose history is
  entirely before the deadline + Submission Hour, or an explicit compo tag),
  checks it out to <workdir>/snapshot, and reconstructs per-file creation /
  last-edit times (following renames). Commits after the cutoff are listed and
  classified (bug-fix/port vs new content) instead of being analysed.
* Archive sources: uses the timestamps stored in the archive (UT/NTFS/DOS/7z/tar).
* Plain folders: filesystem times (flagged as unreliable).
* Embedded timestamps (PNG tIME, XMP, EXIF, Krita, BWF, ID3, fonts...) are read
  for every asset, independent of the source type.
* If a played build was retrieved, it is matched to the commit it was built from.

Writes <workdir>/stage2.json (full data) and <workdir>/stage2.md (summary).

Usage: analyze_static.py --workdir DIR [--source PATH] [--start ISO] [--cutoff SHA]
                         [--subpath DIR] [--lfs]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archives import list_entries  # noqa: E402
from common import (UTC, Window, fmt_time, git, git_blob_shas_tolerant, human_int,  # noqa: E402
                    iso, load_json, log, pct, read_text, run, save_json)
from filetypes import classify, count_lines, is_own, looks_third_party_text  # noqa: E402
from media_meta import AI_GEN_RE, C2PA_RE, file_meta  # noqa: E402

COMPO_TAG_RE = re.compile(r"(compo|ld[-_ ]?\d+|ludum|jam|submission|submitted|rated|event|^v?1\.0(\.0)?$|final)", re.I)
POST_TAG_RE = re.compile(r"(post|after|update|patch|fix|v?1\.[1-9]|v?[2-9]\.\d)", re.I)
BUGFIX_MSG_RE = re.compile(r"\b(fix|fixes|fixed|bug|crash|typo|build|export|web|html5?|port|android|mac|"
                           r"linux|windows|readme|license|itch|deploy|gh-pages|pages|ci|workflow|version|"
                           r"credits|screenshot|cover|icon)\b", re.I)
CONTENT_MSG_RE = re.compile(r"\b(add|added|adds|new|feature|level|levels|music|sound|sfx|art|sprite|"
                            r"polish|juice|post[- ]?(compo|jam|ld)|update|improve|rework|content|boss|"
                            r"enemy|enemies|mechanic)\b", re.I)
CREDIT_RE = re.compile(r"(kenney|opengameart|freesound|asset ?pack|asset store|cc0|cc[- ]by|creative commons|"
                       r"licensed under|(music|art|artwork|sprites?|sounds?|sfx|tileset|font|graphics|audio|"
                       r"models?|textures?|voices?) by (?!me\b|myself\b|the author\b|yours truly\b)|"
                       r"thanks? (to )?\S+ for (the )?(music|art|sounds?|sprites?)|"
                       r"made with .{0,20}(assets?|pack)|from (itch\.io|unity asset store|humble))", re.I)
FREESOUND_RE = re.compile(r"(^|/)\d{2,7}__[^/]+__[^/]+\.(wav|mp3|ogg|flac|aiff?)$", re.I)
AI_TRAILER_RE = re.compile(r"(co-authored-by:.*(claude|anthropic|copilot|cursor|codex|openai|chatgpt|gpt|"
                           r"devin|aider|jules|gemini|windsurf|codeium|replit|lovable|bolt|amp|factory|cody|"
                           r"junie|kiro|amazon q)|generated with \[?claude code|🤖 generated with|"
                           r"replit-commit-author|^aider: |\(aider\)|noreply@anthropic\.com)", re.I | re.M)


# =============================================================== git
def git_commits(repo, ref):
    fmt = "%H%x1f%P%x1f%an%x1f%ae%x1f%at%x1f%cn%x1f%ce%x1f%ct%x1f%s%x1f%b%x1e"
    r = git(repo, "log", "--topo-order", "--reverse", f"--format={fmt}", ref, timeout=600)
    commits = []
    for rec in r.stdout.split("\x1e"):
        rec = rec.lstrip("\n")
        if not rec.strip():
            continue
        f = rec.split("\x1f")
        if len(f) < 10:
            continue
        sha, parents, an, ae, at, cn, ce, ct, subj, body = f[:10]
        commits.append({"sha": sha, "parents": parents.split(), "author": an, "email": ae.lower(),
                        "author_time": dt.datetime.fromtimestamp(int(at), UTC),
                        "committer": cn, "committer_email": ce.lower(),
                        "commit_time": dt.datetime.fromtimestamp(int(ct), UTC),
                        "subject": subj, "body": body.strip()})
    return commits


def first_parent_chain(repo, ref):
    r = git(repo, "rev-list", "--first-parent", "--reverse", ref)
    return r.stdout.split()


def ref_markers(repo):
    out = []
    r = git(repo, "for-each-ref", "--format=%(refname)%1f%(objectname)%1f%(*objectname)%1f%(creatordate:unix)",
            "refs/tags", "refs/remotes", "refs/heads")
    for line in r.stdout.splitlines():
        p = line.split("\x1f")
        if len(p) < 4:
            continue
        name = re.sub(r"^refs/(tags|remotes/origin|heads)/", "", p[0])
        kind = "tag" if p[0].startswith("refs/tags/") else "branch"
        if name in ("HEAD",):
            continue
        out.append({"name": name, "kind": kind, "commit": p[2] or p[1],
                    "date": iso(dt.datetime.fromtimestamp(int(p[3]), UTC)) if p[3].isdigit() else None,
                    "compo_like": bool(COMPO_TAG_RE.search(name)), "post_like": bool(POST_TAG_RE.search(name))})
    return out


def find_cutoff(commits, chain, win, markers, linked_ref_sha=None, manual=None):
    by_sha = {c["sha"]: c for c in commits}
    deadline = win.submission_end
    tainted = {}
    for c in commits:  # topo order: parents first
        late = c["author_time"] > deadline
        tainted[c["sha"]] = late or any(tainted.get(p, False) for p in c["parents"])
    on_chain = set(chain)
    inversions = [c for c in commits if c["author_time"] <= deadline and tainted[c["sha"]]]
    notes = []
    if manual:
        return manual, "manually specified", notes, inversions
    clean = [c for c in commits if not tainted[c["sha"]]]
    if linked_ref_sha and linked_ref_sha in by_sha:
        if not tainted[linked_ref_sha]:
            notes.append("The ldjam link points at a specific ref; its commit predates the deadline.")
            return linked_ref_sha, "ref linked from the Ludum Dare page", notes, inversions
        notes.append("The ldjam link points at a ref that includes post-deadline commits.")
    good_markers = [m for m in markers if m["compo_like"] and not m["post_like"] and m["commit"] in by_sha
                    and not tainted[m["commit"]]]
    late_markers = [m for m in markers if m["compo_like"] and not m["post_like"] and m["commit"] in by_sha
                    and tainted[m["commit"]]]
    for m in late_markers:
        notes.append(f"{m['kind']} '{m['name']}' looks like a compo marker but points to a post-deadline commit.")
    if good_markers:
        good_markers.sort(key=lambda m: by_sha[m["commit"]]["author_time"], reverse=True)
        m = good_markers[0]
        best_clean = max(clean, key=lambda c: c["author_time"]) if clean else None
        if best_clean and best_clean["sha"] != m["commit"]:
            notes.append(f"{m['kind']} '{m['name']}' marks {m['commit'][:8]}; the last pre-deadline commit is "
                         f"{best_clean['sha'][:8]} ({fmt_time(best_clean['author_time'])}).")
        return m["commit"], f"{m['kind']} '{m['name']}'", notes, inversions
    if not clean:
        return None, "no commit predates the deadline", notes, inversions
    best = max(clean, key=lambda c: (c["author_time"], c["sha"] in on_chain))
    return best["sha"], "last commit whose whole history predates the deadline + Submission Hour", notes, inversions


NUMSTAT_RE = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")


def _rename_paths(spec):
    """'a/{b => c}/d' or 'a => b' -> (old, new)."""
    spec = spec.strip('"')
    m = re.match(r"^(.*)\{(.*) => (.*)\}(.*)$", spec)
    if m:
        pre, a, b, post = m.groups()
        old = re.sub(r"/+", "/", f"{pre}{a}{post}").strip("/")
        new = re.sub(r"/+", "/", f"{pre}{b}{post}").strip("/")
        return old, new
    if " => " in spec:
        a, b = spec.split(" => ", 1)
        return a, b
    return None, spec


def file_history(repo, rev, win, pathspec=None):
    """Replay `git log --numstat --summary -M` (oldest first) to get per-file
    creation / last-modification times and lines added per phase."""
    fmt = "\x1e%H\x1f%at\x1f%ct\x1f%an\x1f%ae"
    args = ["log", "--topo-order", "--reverse", "-M", "--numstat", "--summary", f"--format={fmt}", rev]
    if pathspec:
        args += ["--", pathspec]
    out = git(repo, *args, timeout=900).stdout
    files = {}
    per_commit = {}
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        lines = rec.split("\n")
        sha, at, ct, an, ae = (lines[0].split("\x1f") + [""] * 5)[:5]
        t = dt.datetime.fromtimestamp(int(at), UTC)
        phase = win.phase(t)
        creates, deletes, renames, stats = set(), set(), [], []
        for ln in lines[1:]:
            if not ln.strip():
                continue
            m = NUMSTAT_RE.match(ln)
            if m:
                a = int(m.group(1)) if m.group(1).isdigit() else 0
                d = int(m.group(2)) if m.group(2).isdigit() else 0
                old, new = _rename_paths(m.group(3))
                stats.append((a, d, old, new, m.group(1) == "-"))
                continue
            s = ln.strip()
            if s.startswith("create mode"):
                creates.add(s.split(" ", 3)[3].strip('"'))
            elif s.startswith("delete mode"):
                deletes.add(s.split(" ", 3)[3].strip('"'))
            elif s.startswith("rename ") or s.startswith("copy "):
                body = re.sub(r" \(\d+%\)$", "", s.split(" ", 1)[1])
                old, new = _rename_paths(body)
                renames.append((old, new, s.startswith("copy ")))
        for old, new, is_copy in renames:
            if old in files:
                files[new] = dict(files[old]) if is_copy else files.pop(old)
                files[new]["renamed_from"] = old
            else:
                files[new] = _new_entry(sha, t, phase)
        for p in creates:
            if p not in files:
                files[p] = _new_entry(sha, t, phase)
        for p in deletes:
            files.pop(p, None)
        added_total = 0
        pc = per_commit.setdefault(sha, {"time": t, "phase": phase, "files": [], "added": 0, "deleted": 0,
                                         "created": len(creates)})
        for a, d, old, new, binary in stats:
            if new in deletes:
                continue
            e = files.setdefault(new, _new_entry(sha, t, phase))
            e["modified"], e["modified_sha"] = t, sha
            e["commits"] += 1
            e["added"] += a
            e[f"added_{phase}"] = e.get(f"added_{phase}", 0) + a
            added_total += a
            pc["files"].append((new, a, d, binary))
            pc["deleted"] += d
        pc["added"] += added_total
    return files, per_commit


def _new_entry(sha, t, phase):
    return {"created": t, "created_sha": sha, "created_phase": phase, "modified": t, "modified_sha": sha,
            "commits": 0, "added": 0}


def analyze_git(repo, ref, subpath, win, wd, args, s1src):
    repo = Path(repo)
    res = {"mode": "git", "repo": str(repo), "ref": ref, "subpath": subpath, "notes": [], "flags": []}
    if git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}").returncode != 0:
        ref = "HEAD"
        res["ref"] = ref
    commits = git_commits(repo, ref)
    if not commits:
        raise SystemExit("git repository has no commits on " + ref)
    chain = first_parent_chain(repo, ref)
    markers = ref_markers(repo)
    linked = None
    if s1src.get("linked_ref"):
        r = git(repo, "rev-parse", s1src["linked_ref"] + "^{commit}")
        linked = r.stdout.strip() or None
    manual = None
    if args.cutoff:
        r = git(repo, "rev-parse", args.cutoff + "^{commit}")
        manual = r.stdout.strip() or None
    cutoff, why, notes, inversions = find_cutoff(commits, chain, win, markers, linked, manual)
    res["notes"] += notes
    res["markers"] = [m for m in markers if m["compo_like"] or m["post_like"]]
    first, last = commits[0], commits[-1]
    res["history"] = {"commits": len(commits), "first": iso(first["author_time"]), "last": iso(last["author_time"]),
                      "first_parent_length": len(chain)}
    if first["author_time"] > win.submission_end:
        res["flags"].append(("HIGH", "HISTORY_AFTER_DEADLINE",
                             f"The repository history starts {fmt_time(first['author_time'])}, after the deadline: "
                             f"commit times cannot show the work happened during the Compo."))
    if inversions:
        res["flags"].append(("HIGH", "DATE_INVERSION",
                             f"{len(inversions)} commit(s) dated before the deadline come after post-deadline "
                             f"commits (dates edited or history rewritten), e.g. {inversions[0]['sha'][:8]}."))
    rebased = [c for c in commits if (c["commit_time"] - c["author_time"]).total_seconds() > 6 * 3600
               and win.phase(c["author_time"]) in ("during", "submission_hour")]
    if rebased:
        res["notes"].append(f"{len(rebased)} in-event commit(s) were re-committed later (committer date ≫ author "
                            f"date: rebase/amend/cherry-pick). Author dates are used.")
    future = [c for c in commits if c["author_time"] > dt.datetime.now(UTC) + dt.timedelta(days=1)]
    if future:
        res["flags"].append(("MEDIUM", "FUTURE_DATES", f"{len(future)} commit(s) are dated in the future."))

    if cutoff is None:
        res["flags"].append(("HIGH", "NO_PRE_DEADLINE_COMMIT",
                             "No commit predates the deadline; analysing the latest commit instead."))
        cutoff, why = commits[-1]["sha"], "latest commit (no pre-deadline commit exists)"
    by_sha = {c["sha"]: c for c in commits}
    cc = by_sha.get(cutoff) or {"author_time": None, "subject": ""}
    res["cutoff"] = {"sha": cutoff, "why": why, "time": iso(cc["author_time"]), "subject": cc.get("subject")}

    # ---- post-deadline commits
    post = git(repo, "rev-list", f"{cutoff}..{ref}").stdout.split()
    res["post_cutoff"] = classify_post_commits(repo, post, by_sha)

    # ---- snapshot
    snap = wd / "snapshot"
    run(["git", "-C", str(repo), "worktree", "remove", "--force", str(snap)], timeout=120)
    run(["git", "-C", str(repo), "worktree", "prune"], timeout=60)
    if snap.exists():
        import shutil
        shutil.rmtree(snap)
    r = run(["git", "-C", str(repo), "worktree", "add", "--detach", "--force", str(snap), cutoff],
            timeout=1200, env={"GIT_LFS_SKIP_SMUDGE": "0" if args.lfs else "1"})
    if r.returncode != 0:
        raise SystemExit(f"could not check out the cutoff commit: {r.stderr[-400:]}")
    res["snapshot"] = str(snap)

    # ---- per-file history up to the cutoff
    hist, per_commit = file_history(repo, cutoff, win)
    tree = git(repo, "ls-tree", "-r", "--name-only", cutoff).stdout.splitlines()
    res["tree_files"] = len(tree)
    missing = [p for p in tree if p not in hist]
    for p in missing[:300]:  # files that only arrived through merges
        r = git(repo, "log", "--format=%at", "--diff-filter=A", "-1", cutoff, "--", p)
        if r.stdout.strip():
            t = dt.datetime.fromtimestamp(int(r.stdout.split()[0]), UTC)
            hist[p] = _new_entry("?", t, win.phase(t))
    res["file_times"] = {p: hist[p] for p in tree if p in hist}

    # ---- commit timeline (up to cutoff) for stage 3
    timeline, idents = [], collections.Counter()
    for c in commits:
        pc = per_commit.get(c["sha"])
        if pc is None:
            continue
        msg = (c["subject"] + "\n" + c["body"]).strip()
        timeline.append({"sha": c["sha"][:10], "time": iso(c["author_time"]), "commit_time": iso(c["commit_time"]),
                         "phase": pc["phase"], "author": c["author"], "email": c["email"],
                         "added": pc["added"], "deleted": pc["deleted"], "files_changed": len(pc["files"]),
                         "files_created": pc["created"], "message": msg[:600],
                         "ai_trailer": bool(AI_TRAILER_RE.search(msg) or AI_TRAILER_RE.search(c["author"])),
                         "file_stats": [(f, a, d) for f, a, d, b in pc["files"][:400]]})
        if pc["phase"] in ("during", "submission_hour"):
            idents[_ident(c)] += 1
    res["timeline"] = timeline
    res["identities_in_window"] = dict(idents)
    humans = [i for i in idents if not re.search(r"\[bot\]|noreply@github\.com$|^github$", i, re.I)]
    if len(humans) > 1:
        res["flags"].append(("MEDIUM", "MULTIPLE_IDENTITIES",
                             f"{len(humans)} different committer identities during the event ({', '.join(humans)}). "
                             f"The Compo is solo – check whether they are the same person."))
    bulk = [t for t in timeline if t["files_created"] >= max(30, 0.5 * len(tree))]
    if bulk:
        b = bulk[0]
        sev = "MEDIUM" if b["phase"] in ("during", "submission_hour") else "HIGH"
        res["flags"].append((sev, "BULK_COMMIT",
                             f"Commit {b['sha']} ({fmt_time(b['time'])}, {b['phase']}) added {b['files_created']} "
                             f"files at once: per-file creation times inside it are not meaningful."))
    in_window = [t for t in timeline if t["phase"] in ("during", "submission_hour")]
    if len(in_window) <= 2 and len(tree) > 20:
        res["flags"].append(("MEDIUM", "FEW_COMMITS",
                             f"Only {len(in_window)} commit(s) during the event: the history gives little "
                             f"evidence of how the work progressed."))
    if s1src.get("uses_lfs") and not args.lfs:
        res["notes"].append("The repository stores some files in Git LFS; they were not downloaded "
                            "(rerun with --lfs to inspect asset contents).")
    # compo-looking refs that are not part of the analysed history
    reachable = {c["sha"] for c in commits}
    for m in markers:
        if m["compo_like"] and not m["post_like"] and m["commit"] not in reachable:
            t = git(repo, "show", "-s", "--format=%at", m["commit"]).stdout.strip()
            when = dt.datetime.fromtimestamp(int(t), UTC) if t.isdigit() else None
            res["notes"].append(f"{m['kind']} '{m['name']}' ({fmt_time(when)}) is not on the analysed branch – "
                                f"if it is the compo version, rerun with --ref {m['name']}.")
    return res


def _ident(c):
    email = c["email"]
    m = re.match(r"(\d+\+)?([^@]+)@users\.noreply\.github\.com", email)
    return (m.group(2) if m else email) or c["author"]


def classify_post_commits(repo, shas, by_sha):
    out = []
    for sha in shas[:200]:
        r = git(repo, "show", "--numstat", "--format=", "-M", sha)
        added = deleted = 0
        new_assets, code_added, files = 0, 0, []
        for ln in r.stdout.splitlines():
            m = NUMSTAT_RE.match(ln)
            if not m:
                continue
            a = int(m.group(1)) if m.group(1).isdigit() else 0
            d = int(m.group(2)) if m.group(2).isdigit() else 0
            _, path = _rename_paths(m.group(3))
            info = classify(path)
            added += a
            deleted += d
            files.append(path)
            if info["category"] == "code" and is_own(info):
                code_added += a
        cr = git(repo, "show", "--summary", "--format=", sha).stdout
        for ln in cr.splitlines():
            if ln.strip().startswith("create mode"):
                p = ln.strip().split(" ", 3)[3]
                if classify(p)["category"] in ("art", "art_3d", "audio", "music_raw", "art_raw", "level", "video"):
                    new_assets += 1
        c = by_sha.get(sha, {})
        msg = (c.get("subject", "") + " " + c.get("body", "")).strip()
        only_docs = files and all(classify(f)["category"] in ("doc", "config") for f in files)
        if only_docs:
            kind = "docs/config only"
        elif new_assets or code_added > 150 or (CONTENT_MSG_RE.search(msg) and not BUGFIX_MSG_RE.search(msg)):
            kind = "content/feature"
        elif BUGFIX_MSG_RE.search(msg) and code_added <= 80:
            kind = "bug-fix/port"
        else:
            kind = "unclear"
        out.append({"sha": sha[:10], "time": iso(c.get("author_time")), "message": msg[:200],
                    "files": len(files), "added": added, "deleted": deleted, "own_code_added": code_added,
                    "new_assets": new_assets, "kind": kind})
    return out


# =============================================================== archive / folder times
def archive_times(archive_path, extracted_root: Path, win):
    entries = list_entries(archive_path) or []
    files = [e for e in entries if not e["is_dir"] and not e["name"].startswith("__MACOSX/")]
    res = {"mode": "archive", "archive": str(archive_path), "entries": len(files), "notes": [], "flags": []}
    if not files:
        res["notes"].append("Archive timestamps unavailable (format not readable here).")
        return res, {}
    mt = [e["mtime"] for e in files if e.get("mtime")]
    tz_known = all(e.get("tz_known") for e in files)
    res["timestamp_source"] = files[0].get("mtime_src")
    res["has_creation_times"] = any(e.get("ctime") for e in files)
    if mt:
        common_t, n = collections.Counter(int(t.timestamp()) // 2 for t in mt).most_common(1)[0]
        if n >= 0.9 * len(mt) and len(mt) >= 5:
            res["flags"].append(("MEDIUM", "UNIFORM_TIMESTAMPS",
                                 f"{n}/{len(mt)} files share one timestamp ({fmt_time(dt.datetime.fromtimestamp(common_t * 2, UTC))}): "
                                 f"times were reset when the archive was made (e.g. a GitHub 'Download ZIP'), "
                                 f"so they say nothing about when files were created. Treated as unknown."))
            res["uniform"] = True
            return res, {}
    if not tz_known:
        res["notes"].append("Archive stores only DOS times (local time of the author's PC, zone unknown); "
                            "phases within ±14 h of the window edges are marked uncertain.")
    out = {}
    names = {e["name"].rstrip("/"): e for e in files}
    # map archive names to paths relative to the analysed root (strip a common top folder)
    for name, e in names.items():
        out[name] = {"created": e.get("ctime"), "created_src": e.get("ctime_src"),
                     "modified": e.get("mtime"), "modified_src": e.get("mtime_src"),
                     "tz_known": e.get("tz_known", True)}
    return res, out


def fs_times(root: Path):
    out = {}
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            st = p.stat()
            out[p.relative_to(root).as_posix()] = {
                "modified": dt.datetime.fromtimestamp(st.st_mtime, UTC), "modified_src": "filesystem",
                "created": None, "tz_known": True}
    return out


def phase_uncertain(win, t, tz_known):
    """Phase with ±14h tolerance when the zone is unknown."""
    if t is None:
        return None, False
    if tz_known:
        return win.phase(t), False
    slack = dt.timedelta(hours=14)
    if t + slack < win.start:
        return "before", False
    if t - slack > win.submission_end:
        return "after", False
    p = win.phase(t)
    near = (abs((t - win.start).total_seconds()) < 14 * 3600 or
            abs((t - win.compo_end).total_seconds()) < 14 * 3600)
    return p, near


# =============================================================== inventory
def detect_engine(root: Path, files):
    names = set(files)
    eng = []
    for p in files:
        low = p.lower()
        base = low.rsplit("/", 1)[-1]
        if base == "project.godot":
            txt = read_text(root / p, 20000)
            m = re.search(r'config/features=PackedStringArray\("(\d+\.\d+)', txt)
            eng.append(f"Godot {m.group(1)}" if m else ("Godot 3.x" if "config_version=4" in txt else "Godot"))
        elif low.endswith("projectsettings/projectversion.txt"):
            m = re.search(r"m_EditorVersion: (\S+)", read_text(root / p, 4000))
            eng.append(f"Unity {m.group(1)}" if m else "Unity")
        elif base.endswith(".uproject"):
            eng.append("Unreal")
        elif base.endswith(".yyp") or base.endswith(".project.gmx"):
            eng.append("GameMaker")
        elif base == "game.project":
            eng.append("Defold")
        elif base == "conf.lua" or (base == "main.lua" and any(n.endswith("conf.lua") for n in names)):
            eng.append("LÖVE")
        elif base.endswith(".p8") or base.endswith(".p8.png"):
            eng.append("PICO-8")
        elif base.endswith((".c3p", ".capx", ".c3proj")):
            eng.append("Construct")
        elif base == "game.json" and "gdVersion" in read_text(root / p, 4000):
            eng.append("GDevelop")
        elif base.endswith(".sb3"):
            eng.append("Scratch")
        elif base.endswith(".pyxres"):
            eng.append("Pyxel")
        elif base == "package.json":
            txt = read_text(root / p, 40000).lower()
            for lib in ("phaser", "three", "pixi.js", "kaboom", "kaplay", "excalibur", "littlejsengine",
                        "p5", "babylonjs", "playcanvas", "melonjs", "kontra", "react", "vite"):
                if f'"{lib}"' in txt:
                    eng.append(f"JS ({lib})")
        elif base == "cargo.toml":
            txt = read_text(root / p, 20000).lower()
            for lib in ("bevy", "macroquad", "ggez", "fyrox", "comfy", "raylib", "notan"):
                if re.search(rf"^\s*{lib}\s*=", txt, re.M):
                    eng.append(f"Rust ({lib})")
        elif base.endswith(".hxml") or base == "project.xml":
            eng.append("Haxe")
        elif base.endswith(".csproj") and "monogame" in read_text(root / p, 20000).lower():
            eng.append("MonoGame")
    return sorted(set(eng))


def project_roots(files):
    roots = collections.Counter()
    for p in files:
        base = p.rsplit("/", 1)[-1].lower()
        d = p.rsplit("/", 1)[0] if "/" in p else ""
        if base in ("project.godot", "game.project", "package.json", "cargo.toml", "conf.lua") or \
                base.endswith((".yyp", ".uproject", ".c3p", ".capx")):
            roots[d] += 1
        elif p.lower().endswith("projectsettings/projectversion.txt"):
            roots[p.rsplit("/", 2)[0] if p.count("/") >= 2 else ""] += 1
    return sorted(roots)


def build_inventory(root: Path, times_for, win, args):
    records = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".git" in p.relative_to(root).parts:
            continue
        rel = p.relative_to(root).as_posix()
        try:
            size = p.stat().st_size
            with p.open("rb") as fh:
                head = fh.read(512)
        except OSError:
            continue
        info = classify(rel, head)
        rec = {"path": rel, "category": info["category"], "ext": info["ext"], "lang": info.get("lang"),
               "size": size, "third_party": info["third_party"], "tp_reason": info["tp_reason"],
               "generated": info["generated"], "gen_reason": info["gen_reason"],
               "build_dir": info.get("build_dir", False), "engine_default": info.get("engine_default", False),
               "possible_vendor_dir": info.get("possible_vendor_dir")}
        if head.startswith(b"version https://git-lfs.github.com/spec/v1"):
            rec["lfs_pointer"] = True
        if info["category"] in ("code", "markup", "narrative") and size < 3_000_000 and not rec.get("lfs_pointer"):
            text = read_text(p)
            lc = count_lines(text, info.get("style"), keep_comments=False, pico8=info["ext"] in (".p8",))
            rec["loc"] = {k: lc[k] for k in ("lines", "code", "comment", "blank")}
            if lc["minified"]:
                rec["generated"], rec["gen_reason"] = True, "minified"
            if looks_third_party_text(text[:4000]) and not rec["third_party"]:
                rec["third_party"], rec["tp_reason"] = True, "license header (verify)"
        elif info["category"] == "code" and info["ext"] == ".p8.png":
            rec["loc"] = None
        rec["own"] = not rec["third_party"] and not rec["generated"] and not rec["build_dir"]
        # ---- timestamps from git / archive / filesystem
        t = times_for(rel)
        if t:
            created, modified = t.get("created"), t.get("modified")
            tzk = t.get("tz_known", True)
            rec["created"], rec["created_src"] = iso(created), t.get("created_src", "git first commit" if "created_sha" in t else None)
            rec["modified"], rec["modified_src"] = iso(modified), t.get("modified_src", "git last commit" if "modified_sha" in t else None)
            rec["created_phase"], u1 = phase_uncertain(win, created, tzk)
            rec["modified_phase"], u2 = phase_uncertain(win, modified, tzk)
            rec["time_uncertain"] = u1 or u2
            if created is None and rec["modified_phase"] == "before":
                # a file last modified before the event necessarily existed before it
                rec["created_phase"] = "before"
                rec["created_src"] = "inferred (unmodified since before the event)"
            if "added" in t:
                rec["lines_added"] = t.get("added", 0)
                rec["lines_added_during"] = t.get("added_during", 0) + t.get("added_submission_hour", 0)
                rec["lines_added_before"] = t.get("added_before", 0)
                rec["commits"] = t.get("commits", 0)
            if t.get("renamed_from"):
                rec["renamed_from"] = t["renamed_from"]
        # ---- embedded metadata for assets
        if info["category"] in ("art", "art_raw", "audio", "font", "doc") and size < 400_000_000 \
                and not rec.get("lfs_pointer"):
            m = file_meta(p, info["category"])
            if m:
                emb = []
                for label, tt in m.get("times", []):
                    emb.append([label, iso(tt), win.phase(tt)])
                rec["embedded_times"] = emb
                tools = [x for x in m.get("tools", []) if x]
                if tools:
                    rec["tools"] = sorted(set(tools))[:6]
                meta_text = " ".join([m.get("meta_text") or ""] + tools)
                hits = sorted({h.group(0).lower() for h in AI_GEN_RE.finditer(meta_text)})
                if hits:
                    rec["ai_meta_hits"] = hits[:10]
                if m.get("c2pa") or C2PA_RE.search(meta_text or ""):
                    rec["c2pa"] = True
                if m.get("generation_params"):
                    rec["generation_params"] = True
                if m.get("digital_source_type"):
                    rec["digital_source_type"] = m["digital_source_type"]
                keep = {k: m[k] for k in ("width", "height", "frames", "layers", "cels", "tags", "duration",
                                          "sample_rate", "channels", "editing_seconds", "editing_cycles",
                                          "timelapse_segments", "palette_colors", "animated", "names",
                                          "bitrate_kbps", "vendor", "blender_version") if m.get(k) is not None}
                if keep:
                    rec["media"] = keep
                if m.get("text") and info["ext"] == ".png":
                    rec["png_text_keys"] = sorted(m["text"].keys())[:12]
        records.append(rec)
    return records


# =============================================================== build matching
def match_build(repo, ref, build_path: Path, project_prefix: str, win, cutoff, max_commits=400):
    from godot_pck import read_pck, source_paths
    blobs = {}
    src_paths = set()
    text_ext = (".gd", ".tscn", ".tres", ".json", ".cfg", ".txt", ".gdshader", ".shader", ".csv", ".md",
                ".lua", ".js", ".html", ".css", ".py", ".ink", ".yarn", ".dialogue")
    for p in build_path.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() == ".pck" or (p.suffix.lower() in (".exe", ".x86_64", "") and p.stat().st_size > 1_000_000):
            pck = read_pck(p, read_data_for=lambda s: s.lower().endswith(text_ext))
            if pck.get("files"):
                src_paths |= source_paths(pck)
                for f in pck["files"]:
                    if f.get("data") is not None:
                        for s in git_blob_shas_tolerant(f["data"]):
                            blobs[s] = "pck:" + f["path"]
                continue
        if p.suffix.lower() == ".love":
            import zipfile
            try:
                with zipfile.ZipFile(p) as z:
                    for n in z.namelist():
                        if not n.endswith("/"):
                            for s in git_blob_shas_tolerant(z.read(n)):
                                blobs[s] = "love:" + n
                continue
            except zipfile.BadZipFile:
                pass
        if p.stat().st_size <= 50_000_000:
            for s in git_blob_shas_tolerant(p.read_bytes()):
                blobs[s] = p.relative_to(build_path).as_posix()
    if not blobs and not src_paths:
        return {"status": "no comparable files in the build"}
    shas = git(repo, "rev-list", "--topo-order", ref).stdout.split()[:max_commits]
    pre = set(git(repo, "rev-list", cutoff).stdout.split()) if cutoff else set()
    results = []
    all_repo_blobs = set()
    for sha in shas:
        lt = git(repo, "ls-tree", "-r", sha).stdout.splitlines()
        tree = {}
        for ln in lt:
            meta, _, path = ln.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob":
                tree[path] = parts[2]
        tb = set(tree.values())
        all_repo_blobs |= tb
        hits = len(set(blobs) & tb)
        sim = 0.0
        if src_paths:
            rel = {p[len(project_prefix):].lstrip("/") for p in tree if p.startswith(project_prefix)}
            rel = {p for p in rel if not p.startswith((".", "addons/")) and
                   not p.lower().endswith((".import", ".uid", ".md", ".txt", "license", ".gitignore"))}
            sim = len(src_paths & rel) / max(1, len(src_paths | rel))
        results.append({"sha": sha, "hits": hits, "path_similarity": round(sim, 3), "pre_deadline": sha in pre})
    comparable = len(set(blobs) & all_repo_blobs)
    if not results:
        return {"status": "no commits"}
    best_score = max((r["hits"], r["path_similarity"]) for r in results)
    best = [r for r in results if (r["hits"], r["path_similarity"]) == best_score]
    status = "inconclusive"
    if best_score[0] == 0 and best_score[1] < 0.5:
        status = "inconclusive"
    elif any(r["pre_deadline"] for r in best):
        status = "matches a pre-deadline commit"
    else:
        status = "matches only post-deadline commits"
    times = {}
    for r in best[:50]:
        t = git(repo, "show", "-s", "--format=%at", r["sha"]).stdout.strip()
        times[r["sha"]] = iso(dt.datetime.fromtimestamp(int(t), UTC)) if t.isdigit() else None
    return {"status": status, "build_files_hashed": len(blobs), "build_files_found_in_history": comparable,
            "pck_resource_paths": len(src_paths), "best_hits": best_score[0],
            "best_path_similarity": best_score[1], "best_commits": [dict(r, time=times.get(r["sha"])) for r in best[:20]],
            "cutoff_in_best": any(r["sha"] == cutoff for r in best)}


# =============================================================== summary
def summarise(records, win, src_info):
    own = [r for r in records if r["own"]]
    S = {}

    def phases(rs, key):
        c = collections.Counter(r.get(key) or "unknown" for r in rs)
        return {k: c.get(k, 0) for k in ("during", "submission_hour", "before", "after", "unknown")}

    S["files_total"], S["files_own"] = len(records), len(own)
    S["created_all"], S["created_own"] = phases(records, "created_phase"), phases(own, "created_phase")
    S["modified_all"], S["modified_own"] = phases(records, "modified_phase"), phases(own, "modified_phase")
    code_own = [r for r in own if r["category"] == "code" and r.get("loc")]
    S["loc_own_code"] = sum(r["loc"]["code"] for r in code_own)
    S["loc_own_lines"] = sum(r["loc"]["lines"] for r in code_own)
    created_in = [r for r in code_own if r.get("created_phase") in ("during", "submission_hour")]
    S["loc_written_during"] = {"code": sum(r["loc"]["code"] for r in created_in),
                               "comment": sum(r["loc"]["comment"] for r in created_in),
                               "blank": sum(r["loc"]["blank"] for r in created_in),
                               "lines": sum(r["loc"]["lines"] for r in created_in), "files": len(created_in)}
    S["lines_added_during_git"] = sum(r.get("lines_added_during", 0) for r in code_own) if any(
        "lines_added_during" in r for r in code_own) else None
    base = [r for r in code_own if r.get("created_phase") == "before"]
    S["base_code"] = {"files": len(base), "code": sum(r["loc"]["code"] for r in base),
                      "paths": [r["path"] for r in base][:30]}
    by_lang = collections.Counter()
    for r in code_own:
        by_lang[r["lang"] or "?"] += r["loc"]["code"]
    S["loc_by_language"] = dict(by_lang.most_common())
    narrative = [r for r in own if r["category"] == "narrative" and r.get("loc")]
    S["narrative_lines"] = sum(r["loc"]["code"] for r in narrative)

    def ext_counts(rs):
        return dict(collections.Counter(r["ext"] for r in rs).most_common())
    cat = lambda c, rs=own: [r for r in rs if r["category"] == c]  # noqa: E731
    S["artwork"] = {"count": len(cat("art")) + len(cat("art_3d")), "by_ext": ext_counts(cat("art") + cat("art_3d"))}
    S["raw_files"] = {"count": len(cat("art_raw")) + len(cat("music_raw")),
                      "art": ext_counts(cat("art_raw")), "audio_music": ext_counts(cat("music_raw"))}
    S["audio"] = {"count": len(cat("audio")), "by_ext": ext_counts(cat("audio"))}
    S["fonts"], S["video"], S["docs"] = len(cat("font")), len(cat("video")), len(cat("doc"))
    S["levels"] = len(cat("level"))
    S["by_category_all"] = dict(collections.Counter(r["category"] for r in records).most_common())
    S["by_category_own"] = dict(collections.Counter(r["category"] for r in own).most_common())
    S["by_ext_all"] = ext_counts(records)
    S["by_ext_own"] = ext_counts(own)
    S["third_party_files"] = sum(1 for r in records if r["third_party"])
    S["generated_files"] = sum(1 for r in records if r["generated"])
    S["build_dir_files"] = sum(1 for r in records if r["build_dir"])
    return S


def rule_flags(records, S, win, game, src_flags):
    flags = list(src_flags)
    own = [r for r in records if r["own"]]
    content_cats = ("art", "art_3d", "art_raw", "audio", "music_raw", "video", "level")
    pre_assets = [r for r in own if r["category"] in content_cats and r.get("created_phase") == "before"
                  and not r.get("engine_default")]
    if pre_assets:
        flags.append(("HIGH", "PRE_EVENT_ASSETS",
                      f"{len(pre_assets)} asset file(s) existed before the event (Compo content must be created "
                      f"during the 48h): " + ", ".join(r["path"] for r in pre_assets[:12])))
    if S["base_code"]["files"]:
        flags.append(("INFO", "BASE_CODE",
                      f"{S['base_code']['files']} own code file(s) ({human_int(S['base_code']['code'])} lines) predate "
                      f"the event. Base code is allowed, but it should be declared and anything needed to build "
                      f"must be public: " + ", ".join(S["base_code"]["paths"][:8])))
    old_emb = [r for r in own if r["category"] in content_cats and any(e[2] == "before" for e in r.get("embedded_times", [])
                                                                     if not e[0].startswith("font"))]
    if old_emb:
        flags.append(("MEDIUM", "OLD_EMBEDDED_TIMESTAMP",
                      f"{len(old_emb)} asset(s) carry internal timestamps from before the event (reused or "
                      f"pre-made content?): " + ", ".join(f"{r['path']} ({next(e[1] for e in r['embedded_times'] if e[2] == 'before')[:10]})"
                                                          for r in old_emb[:10])))
    tp_assets = [r for r in records if r["third_party"] and r["category"] in content_cats]
    if tp_assets:
        dirs = sorted({r["path"].split("/")[0] for r in tp_assets})
        flags.append(("HIGH", "THIRD_PARTY_ASSETS",
                      f"{len(tp_assets)} art/audio file(s) live in third-party folders ({', '.join(dirs[:6])}). "
                      f"Pre-made assets are not allowed in the Compo (except fonts) – check whether they are used."))
    fs = [r["path"] for r in records if FREESOUND_RE.search(r["path"])]
    if fs:
        flags.append(("HIGH", "FREESOUND_FILES", f"{len(fs)} file(s) named like Freesound downloads: " + ", ".join(fs[:8])))
    fonts = [r["path"] for r in records if r["category"] == "font"]
    if fonts:
        flags.append(("INFO", "FONTS", f"{len(fonts)} font file(s) – fonts are an allowed exception: " + ", ".join(fonts[:6])))
    lfs = [r["path"] for r in records if r.get("lfs_pointer")]
    if lfs:
        flags.append(("INFO", "LFS_POINTERS", f"{len(lfs)} file(s) are Git LFS pointers (contents not downloaded)."))
    vend = sorted({r["possible_vendor_dir"] for r in records if r.get("possible_vendor_dir") and r["own"]})
    if vend:
        flags.append(("INFO", "POSSIBLE_LIBRARY_FOLDERS",
                      f"Folders that may hold libraries were counted as own code: {', '.join(vend)} – review."))
    return flags


def credit_scan(root: Path, records, game):
    hits = []
    texts = [("ldjam description", (game.get("game") or {}).get("body") or "")]
    for r in records:
        if r["category"] == "doc" and r["size"] < 300_000:
            texts.append((r["path"], read_text(root / r["path"], 300_000)))
    for src, txt in texts:
        for m in CREDIT_RE.finditer(txt):
            s = max(0, m.start() - 80)
            hits.append({"where": src, "match": m.group(0), "context": re.sub(r"\s+", " ", txt[s:m.end() + 100])})
    return hits[:40]


def timeline_reliability(out):
    """How far the creation/edit times can be trusted (0-1) and why."""
    src = out["source"]
    flags = {f[1] for f in out["flags"]}
    reasons = []
    if src.get("mode") == "git":
        score = 0.9
        reasons.append("git commit times come from the author's machine and can be edited, so the ceiling is 90%")
        n_win = sum(1 for t in src.get("timeline", []) if t["phase"] in ("during", "submission_hour"))
        if "HISTORY_AFTER_DEADLINE" in flags or "NO_PRE_DEADLINE_COMMIT" in flags:
            score = min(score, 0.1)
            reasons.append("history starts after the deadline – times cannot show work during the Compo")
        if "DATE_INVERSION" in flags:
            score *= 0.4
            reasons.append("commit dates go backwards across the deadline (edited dates or rewritten history)")
        if "BULK_COMMIT" in flags:
            score *= 0.6
            reasons.append("a bulk commit hides when individual files were made")
        if "FEW_COMMITS" in flags:
            score *= 0.7
            reasons.append(f"only {n_win} commit(s) during the event")
        elif n_win >= 15:
            reasons.append(f"{n_win} commits during the event give a fine-grained timeline")
        if any("re-committed later" in n for n in src.get("notes", [])):
            score *= 0.9
            reasons.append("some commits were rebased/amended later")
    elif src.get("mode") == "archive":
        if src.get("uniform"):
            score = 0.05
            reasons.append("all archive timestamps are identical (reset when packed)")
        else:
            tz = "DOS" not in (src.get("timestamp_source") or "")
            score = 0.55 if tz else 0.45
            reasons.append("archive stores last-modified times set by the author's machine" +
                           ("" if tz else " in an unknown time zone"))
            if src.get("has_creation_times"):
                score += 0.1
                reasons.append("creation times are stored too")
            else:
                reasons.append("no creation times: only 'edited before/after' is measurable")
    else:
        score = 0.0 if "UNRELIABLE_TIMES" in flags else 0.2
        reasons.append("filesystem times of copied/extracted files are usually reset")
    return {"score": round(score, 2), "reasons": reasons}


def render_md(out, game, win):
    S = out["summary"]
    src = out["source"]
    L = []
    g = game.get("game") or {}
    L.append(f"## Stage 2 – Static analysis: {g.get('name') or ''}")
    L.append(f"**Window:** {win.describe()}")
    if src["mode"] == "git":
        c = src["cutoff"]
        L.append(f"**Source:** git `{src.get('display') or src['repo']}` – analysed commit `{c['sha'][:10]}` "
                 f"({fmt_time(c['time'])}): {c['why']}.")
        post = src.get("post_cutoff", [])
        if post:
            kinds = collections.Counter(p["kind"] for p in post)
            L.append(f"**After the deadline:** {len(post)} commit(s) not analysed – " +
                     ", ".join(f"{v} {k}" for k, v in kinds.items()) + ".")
        else:
            L.append("**After the deadline:** no later commits on this branch.")
    elif src["mode"] == "archive":
        L.append(f"**Source:** archive `{Path(src['archive']).name}` – timestamps from {src.get('timestamp_source') or 'n/a'}"
                 + (" (creation times available)." if src.get("has_creation_times") else
                    ". The archive has no creation times: files untouched since before the event count as "
                    "created before it; for the rest, creation time is unknown (only the last edit is known)."))
    else:
        L.append("**Source:** plain folder – filesystem times only (usually reset on extraction: unreliable).")
    if out.get("analysis_root_note"):
        L.append(out["analysis_root_note"])
    tr = out.get("timeline_reliability")
    if tr:
        L.append(f"**Timeline reliability:** {int(100 * tr['score'])}% – " + "; ".join(tr["reasons"]) + ".")
    ca, co = S["created_all"], S["created_own"]
    ma, mo = S["modified_all"], S["modified_own"]
    L.append("")
    L.append("| Metric | Whole project | Own files* |")
    L.append("|---|---|---|")
    L.append(f"| Files | {S['files_total']} | {S['files_own']} |")
    L.append(f"| Created during the Compo | {ca['during']} ({pct(ca['during'], S['files_total'])}) | "
             f"{co['during']} ({pct(co['during'], S['files_own'])}) |")
    L.append(f"| Created in Submission Hour | {ca['submission_hour']} | {co['submission_hour']} |")
    L.append(f"| Created before the event | {ca['before']} | {co['before']} |")
    if ca["after"] or ca["unknown"]:
        L.append(f"| Created after / unknown | {ca['after']} / {ca['unknown']} | {co['after']} / {co['unknown']} |")
    edited_ok_a = ma["during"] + ma["before"]
    edited_ok_o = mo["during"] + mo["before"]
    L.append(f"| Last edited before Compo end | {edited_ok_a} ({pct(edited_ok_a, S['files_total'])}) | "
             f"{edited_ok_o} ({pct(edited_ok_o, S['files_own'])}) |")
    L.append(f"| Last edited in Submission Hour | {ma['submission_hour']} | {mo['submission_hour']} |")
    if ma["after"]:
        L.append(f"| Last edited after the deadline | {ma['after']} | {mo['after']} |")
    L.append("")
    L.append("*Own = excluding third-party, generated and build-output files.")
    L.append("")
    w = S["loc_written_during"]
    L.append(f"**LoC written during the event** (own code files created in the window): "
             f"**{human_int(w['code'])}** code lines in {w['files']} files "
             f"({human_int(w['lines'])} incl. {human_int(w['comment'])} comment / {human_int(w['blank'])} blank)")
    if S.get("lines_added_during_git") is not None:
        L.append(f"Lines added during the window per git (all own code files): {human_int(S['lines_added_during_git'])}")
    L.append(f"Own code total: {human_int(S['loc_own_code'])} code lines · base code from before the event: "
             f"{S['base_code']['files']} files / {human_int(S['base_code']['code'])} lines")
    if S["loc_by_language"]:
        L.append("Languages: " + ", ".join(f"{k} {human_int(v)}" for k, v in S["loc_by_language"].items()))
    L.append("")
    fmt_ext = lambda d: ", ".join(f"{k} {v}" for k, v in d.items()) or "–"  # noqa: E731
    L.append(f"**Artwork:** {S['artwork']['count']} ({fmt_ext(S['artwork']['by_ext'])})")
    raw = S["raw_files"]
    L.append(f"**Raw/project files:** {raw['count']} (art: {fmt_ext(raw['art'])}; music/sfx: {fmt_ext(raw['audio_music'])})")
    L.append(f"**Audio:** {S['audio']['count']} ({fmt_ext(S['audio']['by_ext'])}) · Fonts: {S['fonts']} · "
             f"Video: {S['video']} · Docs: {S['docs']} · Level files: {S['levels']}")
    if out.get("engines"):
        L.append(f"**Engine:** {', '.join(out['engines'])}")
    L.append(f"Third-party: {S['third_party_files']} · generated: {S['generated_files']} · build output: {S['build_dir_files']}")
    L.append("")
    L.append("**Files by type (own / all):** " + ", ".join(
        f"{k} {S['by_ext_own'].get(k, 0)}/{v}" for k, v in list(S["by_ext_all"].items())[:40]))
    bm = out.get("build_match")
    if bm:
        L.append("")
        L.append(f"**Played build vs git:** {bm.get('status')}" +
                 (f" – {bm.get('best_hits')} identical files, path similarity {bm.get('best_path_similarity')}"
                  if "best_hits" in bm else ""))
    if out["flags"]:
        L.append("")
        L.append("### Rule flags")
        order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
        for sev, code, msg in sorted(out["flags"], key=lambda f: order.get(f[0], 3)):
            L.append(f"- **{sev}** `{code}` – {msg}")
    if out.get("credits"):
        L.append("")
        L.append("### Credits / third-party mentions to review")
        for h in out["credits"][:10]:
            L.append(f"- {h['where']}: “…{h['context'][:220]}…”")
    notes = out["source"].get("notes", [])
    if notes:
        L.append("")
        L.append("### Notes")
        for n in notes:
            L.append(f"- {n}")
    return "\n".join(L) + "\n"


# =============================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--source", help="override: folder, git repo or archive to analyse")
    ap.add_argument("--start", help="override event start (ISO, UTC)")
    ap.add_argument("--compo-end")
    ap.add_argument("--submission-end")
    ap.add_argument("--cutoff", help="force a specific commit as the compo version")
    ap.add_argument("--subpath", help="analyse only this folder of the source")
    ap.add_argument("--ref", help="branch/tag to analyse (default: the linked ref or HEAD)")
    ap.add_argument("--lfs", action="store_true")
    args = ap.parse_args()

    wd = Path(args.workdir).resolve()
    game = load_json(wd / "game.json") or {}
    s1 = load_json(wd / "stage1.json") or {}
    try:
        win = Window.from_game(game, start=args.start, compo_end=args.compo_end, submission_end=args.submission_end)
    except ValueError:
        raise SystemExit("Event start unknown – pass --start 2026-10-16T22:00:00Z (UTC).")
    src = dict(s1.get("source") or {})
    if args.source:
        p = Path(args.source).resolve()
        src = {"method": "git" if (p / ".git").exists() else ("archive" if p.is_file() else "folder"),
               "repo_path": str(p) if (p / ".git").exists() else None, "path": str(p),
               "archive_path": str(p) if p.is_file() else None}
        if src["method"] == "archive":
            from archives import extract
            tgt = wd / "source" / "override"
            ok, msg = extract(p, tgt)
            if not ok:
                raise SystemExit(msg)
            src["path"] = str(tgt)
    if not src:
        raise SystemExit("No source in stage1.json – run get_source.py first (or pass --source).")

    out = {"window": win.to_dict(), "flags": []}
    times_for = lambda rel: None  # noqa: E731
    if src.get("method") == "git" and src.get("repo_path"):
        ref = args.ref or src.get("linked_ref") or "HEAD"
        g = analyze_git(src["repo_path"], ref, args.subpath or src.get("subpath"), win, wd, args, src)
        g["display"] = src.get("display")
        out["source"] = g
        root = Path(g["snapshot"])
        prefix = (args.subpath or src.get("subpath") or "").strip("/")
        ft = g.pop("file_times")
        tl = {k: v for k, v in ft.items()}
        times_for = lambda rel: tl.get(f"{prefix}/{rel}" if prefix else rel)  # noqa: E731
        if prefix:
            root = root / prefix
        out["flags"] += g.pop("flags")
    else:
        root = Path(src["path"])
        if src.get("method") == "archive" and src.get("archive_path"):
            a, tm = archive_times(src["archive_path"], root, win)
            out["source"] = a
            out["flags"] += a.pop("flags")
            # archive names may carry a top folder that extraction preserved
            keys = list(tm)

            def times_for(rel, tm=tm, keys=keys):  # noqa: E306
                if rel in tm:
                    return tm[rel]
                for k in keys:
                    if k.endswith("/" + rel):
                        return tm[k]
                return None
        else:
            out["source"] = {"mode": "folder", "path": str(root), "notes": [
                "Filesystem times are usually reset when files are copied or extracted."]}
            ft = fs_times(root)
            times_for = lambda rel: ft.get(rel)  # noqa: E731
            stamps = [v["modified"] for v in ft.values()]
            if stamps:
                common_n = collections.Counter(int(t.timestamp()) // 60 for t in stamps).most_common(1)[0][1]
                if common_n >= 0.8 * len(stamps):
                    out["flags"].append(("MEDIUM", "UNRELIABLE_TIMES",
                                         "Most files share the same filesystem time: creation/edit times are unknown."))
                    times_for = lambda rel: None  # noqa: E731

    # multi-project repositories: pick the project worked on during the event
    all_files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    roots = project_roots(all_files)
    if len(roots) > 1 and not args.subpath:
        def active(r):
            return sum(1 for f in all_files if (f.startswith(r + "/") or not r) and
                       (win.phase((times_for(f) or {}).get("created")) in ("during", "submission_hour")))
        scored = sorted(((active(r), r) for r in roots if r), reverse=True)
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > 2 * scored[1][0]):
            pick = scored[0][1]
            out["analysis_root_note"] = (f"**Note:** {len(roots)} projects found in the source "
                                         f"({', '.join(r or '/' for r in roots[:6])}); analysing `{pick}/`, "
                                         f"the one worked on during the event (override with --subpath).")
            old_tf = times_for
            root = root / pick
            times_for = lambda rel, old_tf=old_tf, pick=pick: old_tf(f"{pick}/{rel}")  # noqa: E731
        else:
            out["analysis_root_note"] = (f"**Note:** {len(roots)} projects found in the source "
                                         f"({', '.join(r or '/' for r in roots[:6])}) – consider --subpath.")
    out["analysis_root"] = str(root)

    log("Building inventory…")
    records = build_inventory(root, times_for, win, args)
    out["engines"] = detect_engine(root, [r["path"] for r in records])
    out["summary"] = summarise(records, win, out["source"])
    out["credits"] = credit_scan(root, records, game)
    out["flags"] = rule_flags(records, out["summary"], win, game, out["flags"])
    if out["credits"]:
        out["flags"].append(("MEDIUM", "CREDITS_MENTION",
                             f"{len(out['credits'])} credit/licence mention(s) in docs or the description – "
                             f"check whether any content was made by someone else or pre-made."))
    post = (out["source"].get("post_cutoff") or [])
    feats = [p for p in post if p["kind"] == "content/feature"]
    if feats:
        out["flags"].append(("MEDIUM", "POST_DEADLINE_CONTENT",
                             f"{len(feats)} commit(s) after the deadline add content/features (e.g. "
                             f"{feats[0]['sha']}: “{feats[0]['message'][:80]}”). Fine for a post-compo version, but the "
                             f"rated build must be the compo version (only bug fixes, with a change log)."))
    pb = (s1.get("played_build") or {})
    if pb.get("status") == "ok" and out["source"].get("mode") == "git":
        try:
            snap = Path(out["source"]["snapshot"])
            rel_root = root.relative_to(snap).as_posix() if root != snap else ""
            here = project_roots([r["path"] for r in records])
            proj = "/".join(x for x in (rel_root.strip("./"), here[0] if here else "") if x)
            bm = match_build(src["repo_path"], out["source"]["ref"], Path(pb["path"]),
                             proj, win, out["source"]["cutoff"]["sha"])
            out["build_match"] = bm
            if bm.get("status") == "matches only post-deadline commits":
                earliest = bm["best_commits"][-1]["sha"] if bm.get("best_commits") else None
                included = git(src["repo_path"], "rev-list", f"{out['source']['cutoff']['sha']}..{earliest}").stdout.split() \
                    if earliest else []
                kinds = {p["sha"]: p["kind"] for p in post}
                inc_kinds = [kinds.get(s[:10], "unclear") for s in included]
                bm["post_deadline_commits_in_build"] = dict(collections.Counter(inc_kinds))
                if any(k in ("content/feature", "unclear") for k in inc_kinds):
                    out["flags"].append(("HIGH", "PLAYED_BUILD_POST_DEADLINE",
                                         f"The build players get matches a post-deadline version that includes "
                                         f"{len(included)} later commit(s) ({', '.join(f'{v} {k}' for k, v in bm['post_deadline_commits_in_build'].items())}). "
                                         f"Only bug fixes (with a change log) are allowed after the deadline."))
                else:
                    out["flags"].append(("MEDIUM", "PLAYED_BUILD_HAS_FIXES",
                                         f"The build players get includes {len(included)} post-deadline commit(s) that "
                                         f"look like bug fixes/ports – allowed if listed in a change log."))
        except Exception as exc:  # noqa: BLE001
            out["build_match"] = {"status": f"error: {exc}"}
    out["timeline_reliability"] = timeline_reliability(out)
    out["files"] = records
    save_json(wd / "stage2.json", out)
    md = render_md(out, game, win)
    (wd / "stage2.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"Wrote {wd / 'stage2.json'} and {wd / 'stage2.md'}")


if __name__ == "__main__":
    main()
