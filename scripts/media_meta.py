#!/usr/bin/env python3
"""Metadata readers for art/audio/font/document files.

Every reader returns a dict that may contain:
  times:     [(label, datetime UTC)]   timestamps stored *inside* the file
  meta_text: str                       all metadata text (for keyword searches)
  tools:     [str]                     software that wrote the file, if stated
  plus format-specific fields (dimensions, layers, frames, duration, ...)
Readers never raise; on failure they return {} or {"error": ...}.
"""
from __future__ import annotations

import datetime as dt
import re
import struct
import zipfile
import zlib
from pathlib import Path

UTC = dt.timezone.utc
EPOCH_1904 = dt.datetime(1904, 1, 1, tzinfo=UTC)

# ----------------------------------------------------------------- AI / tool markers
AI_GEN_RE = re.compile(
    r"(stable[ _-]?diffusion|\bsdxl\b|\bsd ?1\.5\b|novelai|midjourney|\bniji\b|dall[-·· ]?e|\bdalle\b|"
    r"openai|chatgpt|gpt-?image|gpt-?4o|\bsora\b|firefly|leonardo\.?ai|\bleonardo\b|invokeai|"
    r"comfyui|automatic1111|a1111|\bforge\b.*\bdiffusion|fooocus|draw ?things|diffusionbee|"
    r"imagine with meta|meta ai|bing image creator|image creator from microsoft|microsoft designer|"
    r"ideogram|recraft|krea\.ai|\bflux(\.1|-dev|-schnell|-pro)\b|black forest labs|pixellab|"
    r"retro ?diffusion|scenario\.(gg|com)|layer\.ai|nightcafe|dreamstudio|playground ?ai|"
    r"tensor\.art|civitai|seaart|craiyon|artbreeder|runwayml|\bimagen\b|gemini|made with google ai|"
    r"edited with google ai|synthid|\bkling\b|hailuo|luma ai|\bgrok\b|aurora|seedream|qwen-image|"
    r"hunyuan|midjourney|trainedalgorithmicmedia|compositewithtrainedalgorithmicmedia|"
    r"algorithmicmedia|ai[ -]generated|generated (by|with) ai|created with ai|"
    r"steps: ?\d+, ?sampler|cfg scale|negative prompt|\"sampler_name\"|\"ksampler\"|"
    r"\bsuno\b|\budio\b|\baiva\b|mubert|soundraw|beatoven|\bboomy\b|soundful|loudly|"
    r"stable ?audio|musicgen|audiocraft|riffusion|elevenlabs|eleven ?labs|\bmurf\b|play\.ht|"
    r"wellsaid|resemble\.ai|uberduck|fakeyou|mureka|sonauto|topmediai)", re.I)
C2PA_RE = re.compile(r"(c2pa|contentcredentials|content credentials|jumbf)", re.I)
TOOL_RE = re.compile(
    r"(aseprite|libresprite|photoshop|gimp|krita|paint\.net|clip ?studio|procreate|affinity|"
    r"inkscape|illustrator|pyxel ?edit|pixelorama|graphicsgale|piskel|blender|magicavoxel|"
    r"medibang|firealpaca|paint ?tool ?sai|mspaint|pinta|tiled|ldtk|texturepacker|imagemagick|"
    r"pngcrush|optipng|pngquant|oxipng|godot|unity|bosca|ceoil|beepbox|jummbox|ultrabox|lmms|"
    r"fl studio|fruity|ableton|reaper|audacity|sfxr|bfxr|jsfxr|chiptone|rfxgen|furnace|"
    r"famitracker|milkytracker|openmpt|sunvox|garageband|logic pro|cubase|studio one|bitwig|"
    r"musescore|pxtone|renoise|deflemask|1bitdragon|caustic|goldwave|ocenaudio|sound forge|"
    r"audition|lavf|lavc|libvorbis|libsndfile|xiph|lame|ffmpeg)", re.I)


def _dec(b: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def find_xmp(data: bytes):
    i = data.find(b"<x:xmpmeta")
    if i < 0:
        i = data.find(b"<rdf:RDF")
    if i < 0:
        return None
    j = data.find(b"</x:xmpmeta>", i)
    if j < 0:
        j = data.find(b"</rdf:RDF>", i)
    return _dec(data[i:j + 12 if j > 0 else i + 65536])


def xmp_fields(xmp: str):
    out = {"times": [], "tools": []}
    if not xmp:
        return out
    for key in ("xmp:CreateDate", "xmp:ModifyDate", "xmp:MetadataDate", "photoshop:DateCreated",
                "exif:DateTimeOriginal"):
        for m in re.finditer(rf'{key}(?:="([^"]+)"|>([^<]+)<)', xmp):
            t = _ptime(m.group(1) or m.group(2))
            if t:
                out["times"].append((f"XMP {key}", t))
    for m in re.finditer(r'stEvt:when(?:="([^"]+)"|>([^<]+)<)', xmp):
        t = _ptime(m.group(1) or m.group(2))
        if t:
            out["times"].append(("XMP history", t))
    for m in re.finditer(r'(?:xmp:CreatorTool|stEvt:softwareAgent)(?:="([^"]+)"|>([^<]+)<)', xmp):
        out["tools"].append(m.group(1) or m.group(2))
    m = re.search(r"DigitalSourceType(?:=\"|>)([^\"<]+)", xmp)
    if m:
        out["digital_source_type"] = m.group(1)
    return out


def _ptime(s):
    from common import parse_time
    return parse_time(s)


# ----------------------------------------------------------------- images
def png_info(path):
    out = {"format": "png", "text": {}, "times": [], "tools": [], "c2pa": False}
    try:
        with open(path, "rb") as fh:
            if fh.read(8) != b"\x89PNG\r\n\x1a\n":
                return {}
            while True:
                hdr = fh.read(8)
                if len(hdr) < 8:
                    break
                length, ctype = struct.unpack(">I4s", hdr)
                ctype = ctype.decode("latin-1")
                if ctype in ("tEXt", "zTXt", "iTXt", "tIME", "IHDR", "eXIf", "acTL") and length < 20_000_000:
                    data = fh.read(length)
                    fh.seek(4, 1)
                else:
                    fh.seek(length + 4, 1)
                    data = None
                if ctype == "IHDR" and data:
                    out["width"], out["height"], out["bit_depth"], out["color_type"] = struct.unpack(">IIBB", data[:10])
                elif ctype == "tEXt" and data:
                    k, _, v = data.partition(b"\0")
                    out["text"][_dec(k)] = _dec(v)
                elif ctype == "zTXt" and data:
                    k, _, rest = data.partition(b"\0")
                    try:
                        out["text"][_dec(k)] = _dec(zlib.decompress(rest[1:]))
                    except zlib.error:
                        pass
                elif ctype == "iTXt" and data:
                    k, _, rest = data.partition(b"\0")
                    comp = rest[0]
                    rest = rest[2:]
                    _lang, _, rest = rest.partition(b"\0")
                    _tk, _, text = rest.partition(b"\0")
                    try:
                        out["text"][_dec(k)] = _dec(zlib.decompress(text) if comp else text)
                    except zlib.error:
                        pass
                elif ctype == "tIME" and data and len(data) >= 7:
                    y, mo, d, h, mi, s = struct.unpack(">HBBBBB", data[:7])
                    try:
                        out["times"].append(("PNG tIME (last modified)", dt.datetime(y, mo, d, h, mi, min(s, 59), tzinfo=UTC)))
                    except ValueError:
                        pass
                elif ctype == "eXIf":
                    out["exif"] = True
                elif ctype == "acTL":
                    out["animated"] = True
                elif ctype in ("caBX", "c2pa"):
                    out["c2pa"] = True
                if ctype == "IEND":
                    break
    except (OSError, struct.error, IndexError):
        return out
    xmp = out["text"].get("XML:com.adobe.xmp")
    if xmp:
        f = xmp_fields(xmp)
        out["times"] += f["times"]
        out["tools"] += f["tools"]
        if f.get("digital_source_type"):
            out["digital_source_type"] = f["digital_source_type"]
    for k in ("date:create", "date:modify", "date:timestamp", "Creation Time", "creation_time"):
        if k in out["text"]:
            t = _ptime(out["text"][k])
            if t:
                out["times"].append((f"PNG text '{k}'", t))
    if "Software" in out["text"]:
        out["tools"].append(out["text"]["Software"])
    out["meta_text"] = " | ".join(f"{k}={v[:2000]}" for k, v in out["text"].items())
    out["generation_params"] = any(k.lower() in ("parameters", "prompt", "workflow", "sd-metadata",
                                                 "dream", "invokeai_metadata", "generation_data")
                                   for k in out["text"])
    return out


def jpeg_info(path):
    out = {"format": "jpeg", "times": [], "tools": [], "c2pa": False, "meta_text": ""}
    texts = []
    try:
        with open(path, "rb") as fh:
            if fh.read(2) != b"\xff\xd8":
                return {}
            while True:
                b = fh.read(2)
                if len(b) < 2 or b[0] != 0xFF:
                    break
                marker = b[1]
                if marker in (0xD9, 0xDA):
                    break
                (seglen,) = struct.unpack(">H", fh.read(2))
                data = fh.read(seglen - 2)
                if marker == 0xE1 and data.startswith(b"http://ns.adobe.com/xap/1.0/"):
                    f = xmp_fields(_dec(data))
                    out["times"] += f["times"]
                    out["tools"] += f["tools"]
                    texts.append(_dec(data)[:6000])
                    if f.get("digital_source_type"):
                        out["digital_source_type"] = f["digital_source_type"]
                elif marker == 0xEB:
                    out["c2pa"] = out["c2pa"] or b"c2pa" in data or b"jumb" in data
                elif marker == 0xFE:
                    texts.append(_dec(data))
                elif marker == 0xE1 and data.startswith(b"Exif"):
                    texts.append(re.sub(r"[^\x20-\x7e]+", " ", _dec(data))[:3000])
    except (OSError, struct.error):
        pass
    try:
        from PIL import Image
        with Image.open(path) as im:
            ex = im.getexif()
            if ex:
                if ex.get(0x0131):
                    out["tools"].append(str(ex.get(0x0131)))
                for tag, label in ((0x0132, "EXIF DateTime"),):
                    t = _ptime(ex.get(tag))
                    if t:
                        out["times"].append((label, t))
                sub = ex.get_ifd(0x8769)
                t = _ptime(sub.get(0x9003)) if sub else None
                if t:
                    out["times"].append(("EXIF DateTimeOriginal", t))
    except Exception:  # noqa: BLE001
        pass
    out["meta_text"] = " | ".join(texts)
    return out


def riff_chunks(path, max_chunk=8_000_000):
    """Yield (id, data or None, size) for RIFF files (WAV/WebP)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
            if head[:4] != b"RIFF":
                return
            while True:
                h = fh.read(8)
                if len(h) < 8:
                    return
                cid, size = h[:4].decode("latin-1"), struct.unpack("<I", h[4:])[0]
                if cid in ("data", "VP8 ", "VP8L") or size > max_chunk:
                    fh.seek(size + (size & 1), 1)
                    yield cid, None, size
                else:
                    data = fh.read(size)
                    if size & 1:
                        fh.read(1)
                    yield cid, data, size
    except (OSError, struct.error):
        return


def webp_info(path):
    out = {"format": "webp", "times": [], "tools": [], "c2pa": False, "meta_text": ""}
    for cid, data, _ in riff_chunks(path):
        if cid == "XMP " and data:
            f = xmp_fields(_dec(data))
            out["times"] += f["times"]
            out["tools"] += f["tools"]
            out["meta_text"] += _dec(data)[:6000]
        elif cid == "EXIF" and data:
            out["meta_text"] += re.sub(r"[^\x20-\x7e]+", " ", _dec(data))[:3000]
        elif cid.upper() == "C2PA":
            out["c2pa"] = True
    return out


def generic_image_info(path):
    """GIF/BMP/TGA etc: look for embedded XMP / comments in the first 1 MB."""
    out = {"times": [], "tools": [], "c2pa": False, "meta_text": ""}
    try:
        data = Path(path).read_bytes()[:1_000_000]
    except OSError:
        return out
    xmp = find_xmp(data)
    if xmp:
        f = xmp_fields(xmp)
        out["times"] += f["times"]
        out["tools"] += f["tools"]
        out["meta_text"] += xmp[:6000]
    if data[:3] == b"GIF":
        for m in re.finditer(rb"\x21\xfe([\x01-\xff])", data):
            n = m.group(1)[0]
            out["meta_text"] += " " + _dec(data[m.end():m.end() + n])
    out["c2pa"] = b"c2pa" in data and b"jumb" in data
    return out


def image_meta(path):
    ext = Path(path).suffix.lower()
    if ext == ".png":
        return png_info(path)
    if ext in (".jpg", ".jpeg"):
        return jpeg_info(path)
    if ext == ".webp":
        return webp_info(path)
    return generic_image_info(path)


# ----------------------------------------------------------------- raw art files
def aseprite_info(path):
    try:
        with open(path, "rb") as fh:
            h = fh.read(128)
            if len(h) < 128 or struct.unpack_from("<H", h, 4)[0] != 0xA5E0:
                return {"error": "not an aseprite file"}
            frames, w, hgt, depth = struct.unpack_from("<HHHH", h, 6)
            ncolors = struct.unpack_from("<H", h, 32)[0]
            out = {"format": "aseprite", "frames": frames, "width": w, "height": hgt,
                   "color_depth": depth, "palette_colors": ncolors or 256, "layers": 0, "cels": 0,
                   "tags": 0, "tilesets": 0, "times": [], "tools": ["Aseprite"]}
            for fi in range(frames):
                fh_ = fh.read(16)
                if len(fh_) < 16:
                    break
                fbytes, magic, old_chunks, _dur = struct.unpack_from("<IHHH", fh_, 0)
                new_chunks = struct.unpack_from("<I", fh_, 12)[0]
                nchunks = new_chunks or old_chunks
                end = fh.tell() - 16 + fbytes
                for _ in range(nchunks):
                    ch = fh.read(6)
                    if len(ch) < 6:
                        break
                    csize, ctype = struct.unpack("<IH", ch)
                    if ctype == 0x2004 and fi == 0:
                        out["layers"] += 1
                    elif ctype == 0x2005:
                        out["cels"] += 1
                    elif ctype == 0x2018:
                        out["tags"] += 1
                    elif ctype == 0x2023:
                        out["tilesets"] += 1
                    fh.seek(max(csize - 6, 0), 1)
                fh.seek(end)
            return out
    except (OSError, struct.error) as exc:
        return {"error": str(exc)}


def kra_info(path):
    out = {"format": "krita", "times": [], "tools": ["Krita"]}
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "documentinfo.xml" in names:
                x = _dec(z.read("documentinfo.xml"))
                for tag, label in (("creation-date", "Krita creation-date"), ("date", "Krita last saved")):
                    m = re.search(rf"<{tag}>([^<]+)</{tag}>", x)
                    t = _ptime(m.group(1)) if m else None
                    if t:
                        out["times"].append((label + " (local time)", t))
                m = re.search(r"<editing-time>(\d+)</editing-time>", x)
                out["editing_seconds"] = int(m.group(1)) if m else None
                m = re.search(r"<editing-cycles>(\d+)</editing-cycles>", x)
                out["editing_cycles"] = int(m.group(1)) if m else None
            if "maindoc.xml" in names:
                out["layers"] = len(re.findall(r"<layer ", _dec(z.read("maindoc.xml"))))
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        out["error"] = str(exc)
    return out


def procreate_info(path):
    out = {"format": "procreate", "times": [], "tools": ["Procreate"]}
    try:
        with zipfile.ZipFile(path) as z:
            segs = [n for n in z.namelist() if n.startswith("video/segments/")]
            out["timelapse_segments"] = len(segs)
            out["layers"] = len({n.split("/")[0] for n in z.namelist() if re.match(r"[0-9A-F-]{36}/", n)})
    except (zipfile.BadZipFile, OSError) as exc:
        out["error"] = str(exc)
    return out


def ora_info(path):
    out = {"format": "openraster", "times": [], "tools": []}
    try:
        with zipfile.ZipFile(path) as z:
            out["layers"] = len(re.findall(r"<layer ", _dec(z.read("stack.xml"))))
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        out["error"] = str(exc)
    return out


def psd_info(path):
    out = {"format": "psd", "times": [], "tools": ["Photoshop-compatible"]}
    try:
        with open(path, "rb") as fh:
            h = fh.read(26)
            if h[:4] != b"8BPS":
                return {"error": "not a PSD"}
            ver = struct.unpack_from(">H", h, 4)[0]
            out["channels"], out["height"], out["width"], out["depth"], out["mode"] = struct.unpack_from(">HIIHH", h, 12)
            (cm_len,) = struct.unpack(">I", fh.read(4))
            fh.seek(cm_len, 1)
            (ir_len,) = struct.unpack(">I", fh.read(4))
            res = fh.read(ir_len)
            xmp = find_xmp(res)
            if xmp:
                f = xmp_fields(xmp)
                out["times"] += f["times"]
                out["tools"] += f["tools"]
                out["meta_text"] = xmp[:8000]
            lm_len = struct.unpack(">Q" if ver == 2 else ">I", fh.read(8 if ver == 2 else 4))[0]
            if lm_len:
                li_len = struct.unpack(">Q" if ver == 2 else ">I", fh.read(8 if ver == 2 else 4))[0]
                if li_len:
                    (count,) = struct.unpack(">h", fh.read(2))
                    out["layers"] = abs(count)
    except (OSError, struct.error) as exc:
        out["error"] = str(exc)
    return out


def xcf_info(path):
    out = {"format": "xcf", "times": [], "tools": ["GIMP"]}
    try:
        with open(path, "rb") as fh:
            magic = fh.read(14)
            if not magic.startswith(b"gimp xcf "):
                return {"error": "not an XCF"}
            v = magic[9:13]
            version = 0 if v == b"file" else int(v[1:].decode())
            fh.read(0 if magic[13:14] == b"\0" else 1)
            out["width"], out["height"], _base = struct.unpack(">III", fh.read(12))
            if version >= 4:
                fh.read(4)
            while True:  # properties
                ptype, psize = struct.unpack(">II", fh.read(8))
                if ptype == 0:
                    break
                fh.seek(psize, 1)
            ptr_fmt, ptr_len = (">Q", 8) if version >= 11 else (">I", 4)
            layers = 0
            while True:
                (ptr,) = struct.unpack(ptr_fmt, fh.read(ptr_len))
                if ptr == 0:
                    break
                layers += 1
            out["layers"], out["version"] = layers, version
    except (OSError, struct.error, ValueError) as exc:
        out["error"] = str(exc)
    return out


def blend_info(path):
    out = {"format": "blend", "times": [], "tools": ["Blender"]}
    try:
        with open(path, "rb") as fh:
            h = fh.read(17)
        if h[:7] == b"BLENDER":
            m = re.match(rb"BLENDER[-_]?(?:\d\d-\d\d)?[vV](\d+)", h)
            out["blender_version"] = m.group(1).decode() if m else _dec(h[9:12])
        elif h[:4] == b"\x28\xb5\x2f\xfd" or h[:2] == b"\x1f\x8b":
            out["compressed"] = True
        else:
            out["error"] = "unknown .blend header"
    except OSError as exc:
        out["error"] = str(exc)
    return out


def raw_art_meta(path):
    ext = Path(path).suffix.lower()
    return {".aseprite": aseprite_info, ".ase": aseprite_info, ".kra": kra_info, ".krz": kra_info,
            ".procreate": procreate_info, ".ora": ora_info, ".psd": psd_info, ".psb": psd_info,
            ".xcf": xcf_info, ".blend": blend_info}.get(ext, lambda p: {})(path)


# ----------------------------------------------------------------- audio
def wav_info(path):
    out = {"format": "wav", "times": [], "tools": [], "info": {}, "meta_text": ""}
    byte_rate = data_size = None
    for cid, data, size in riff_chunks(path):
        if cid == "fmt " and data and len(data) >= 16:
            fmt, ch, rate, brate, _ba, bits = struct.unpack_from("<HHIIHH", data, 0)
            out.update(channels=ch, sample_rate=rate, bits=bits, codec=fmt)
            byte_rate = brate
        elif cid == "data":
            data_size = size
        elif cid == "LIST" and data and data[:4] == b"INFO":
            i = 4
            while i + 8 <= len(data):
                sid, ssz = data[i:i + 4].decode("latin-1"), struct.unpack_from("<I", data, i + 4)[0]
                out["info"][sid] = _dec(data[i + 8:i + 8 + ssz]).strip("\0 ")
                i += 8 + ssz + (ssz & 1)
        elif cid == "bext" and data and len(data) >= 338:
            desc, orig = _dec(data[:256]).strip("\0 "), _dec(data[256:288]).strip("\0 ")
            date, time_ = _dec(data[320:330]).strip("\0 "), _dec(data[330:338]).strip("\0 ")
            out["bext"] = {"description": desc, "originator": orig, "date": date, "time": time_}
            t = _ptime(f"{date.replace(':', '-')}T{time_.replace('-', ':')}") if date else None
            if t:
                out["times"].append(("BWF origination (local time)", t))
        elif cid in ("id3 ", "ID3 ") and data:
            out["meta_text"] += " " + re.sub(r"[^\x20-\x7e]+", " ", _dec(data))[:3000]
    if byte_rate and data_size:
        out["duration"] = data_size / byte_rate
    if "ICRD" in out["info"]:
        t = _ptime(out["info"]["ICRD"])
        if t:
            out["times"].append(("WAV ICRD (creation date)", t))
    if "ISFT" in out["info"]:
        out["tools"].append(out["info"]["ISFT"])
    out["meta_text"] += " | ".join(f"{k}={v}" for k, v in out["info"].items())
    if out.get("bext"):
        out["meta_text"] += " | " + " ".join(out["bext"].values())
    return out


def _syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def mp3_info(path):
    out = {"format": "mp3", "times": [], "tools": [], "id3": {}, "meta_text": ""}
    try:
        with open(path, "rb") as fh:
            data = fh.read(2_000_000)
            fh.seek(0, 2)
            fsize = fh.tell()
    except OSError:
        return out
    pos = 0
    if data[:3] == b"ID3":
        ver = data[3]
        size = _syncsafe(data[6:10])
        i = 10
        end = 10 + size
        while i + 10 <= min(end, len(data)):
            fid = data[i:i + 4]
            if not re.match(rb"[A-Z0-9]{4}", fid):
                break
            fsz = _syncsafe(data[i + 4:i + 8]) if ver >= 4 else struct.unpack(">I", data[i + 4:i + 8])[0]
            body = data[i + 10:i + 10 + fsz]
            fid_s = fid.decode()
            if fid_s.startswith("T") or fid_s in ("COMM", "USLT") or fid_s.startswith("W"):
                enc = body[:1]
                txt = body[1:] if fid_s.startswith(("T", "C", "U")) else body
                if enc in (b"\x01", b"\x02"):
                    try:
                        txt_s = txt.decode("utf-16" if enc == b"\x01" else "utf-16-be", "replace")
                    except Exception:  # noqa: BLE001
                        txt_s = _dec(txt)
                else:
                    txt_s = _dec(txt)
                out["id3"][fid_s] = txt_s.replace("\x00", " ").strip()[:1000]
            i += 10 + fsz
        pos = end
    for k in ("TDRC", "TYER", "TDEN", "TDTG"):
        t = _ptime(out["id3"].get(k)) if out["id3"].get(k) and len(out["id3"].get(k)) > 4 else None
        if t:
            out["times"].append((f"ID3 {k}", t))
    for k in ("TSSE", "TENC"):
        if out["id3"].get(k):
            out["tools"].append(out["id3"][k])
    out["meta_text"] = " | ".join(f"{k}={v}" for k, v in out["id3"].items())
    # duration estimate from the first frame (+ Xing/Info header)
    m = re.search(rb"\xff[\xe0-\xff]", data[pos:pos + 200000])
    if m:
        p = pos + m.start()
        hdr = struct.unpack(">I", data[p:p + 4])[0]
        ver_bits, layer_bits = (hdr >> 19) & 3, (hdr >> 17) & 3
        br_idx, sr_idx = (hdr >> 12) & 15, (hdr >> 10) & 3
        rates = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
        br_tab = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0] if ver_bits == 3 else \
            [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
        if layer_bits == 1 and sr_idx < 3 and ver_bits in rates and 0 < br_idx < 15:
            sr = rates[ver_bits][sr_idx]
            out["sample_rate"] = sr
            out["bitrate_kbps"] = br_tab[br_idx]
            xing = data.find(b"Xing", p, p + 200)
            if xing < 0:
                xing = data.find(b"Info", p, p + 200)
            if xing > 0 and struct.unpack(">I", data[xing + 4:xing + 8])[0] & 1:
                frames = struct.unpack(">I", data[xing + 8:xing + 12])[0]
                out["duration"] = frames * (1152 if ver_bits == 3 else 576) / sr
            elif br_tab[br_idx]:
                out["duration"] = (fsize - p) * 8 / (br_tab[br_idx] * 1000)
    return out


def ogg_info(path):
    out = {"format": "ogg", "times": [], "tools": [], "comments": {}, "meta_text": ""}
    try:
        with open(path, "rb") as fh:
            head = fh.read(200_000)
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            tail = fh.read()
    except OSError:
        return out
    rate = None
    m = head.find(b"\x01vorbis")
    if m >= 0:
        rate = struct.unpack_from("<I", head, m + 12)[0]
        out["channels"] = head[m + 11]
    elif head.find(b"OpusHead") >= 0:
        rate = 48000
        out["codec"] = "opus"
    c = head.find(b"\x03vorbis")
    off = c + 7 if c >= 0 else (head.find(b"OpusTags") + 8 if head.find(b"OpusTags") >= 0 else -1)
    if off > 0:
        try:
            vlen = struct.unpack_from("<I", head, off)[0]
            vendor = _dec(head[off + 4:off + 4 + vlen])
            out["vendor"] = vendor
            out["tools"].append(vendor)
            p = off + 4 + vlen
            n = struct.unpack_from("<I", head, p)[0]
            p += 4
            for _ in range(min(n, 200)):
                ln = struct.unpack_from("<I", head, p)[0]
                kv = _dec(head[p + 4:p + 4 + ln])
                p += 4 + ln
                k, _, v = kv.partition("=")
                out["comments"][k.upper()] = v[:1000]
        except struct.error:
            pass
    if "DATE" in out["comments"]:
        t = _ptime(out["comments"]["DATE"])
        if t:
            out["times"].append(("Vorbis DATE", t))
    last = tail.rfind(b"OggS")
    if last >= 0 and rate:
        gran = struct.unpack_from("<q", tail, last + 6)[0]
        if gran > 0:
            out["duration"] = gran / rate
    out["sample_rate"] = rate
    out["meta_text"] = (out.get("vendor") or "") + " | " + " | ".join(f"{k}={v}" for k, v in out["comments"].items())
    return out


def flac_info(path):
    out = {"format": "flac", "times": [], "tools": [], "comments": {}, "meta_text": ""}
    try:
        with open(path, "rb") as fh:
            if fh.read(4) != b"fLaC":
                return out
            last = False
            while not last:
                h = fh.read(4)
                if len(h) < 4:
                    break
                last, btype, blen = bool(h[0] & 0x80), h[0] & 0x7F, int.from_bytes(h[1:], "big")
                body = fh.read(blen)
                if btype == 0 and len(body) >= 18:
                    sr = (body[10] << 12) | (body[11] << 4) | (body[12] >> 4)
                    total = ((body[13] & 0x0F) << 32) | int.from_bytes(body[14:18], "big")
                    out["sample_rate"] = sr
                    out["duration"] = total / sr if sr else None
                elif btype == 4:
                    vlen = struct.unpack_from("<I", body, 0)[0]
                    out["vendor"] = _dec(body[4:4 + vlen])
                    p = 4 + vlen
                    n = struct.unpack_from("<I", body, p)[0]
                    p += 4
                    for _ in range(min(n, 200)):
                        ln = struct.unpack_from("<I", body, p)[0]
                        k, _, v = _dec(body[p + 4:p + 4 + ln]).partition("=")
                        out["comments"][k.upper()] = v[:1000]
                        p += 4 + ln
    except (OSError, struct.error):
        pass
    out["meta_text"] = (out.get("vendor") or "") + " | " + " | ".join(f"{k}={v}" for k, v in out["comments"].items())
    return out


def audio_meta(path):
    ext = Path(path).suffix.lower()
    if ext == ".wav":
        return wav_info(path)
    if ext == ".mp3":
        return mp3_info(path)
    if ext in (".ogg", ".oga", ".opus"):
        return ogg_info(path)
    if ext == ".flac":
        return flac_info(path)
    return generic_image_info(path) | {"format": ext.lstrip(".")}


# ----------------------------------------------------------------- fonts / documents
def font_info(path):
    out = {"format": "font", "times": [], "tools": [], "names": {}}
    try:
        data = Path(path).read_bytes()
        if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
            return out
        num = struct.unpack_from(">H", data, 4)[0]
        tables = {}
        for i in range(num):
            tag, _cs, off, ln = struct.unpack_from(">4sIII", data, 12 + 16 * i)
            tables[tag.decode("latin-1")] = (off, ln)
        if "head" in tables:
            off = tables["head"][0]
            created, modified = struct.unpack_from(">qq", data, off + 20)
            for label, v in (("font created", created), ("font modified", modified)):
                try:
                    if 0 < v < 5_000_000_000:
                        out["times"].append((label, EPOCH_1904 + dt.timedelta(seconds=v)))
                except OverflowError:
                    pass
        if "name" in tables:
            off = tables["name"][0]
            _fmt, count, str_off = struct.unpack_from(">HHH", data, off)
            for i in range(count):
                pid, eid, _lid, nid, ln, so = struct.unpack_from(">HHHHHH", data, off + 6 + 12 * i)
                if nid in (0, 1, 8, 9, 13) and nid not in out["names"]:
                    raw = data[off + str_off + so: off + str_off + so + ln]
                    txt = raw.decode("utf-16-be", "replace") if pid in (0, 3) else _dec(raw)
                    out["names"][{0: "copyright", 1: "family", 8: "manufacturer", 9: "designer",
                                  13: "license"}[nid]] = txt[:300]
    except (OSError, struct.error):
        pass
    out["meta_text"] = " | ".join(f"{k}={v}" for k, v in out["names"].items())
    return out


def office_info(path):
    out = {"times": [], "tools": []}
    try:
        with zipfile.ZipFile(path) as z:
            core = _dec(z.read("docProps/core.xml"))
            for tag in ("dcterms:created", "dcterms:modified"):
                m = re.search(rf"<{tag}[^>]*>([^<]+)</{tag}>", core)
                t = _ptime(m.group(1)) if m else None
                if t:
                    out["times"].append((tag, t))
    except (zipfile.BadZipFile, OSError, KeyError):
        pass
    return out


def pdf_info(path):
    out = {"times": [], "tools": []}
    try:
        data = Path(path).read_bytes()[:400_000]
    except OSError:
        return out
    for key in ("CreationDate", "ModDate"):
        m = re.search(rb"/" + key.encode() + rb"\s*\(D:(\d{14})", data)
        if m:
            t = _ptime(dt.datetime.strptime(m.group(1).decode(), "%Y%m%d%H%M%S").replace(tzinfo=UTC))
            if t:
                out["times"].append((f"PDF {key}", t))
    for key in ("Producer", "Creator"):
        m = re.search(rb"/" + key.encode() + rb"\s*\(([^)]{1,200})\)", data)
        if m:
            out["tools"].append(_dec(m.group(1)))
    return out


def file_meta(path, category):
    """Dispatch on the classifier's category."""
    ext = Path(path).suffix.lower()
    try:
        if category == "art":
            return image_meta(path)
        if category == "art_raw":
            return raw_art_meta(path)
        if category == "audio":
            return audio_meta(path)
        if category == "font" and ext in (".ttf", ".otf", ".ttc"):
            return font_info(path)
        if category == "doc" and ext in (".docx", ".odt"):
            return office_info(path)
        if category == "doc" and ext == ".pdf":
            return pdf_info(path)
    except Exception as exc:  # noqa: BLE001 – never let one odd file stop the run
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {}
