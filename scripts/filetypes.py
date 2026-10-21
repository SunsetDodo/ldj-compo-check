#!/usr/bin/env python3
"""File classification, third-party/generated detection and line counting."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

# ---------------------------------------------------------------- language table
# ext -> (language, comment-style)
CODE = {
    ".gd": ("GDScript", "hash"), ".cs": ("C#", "c"), ".js": ("JavaScript", "c"),
    ".mjs": ("JavaScript", "c"), ".cjs": ("JavaScript", "c"), ".jsx": ("JavaScript", "c"),
    ".ts": ("TypeScript", "c"), ".tsx": ("TypeScript", "c"), ".lua": ("Lua", "lua"),
    ".p8": ("PICO-8", "lua"), ".py": ("Python", "python"), ".pyw": ("Python", "python"),
    ".c": ("C", "c"), ".h": ("C/C++", "c"), ".cpp": ("C++", "c"), ".cc": ("C++", "c"),
    ".cxx": ("C++", "c"), ".hpp": ("C++", "c"), ".hh": ("C++", "c"), ".hxx": ("C++", "c"),
    ".java": ("Java", "c"), ".kt": ("Kotlin", "c"), ".kts": ("Kotlin", "c"), ".rs": ("Rust", "c"),
    ".go": ("Go", "c"), ".hx": ("Haxe", "c"), ".as": ("ActionScript", "c"), ".gml": ("GML", "c"),
    ".rb": ("Ruby", "ruby"), ".swift": ("Swift", "c"), ".dart": ("Dart", "c"), ".zig": ("Zig", "c"),
    ".odin": ("Odin", "c"), ".jai": ("Jai", "c"), ".nim": ("Nim", "hash"), ".cr": ("Crystal", "hash"),
    ".ex": ("Elixir", "hash"), ".exs": ("Elixir", "hash"), ".clj": ("Clojure", "semi"),
    ".cljs": ("Clojure", "semi"), ".fnl": ("Fennel", "semi"), ".moon": ("MoonScript", "lua"),
    ".wren": ("Wren", "c"), ".janet": ("Janet", "hash"), ".scala": ("Scala", "c"), ".fsx": ("F#", "c"),
    ".vb": ("VB.NET", "basic"), ".php": ("PHP", "c"), ".sh": ("Shell", "hash"), ".bash": ("Shell", "hash"),
    ".ps1": ("PowerShell", "hash"), ".bas": ("BASIC", "basic"), ".asm": ("Assembly", "semi"),
    ".s": ("Assembly", "semi"), ".vue": ("Vue", "c"), ".svelte": ("Svelte", "c"),
    ".coffee": ("CoffeeScript", "hash"), ".elm": ("Elm", "hs"), ".hs": ("Haskell", "hs"),
    ".jl": ("Julia", "hash"), ".d": ("D", "c"), ".pas": ("Pascal", "c"), ".lisp": ("Lisp", "semi"),
    ".scm": ("Scheme", "semi"), ".rkt": ("Racket", "semi"), ".tcl": ("Tcl", "hash"),
    ".rpy": ("Ren'Py", "hash"), ".v": None,  # ambiguous, ignored
}
SHADER = {e: ("Shader", "c") for e in (
    ".gdshader", ".gdshaderinc", ".shader", ".hlsl", ".glsl", ".vert", ".frag", ".geom", ".comp",
    ".tesc", ".tese", ".fx", ".fxh", ".cginc", ".compute", ".wgsl", ".metal", ".usf", ".ush",
    ".fsh", ".vsh", ".gdshader")}
NARRATIVE = {".ink": ("Ink", "c"), ".twee": ("Twee", "c"), ".tw": ("Twee", "c"),
             ".yarn": ("Yarn", "c"), ".dialogue": ("Dialogue Manager", "hash")}
MARKUP = {".html": ("HTML", "html"), ".htm": ("HTML", "html"), ".css": ("CSS", "css"),
          ".scss": ("SCSS", "c"), ".sass": ("Sass", "c"), ".less": ("Less", "c")}

ART = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tga", ".webp", ".tif", ".tiff", ".dds", ".ktx",
       ".ktx2", ".exr", ".hdr", ".ico", ".icns", ".svg", ".pcx", ".qoi", ".avif", ".jxl", ".basis"}
ART_3D = {".fbx", ".obj", ".mtl", ".glb", ".gltf", ".dae", ".3ds", ".ply", ".stl", ".x3d", ".usd",
          ".usda", ".usdc", ".usdz", ".abc", ".iqm", ".md2", ".md3", ".b3d", ".ms3d", ".mesh", ".x"}
ART_RAW = {".aseprite", ".ase", ".psd", ".psb", ".kra", ".krz", ".xcf", ".clip", ".procreate",
           ".pdn", ".pyxel", ".sai", ".sai2", ".afphoto", ".afdesign", ".ai", ".cdr", ".sketch",
           ".fig", ".xd", ".blend", ".blend1", ".blend2", ".max", ".ma", ".mb", ".c4d", ".spp",
           ".sbs", ".sbsar", ".ztl", ".zpr", ".vox", ".gox", ".qb", ".piskel", ".pxo", ".gal",
           ".spine", ".scml", ".dbproj", ".mdp", ".ora", ".pixil", ".lbm", ".iff", ".pkm",
           ".tilesetter", ".pyxelrc"}
AUDIO = {".wav", ".ogg", ".oga", ".mp3", ".flac", ".aiff", ".aif", ".m4a", ".aac", ".opus",
         ".wma", ".weba", ".caf"}
MUSIC_RAW = {".ceol", ".bosca", ".flp", ".als", ".alp", ".logicx", ".band", ".cpr", ".npr", ".rpp",
             ".rpp-bak", ".mmp", ".mmpz", ".ptcop", ".pttune", ".sfs", ".bfxr", ".bfxrsound",
             ".jsfxr", ".ftm", ".0cc", ".dnm", ".dmf", ".fur", ".mptm", ".xrns", ".rns", ".sunvox",
             ".mscz", ".mscx", ".musicxml", ".mxl", ".sib", ".gp", ".gpx", ".gp5", ".aup", ".aup3",
             ".song", ".cwp", ".bwproject", ".reason", ".ptx", ".dawproject", ".mod", ".xm", ".it",
             ".s3m", ".mid", ".midi", ".beepbox", ".jummbox", ".chiptone", ".rfx", ".sfxr"}
TRACKER_PLAYABLE = {".mod", ".xm", ".it", ".s3m"}
FONT = {".ttf", ".otf", ".woff", ".woff2", ".fnt", ".bdf", ".pcf", ".fon", ".ttc", ".eot", ".bmfc"}
VIDEO = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".ogv", ".m4v", ".wmv"}
DOC = {".md", ".markdown", ".txt", ".rst", ".pdf", ".doc", ".docx", ".odt", ".rtf", ".adoc"}
ENGINE = {".tscn", ".tres", ".godot", ".scn", ".res", ".gdextension", ".unity", ".prefab", ".asset",
          ".mat", ".anim", ".controller", ".overridecontroller", ".physicmaterial", ".physicsmaterial2d",
          ".mixer", ".lighting", ".playable", ".signal", ".rendertexture", ".spriteatlas",
          ".spriteatlasv2", ".guiskin", ".fontsettings", ".terrainlayer", ".preset", ".asmdef",
          ".asmref", ".inputactions", ".shadergraph", ".shadersubgraph", ".vfx", ".yy", ".yyp", ".gmx",
          ".collection", ".go", ".gui", ".atlas", ".tilemap", ".tilesource", ".particlefx",
          ".collisionobject", ".sound", ".label", ".model", ".camera", ".factory",
          ".collectionfactory", ".collectionproxy", ".input_binding", ".render", ".display_profiles",
          ".texture_profiles", ".c3p", ".capx", ".c3proj", ".ron", ".uasset", ".umap", ".uproject",
          ".flax", ".sdpkg", ".gmez", ".yyz", ".sb3", ".tic", ".pyxres"}
LEVEL = {".tmx", ".tmj", ".tsj", ".world", ".ldtk", ".ldtkl", ".ogmo", ".level"}
DATA = {".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".conf", ".properties", ".plist",
        ".csv", ".tsv", ".env", ".sql", ".db", ".sqlite", ".lock"}
BUILD = {".exe", ".dll", ".so", ".dylib", ".pck", ".wasm", ".apk", ".aab", ".ipa", ".love", ".jar",
         ".class", ".pdb", ".o", ".a", ".lib", ".unityweb", ".data", ".br", ".mem", ".bin",
         ".app", ".x86_64", ".x86", ".msi", ".dmg", ".appimage", ".deb", ".rpm", ".nupkg", ".pyc"}
ARCHIVE = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".unitypackage"}

SPECIAL_NAMES = {
    "readme": "doc", "license": "doc", "licence": "doc", "changelog": "doc", "credits": "doc",
    "authors": "doc", "notice": "doc", "copying": "doc", "todo": "doc", "makefile": "config",
    "dockerfile": "config", "cmakelists.txt": "config", "project.godot": "engine",
    "game.project": "engine", "conf.lua": "code", "main.lua": "code", ".gitignore": "config",
    ".gitattributes": "config", ".gitmodules": "config", ".editorconfig": "config",
    "package.json": "config", "package-lock.json": "generated", "yarn.lock": "generated",
    "pnpm-lock.yaml": "generated", "cargo.lock": "generated", "go.sum": "generated",
    "export_presets.cfg": "config", "default_bus_layout.tres": "engine", "icon.svg": "art",
    "go.mod": "config", "cargo.toml": "config", "tsconfig.json": "config",
}

GENERATED_DIR_PARTS = {".godot", ".import", "__pycache__", ".gradle", ".vs", ".idea", ".mypy_cache",
                       ".pytest_cache", ".next", ".nuxt", ".cache", ".parcel-cache", ".svelte-kit",
                       ".tmp", ".sass-cache", "deriveddatacache", "node_modules/.cache"}
# Unity / Unreal generated folders are matched case-sensitively to avoid hitting own code.
GENERATED_DIR_EXACT = {"Library", "Temp", "Logs", "UserSettings", "Obj", "obj", "Intermediate",
                       "Saved", "Binaries", "DerivedDataCache"}
BUILD_DIR_PARTS = {"build", "builds", "dist", "out", "bin", "target", "export", "exports",
                   "release", "releases", "web-build", "webbuild", "html5", "public_build"}
# Folders that almost always hold someone else's code/assets.
VENDOR_DIR_STRONG = {"node_modules", "bower_components", "vendor", "vendors", "third_party",
                     "thirdparty", "third-party", "3rdparty", "addons", "standard assets",
                     "textmesh pro", "assetstoretools", "site-packages", "venv", ".venv", "pods"}
# Folders that often hold libraries but may hold own code: only flagged together with a
# license header / known library name (checked when the content is read).
VENDOR_DIR_WEAK = {"lib", "libs", "libraries", "external", "externals", "extern", "deps",
                   "plugins", "packages", "samples", "thirdparty-lite"}
ENGINE_DEFAULT_ASSETS = {"icon.svg", "icon.png", "default_env.tres", "icon.svg.import"}

KNOWN_LIBS = re.compile(
    r"(^|/)(phaser|three|pixi|p5|kaboom|kaplay|matter|howler|babylon|jquery|lodash|gsap|tween|"
    r"littlejs|kontra|excalibur|melonjs|planck|box2d|cannon|ammo|stats|dat\.gui|lil-gui|"
    r"socket\.io|zzfx|zzfxm|jsfxr|riffwave|seedrandom|simplex-noise|noisejs|easystar|"
    r"anim8|bump|lume|classic|middleclass|flux|tween|tiny|push|gamera|baton|inspect|json|rxi|"
    r"lovebird|lurker|knife|hump|sti|windfield|moonshine|stb_[a-z_]+|raylib|raymath|rlgl|"
    r"miniaudio|cute_[a-z_]+|sokol_[a-z_]+|imgui[a-z_]*|glad|khrplatform|tinyxml2|lodepng|"
    r"dr_[a-z]+|nlohmann|entt|sdl[0-9_a-z]*|glfw[0-9a-z]*|cJSON|minimp3|stb)"
    r"([.-][\w.-]*)?\.(js|lua|h|hpp|c|cpp|cs)$", re.I)
LICENSE_HEADER = re.compile(
    r"(Permission is hereby granted, free of charge|Licensed under the Apache License|"
    r"SPDX-License-Identifier|Mozilla Public License|GNU (Lesser )?General Public License|"
    r"This software is provided 'as-is'|zlib License|BSD \d-Clause|THE SOFTWARE IS PROVIDED \"AS IS\"|"
    r"@license|\(c\) \d{4}.{0,60}(MIT|BSD|Apache))", re.I)


def ext_of(path: str) -> str:
    name = PurePosixPath(path).name.lower()
    for double in (".tar.gz", ".tar.xz", ".tar.bz2", ".p8.png", ".min.js", ".min.css",
                   ".scn.ron", ".rpp-bak"):
        if name.endswith(double):
            return double
    return PurePosixPath(name).suffix


def classify(relpath: str, head: bytes | None = None) -> dict:
    """Classify a project-relative path. `head` = first bytes for sniffing."""
    p = relpath.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    orig_parts = p.split("/")
    parts = [x.lower() for x in orig_parts]
    name = parts[-1]
    ext = ext_of(p)
    info = {"path": p, "ext": ext or "(none)", "category": "other", "lang": None, "style": None,
            "third_party": False, "tp_reason": None, "generated": False, "gen_reason": None}

    dirs, odirs = parts[:-1], orig_parts[:-1]
    if (any(d in GENERATED_DIR_PARTS for d in dirs) or any(d in GENERATED_DIR_EXACT for d in odirs)
            or ext in (".import", ".uid", ".meta", ".map")):
        info["generated"] = True
        info["gen_reason"] = "engine/IDE generated"
    if ext in (".min.js", ".min.css") or name.endswith((".bundle.js", "-bundle.js", ".chunk.js")):
        info["generated"] = True
        info["gen_reason"] = "minified/bundled"
    if any(d in BUILD_DIR_PARTS for d in dirs):
        info["build_dir"] = True
    strong = [d for d in dirs if d in VENDOR_DIR_STRONG]
    unity_vendor = (len(dirs) >= 2 and dirs[0] == "assets" and dirs[1] in ("plugins", "samples")) or \
                   (len(dirs) >= 2 and dirs[0] == "packages" and name not in ("manifest.json", "packages-lock.json"))
    if strong or unity_vendor:
        info["third_party"] = True
        info["tp_reason"] = f"vendor folder '{(strong or dirs[:2])[0]}'"
    elif any(d in VENDOR_DIR_WEAK for d in dirs):
        info["possible_vendor_dir"] = next(d for d in dirs if d in VENDOR_DIR_WEAK)
    if KNOWN_LIBS.search("/" + p):
        info["third_party"] = True
        info["tp_reason"] = info["tp_reason"] or "known library file"

    stem = name.split(".")[0]
    if name in SPECIAL_NAMES:
        info["category"] = SPECIAL_NAMES[name]
    elif stem in ("readme", "license", "licence", "changelog", "credits", "authors", "copying") and ext in ("", ".md", ".txt", ".markdown", ".rst"):
        info["category"] = "doc"
    elif ext == ".tsx" and head and head.lstrip()[:5] in (b"<?xml", b"<tile"):
        info["category"] = "level"
    elif ext == ".obj" and head and not re.match(rb"^\s*(#|v |o |g |mtllib|vn |vt )", head[:200]):
        info["category"] = "build"
    elif ext == ".p8.png":
        info.update(category="code", lang="PICO-8 cart", style="lua")
    elif ext in CODE and CODE[ext]:
        info.update(category="code", lang=CODE[ext][0], style=CODE[ext][1])
    elif ext in SHADER:
        info.update(category="code", lang="Shader", style="c", subcategory="shader")
    elif ext in NARRATIVE:
        info.update(category="narrative", lang=NARRATIVE[ext][0], style=NARRATIVE[ext][1])
    elif ext in MARKUP:
        info.update(category="markup", lang=MARKUP[ext][0], style=MARKUP[ext][1])
    elif ext in ART_RAW:
        info["category"] = "art_raw"
    elif ext in ART:
        info["category"] = "art"
    elif ext in ART_3D:
        info["category"] = "art_3d"
    elif ext in MUSIC_RAW:
        info["category"] = "music_raw"
    elif ext in AUDIO:
        info["category"] = "audio"
    elif ext in FONT:
        info["category"] = "font"
    elif ext in VIDEO:
        info["category"] = "video"
    elif ext == ".txt" and not re.search(r"(readme|licen[cs]e|credit|changelog|notes?|todo|"
                                         r"instructions|how.?to|controls|about|devlog|postmortem)",
                                         name) and "docs" not in dirs and "doc" not in dirs:
        info["category"] = "data"
    elif ext in DOC:
        info["category"] = "doc"
    elif ext in ENGINE:
        info["category"] = "engine"
    elif ext in LEVEL:
        info["category"] = "level"
    elif ext in BUILD:
        info["category"] = "build"
    elif ext in ARCHIVE:
        info["category"] = "archive"
    elif ext in DATA:
        info["category"] = "data"
    elif ext in (".import", ".uid", ".meta", ".map"):
        info["category"] = "generated"
    if info["generated"] and info["category"] not in ("code", "markup"):
        info["category"] = "generated" if info["category"] in ("other", "data", "engine") else info["category"]
    if info["category"] in ("art", "audio") and name in ENGINE_DEFAULT_ASSETS:
        info["engine_default"] = True
    return info


def is_own(info) -> bool:
    return not info.get("third_party") and not info.get("generated") and not info.get("build_dir")


# ---------------------------------------------------------------- line counting
STYLES = {
    "c": (["//"], [("/*", "*/")]),
    "hash": (["#"], []),
    "python": (["#"], [('"""', '"""'), ("'''", "'''")]),
    "lua": (["--"], [("--[[", "]]"), ("--[=[", "]=]")]),
    "html": ([], [("<!--", "-->")]),
    "css": ([], [("/*", "*/")]),
    "semi": ([";"], []),
    "hs": (["--"], [("{-", "-}")]),
    "ruby": (["#"], [("=begin", "=end")]),
    "basic": (["'", "REM "], []),
    None: ([], []),
}


def _strip_strings(line):
    """Remove the contents of simple string literals so comment markers inside
    strings are ignored (approximate; good enough for statistics)."""
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`', '""', line)


def count_lines(text: str, style: str | None, keep_comments=True, pico8=False):
    """Return dict(lines, code, comment, blank, comments=[(lineno, text, full_line)],
    minified)."""
    if pico8:
        m = re.search(r"__lua__\s*\n(.*?)(\n__\w+__|\Z)", text, re.S)
        text = m.group(1) if m else ""
    lines = text.splitlines()
    line_toks, blocks = STYLES.get(style, ([], []))
    res = {"lines": len(lines), "code": 0, "comment": 0, "blank": 0, "comments": [],
           "minified": False}
    if lines:
        longest = max(len(l) for l in lines)
        avg = sum(len(l) for l in lines) / len(lines)
        res["minified"] = longest > 3000 or (avg > 250 and len(lines) > 3)
    in_block = None
    block_buf = []
    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        if in_block:
            res["comment"] += 1
            end = in_block[1]
            if end in s:
                before = s.split(end, 1)[0]
                block_buf.append(before)
                if keep_comments:
                    res["comments"].append((i, " ".join(x for x in block_buf if x).strip(), True))
                in_block, block_buf = None, []
            else:
                block_buf.append(s.lstrip("*").strip())
            continue
        if not s:
            res["blank"] += 1
            continue
        # full-line or trailing comment detection
        stripped = _strip_strings(s)
        started_block = None
        for b0, b1 in blocks:
            if stripped.startswith(b0):
                started_block = (b0, b1)
                break
        if started_block:
            rest = s[len(started_block[0]):]
            if started_block[1] in rest:
                res["comment"] += 1
                if keep_comments:
                    res["comments"].append((i, rest.split(started_block[1], 1)[0].strip(" *"), True))
            else:
                res["comment"] += 1
                in_block, block_buf = started_block, [rest.strip(" *")]
            continue
        tok = next((t for t in line_toks if stripped.startswith(t)), None)
        if tok:
            res["comment"] += 1
            if keep_comments:
                res["comments"].append((i, s[len(tok):].lstrip("/#-;! ").strip(), True))
            continue
        res["code"] += 1
        if keep_comments:
            for t in line_toks:
                idx = stripped.find(" " + t) if not stripped.startswith(t) else -1
                if idx > 0:
                    txt = stripped[idx + 1 + len(t):].strip("/#- ")
                    if txt:
                        res["comments"].append((i, txt, False))
                    break
            else:
                for b0, b1 in blocks:
                    idx = stripped.find(b0)
                    if idx > 0 and b1 in stripped[idx:]:
                        txt = stripped[idx + len(b0):].split(b1, 1)[0].strip(" *")
                        if txt:
                            res["comments"].append((i, txt, False))
                        break
                    if idx > 0 and b0 in ('"""', "'''"):
                        break
    return res


def looks_third_party_text(text_head: str) -> bool:
    return bool(LICENSE_HEADER.search(text_head or ""))
