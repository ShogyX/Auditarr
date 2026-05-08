"""
checks.py — Plex compatibility rules with new 6-level severity.

Severity scale (worst → best):
  unplayable         — File has issues or formats Plex can't play
  always_transcode   — Will always transcode (assume Chrome web client baseline)
  possible_transcode — Some clients won't direct-play
  high_bitrate       — Bitrate above configured threshold
  info               — Worth noting but generally fine
  ok                 — Clean
"""
import json
import re
import subprocess
from pathlib import Path

# ─── Severity ─────────────────────────────────────────────────────────────────
SEVERITY = ["ok", "info", "high_bitrate", "possible_transcode", "always_transcode", "unplayable"]
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY)}
SEVERITY_LABELS = {
    "unplayable":         "Unplayable",
    "always_transcode":   "Always Transcode",
    "possible_transcode": "Possible Transcode",
    "high_bitrate":       "High Bitrate",
    "info":               "Info",
    "ok":                 "OK",
}

def max_severity(a, b):
    return a if SEVERITY_RANK[a] >= SEVERITY_RANK[b] else b

# ─── Devices ──────────────────────────────────────────────────────────────────
# Plex devices (the original list)
PLEX_DEVICES = [
    "Apple TV 4K", "Apple TV HD", "iOS / iPadOS",
    "Android (mobile)", "Android TV",
    "Chromecast w/ Google TV", "Chromecast (3rd gen)",
    "Roku Ultra", "Roku Streaming Stick",
    "Fire TV (4K Max / Cube)", "Fire TV Stick",
    "Smart TVs (Samsung/LG)",
    "PlayStation 5", "Xbox Series X/S",
    "Web browser (Chrome/Edge)", "Web browser (Safari)",
    "Plex HTPC / Desktop",
]

# Jellyfin devices — focused on the ecosystem's actual clients
JELLYFIN_DEVICES = [
    "Jellyfin Web (Chrome/Edge)", "Jellyfin Web (Safari/Firefox)",
    "Jellyfin Media Player (Desktop)",
    "Jellyfin Android", "Jellyfin Android TV",
    "Jellyfin iOS / iPadOS",
    "Jellyfin for Roku", "Jellyfin for Fire TV",
    "Jellyfin for Apple TV (Swiftfin)",
    "Kodi (Jellyfin add-on)",
    "Infuse (Jellyfin)",
]

# Device → ecosystem map for filtering
DEVICE_ECOSYSTEM = {}
for d in PLEX_DEVICES:     DEVICE_ECOSYSTEM[d] = "plex"
for d in JELLYFIN_DEVICES: DEVICE_ECOSYSTEM[d] = "jellyfin"

# DEVICES kept as full list for backwards compatibility — rules pre-fill all 17 Plex devices
# Jellyfin devices are added by enrich_devices_for_jellyfin() below, based on rule category/codec
DEVICES = PLEX_DEVICES  # alias for any old code

def filter_devices_for_mode(affected, mode):
    """Filter device matrix entries by compatibility mode."""
    if mode == "both": return affected
    return [a for a in affected if DEVICE_ECOSYSTEM.get(a.get("device")) == mode]

# Status values used in `affected`:
#   ok | transcode | fail | partial

# ─── File categorisation ──────────────────────────────────────────────────────
MEDIA_EXTS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".ts", ".m2ts",
    ".mpg", ".mpeg", ".flv", ".webm", ".divx", ".ogv", ".rmvb", ".3gp",
}
SUBTITLE_EXTS = {
    ".srt", ".ass", ".ssa", ".sub", ".idx", ".sup", ".vtt", ".smi",
    ".pgs", ".usf", ".dfxp", ".ttml",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
METADATA_EXTS = {".nfo", ".xml", ".txt", ".sfv", ".md5", ".sha256", ".edl", ".theme"}


def categorise_file(path: Path) -> str:
    """Return one of: media, subtitle, image, metadata, junk."""
    ext = path.suffix.lower()
    if ext in MEDIA_EXTS:    return "media"
    if ext in SUBTITLE_EXTS: return "subtitle"
    if ext in IMAGE_EXTS:    return "image"
    if ext in METADATA_EXTS: return "metadata"
    return "junk"


def should_ignore(path: Path, patterns: list) -> bool:
    """Match against ignore patterns.

    Patterns can be:
      - exact filename: ".plexmatch" / "Thumbs.db"
      - extension wildcard: "*.tmp" / "*.partial"
      - prefix wildcard: "_UNPACK_*" / "sample_*"
      - directory component: "@eaDir" / ".AppleDouble"

    Glob patterns match against both the filename AND any directory in the path,
    so "_UNPACK_*" will skip files in any directory whose name starts with _UNPACK_.
    """
    import fnmatch
    name = path.name
    for p in patterns or []:
        if not p: continue
        # Exact name match
        if name == p: return True
        # Component match (folder anywhere in path) — exact name
        if p in path.parts: return True
        # Glob match — try filename and every parent directory name
        if any(ch in p for ch in "*?["):
            if fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(name.lower(), p.lower()):
                return True
            for part in path.parts:
                if fnmatch.fnmatchcase(part, p) or fnmatch.fnmatchcase(part.lower(), p.lower()):
                    return True
    return False


# ─── ffprobe ──────────────────────────────────────────────────────────────────

def run_ffprobe(path, extra_args=None):
    cmd = ["ffprobe","-v","quiet","-print_format","json",
           "-show_streams","-show_format","-show_chapters", str(path)]
    if extra_args:
        cmd = cmd[:3] + extra_args + cmd[3:]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0: return json.loads(r.stdout)
    except Exception: pass
    return None

def probe_sample(path, offset=60):
    meta = run_ffprobe(path)
    if not meta: return None
    duration = float(meta.get("format", {}).get("duration", 0) or 0)
    off = min(offset, max(0, duration * 0.1))
    extra = ["-ss", str(int(off)), "-read_intervals", "%+30"] if off > 0 else ["-read_intervals", "%+30"]
    return run_ffprobe(path, extra_args=extra) or meta


def derive_fields(probe):
    if not probe: return {}
    fmt = probe.get("format", {}) or {}
    streams = probe.get("streams", []) or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    out = {
        "container": (fmt.get("format_name") or "").split(",")[0] or None,
        "duration_sec": float(fmt.get("duration") or 0) or None,
        "bitrate": int(fmt.get("bit_rate") or 0) or None,
    }
    if video:
        out["codec"] = video.get("codec_name")
        w, h = video.get("width") or 0, video.get("height") or 0
        if w and h: out["resolution"] = f"{w}x{h}"
        for sd in video.get("side_data_list", []) or []:
            sdt = sd.get("side_data_type") or ""
            if "DOVI" in sdt or "Dolby Vision" in sdt or "DOWI" in sdt:
                p = sd.get("dv_profile", sd.get("profile"))
                out["dovi_profile"] = f"DoVi Profile {p}" if p is not None else "DoVi (unknown profile)"
                break
    if audio:
        out["audio_codec"] = audio.get("codec_name")
    return out


# ─── Subtitle validation (external subs) ──────────────────────────────────────

LANG_TAG_RE = re.compile(r"\.([a-z]{2,3})(?:[-_][A-Za-z0-9]+)?$", re.I)
# common forced/sdh tags before language
SUB_FLAGS_RE = re.compile(r"\.(forced|sdh|cc|hi|default)$", re.I)


def parse_sub_filename(name: str):
    """
    Extract base + language tag from subtitle filenames like:
      Movie.Name.2020.en.srt           -> ('Movie.Name.2020', 'en', None)
      Movie.Name.2020.en.forced.srt    -> ('Movie.Name.2020', 'en', 'forced')
      Movie.Name.2020.srt              -> ('Movie.Name.2020', None, None)
    """
    stem = Path(name).stem  # drops the .srt/.ass etc.
    flag = None
    m = SUB_FLAGS_RE.search(stem)
    if m:
        flag = m.group(1).lower()
        stem = stem[:m.start()]
    lang = None
    m2 = LANG_TAG_RE.search(stem)
    if m2:
        lang = m2.group(1).lower()
        stem = stem[:m2.start()]
    return stem, lang, flag


def validate_subtitle_file(path: Path) -> dict:
    """
    Returns dict with:
      readable: bool
      format_ok: bool
      lang: str|None
      flag: str|None
      base_name: str
      issue: str|None       — short problem description, if any
    """
    out = {
        "readable": False, "format_ok": False, "lang": None,
        "flag": None, "base_name": "", "issue": None,
    }
    base, lang, flag = parse_sub_filename(path.name)
    out["base_name"] = base
    out["lang"] = lang
    out["flag"] = flag

    ext = path.suffix.lower()

    try:
        size = path.stat().st_size
        if size == 0:
            out["issue"] = "Empty subtitle file (0 bytes)"
            return out
        if size > 10_000_000:
            out["issue"] = "Suspiciously large subtitle (>10MB)"
            return out
    except OSError as e:
        out["issue"] = f"Cannot stat subtitle: {e}"
        return out

    # Try multiple encodings
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                text = f.read(64 * 1024)  # first 64KB is enough to validate format
            break
        except UnicodeDecodeError:
            continue
        except Exception as e:
            out["issue"] = f"Cannot read: {e}"
            return out

    if text is None:
        out["issue"] = "Unrecognised text encoding"
        return out

    out["readable"] = True
    text_str = text.lstrip()

    # Format-specific structure checks
    if ext == ".srt":
        # Must have an arrow timecode somewhere in first 64KB
        if "-->" not in text_str:
            out["issue"] = "SRT has no timecode arrows (-->) — likely corrupt"
            return out
        # Should have at least one entry-numbered line
        if not re.search(r"^\s*\d+\s*$", text_str[:8192], re.M):
            out["issue"] = "SRT missing cue numbers"
            return out
        out["format_ok"] = True

    elif ext == ".vtt":
        if not text_str.startswith("WEBVTT"):
            out["issue"] = "VTT file missing WEBVTT header"
            return out
        out["format_ok"] = True

    elif ext in {".ass", ".ssa"}:
        if "[Script Info]" not in text_str:
            out["issue"] = "ASS/SSA missing [Script Info] header"
            return out
        if "[Events]" not in text_str:
            out["issue"] = "ASS/SSA missing [Events] section"
            return out
        out["format_ok"] = True

    elif ext == ".idx":
        if "id:" not in text_str.lower() and "size:" not in text_str.lower():
            out["issue"] = "VobSub .idx missing required directives"
            return out
        out["format_ok"] = True

    elif ext == ".smi":
        if "<sami" not in text_str.lower() and "<body" not in text_str.lower():
            out["issue"] = "SAMI subtitle missing required tags"
            return out
        out["format_ok"] = True

    elif ext in {".sub", ".sup", ".pgs"}:
        # Binary formats — skip text validation
        out["format_ok"] = True

    elif ext in {".usf", ".dfxp", ".ttml"}:
        if "<?xml" not in text_str and "<tt" not in text_str.lower():
            out["issue"] = f"{ext.upper()} file doesn't look like XML"
            return out
        out["format_ok"] = True

    else:
        out["format_ok"] = True

    return out


# ═══════════════════════════════════════════════════════════════════════════════
# Issue helper
# ═══════════════════════════════════════════════════════════════════════════════

def _issue(rule_key, severity, category, message, detail, affected):
    return {
        "rule_key": rule_key, "severity": severity, "category": category,
        "message": message, "detail": detail, "affected": affected,
    }

def _all_devices(status):
    return [{"device": d, "status": status} for d in PLEX_DEVICES]

def _all_jellyfin_devices(status):
    return [{"device": d, "status": status} for d in JELLYFIN_DEVICES]

def _aff(*pairs):
    out = []
    for spec in pairs:
        for dev, status in spec.items():
            out.append({"device": dev, "status": status})
    return out


# Jellyfin compatibility profiles — for each rule we add Jellyfin-specific impact.
# Jellyfin clients generally have BETTER codec direct-play (ffmpeg.wasm in web, native on
# Android/iOS) but WORSE Dolby Vision support — DV passthrough is buggy/unsupported on most.
def jellyfin_aff_for_rule(rule_key, default_status):
    """
    Return a list of Jellyfin device statuses for a given rule.
    `default_status` is the fallback used for rules without specific Jellyfin behavior.
    """
    overrides = JELLYFIN_OVERRIDES.get(rule_key)
    if overrides is None:
        # Fall back: treat Jellyfin like Plex by default
        return [{"device": d, "status": default_status} for d in JELLYFIN_DEVICES]
    return [{"device": d, "status": overrides.get(d, default_status)} for d in JELLYFIN_DEVICES]


# Jellyfin-specific overrides for select rules (where behavior diverges from Plex significantly)
# Only specify deviations from the rule's default.
JELLYFIN_OVERRIDES = {
    # Profile 5 — Jellyfin doesn't claim DV support at all, so it just plays the base layer if any
    "dovi_p5": {
        "Jellyfin Web (Chrome/Edge)":   "fail",
        "Jellyfin Web (Safari/Firefox)":"fail",
        "Jellyfin Media Player (Desktop)":"transcode",
        "Jellyfin Android":             "fail",
        "Jellyfin Android TV":          "fail",
        "Jellyfin iOS / iPadOS":        "fail",
        "Jellyfin for Roku":            "fail",
        "Jellyfin for Fire TV":         "fail",
        "Jellyfin for Apple TV (Swiftfin)":"fail",
        "Kodi (Jellyfin add-on)":       "transcode",
        "Infuse (Jellyfin)":            "ok",  # Infuse handles DV well
    },
    "dovi_p7": {
        # Jellyfin strips DV layer; passes HDR10 base, Infuse can handle DV
        "Jellyfin Media Player (Desktop)":"ok",
        "Kodi (Jellyfin add-on)":       "ok",
        "Infuse (Jellyfin)":            "ok",
    },
    "dovi_p8": {
        "Infuse (Jellyfin)":            "ok",
    },
    # H.264 Hi10P — Jellyfin Media Player (mpv-based) handles this natively!
    "video_h264_10bit": {
        "Jellyfin Media Player (Desktop)":"ok",
        "Kodi (Jellyfin add-on)":       "ok",
        "Infuse (Jellyfin)":            "ok",
    },
    # HEVC 10-bit — Jellyfin's mpv-based desktop client and Infuse handle natively
    "video_hevc_10bit": {
        "Jellyfin Media Player (Desktop)":"ok",
        "Kodi (Jellyfin add-on)":       "ok",
        "Infuse (Jellyfin)":            "ok",
    },
    # AV1 — Jellyfin web-client transcodes server-side; native clients vary
    "video_av1": {
        "Jellyfin Web (Chrome/Edge)":   "ok",
        "Jellyfin Media Player (Desktop)":"ok",
        "Infuse (Jellyfin)":            "ok",
    },
    # VP9 — strong on Jellyfin web (Chrome/FF), Infuse, MPV
    "video_vp9": {
        "Jellyfin Web (Chrome/Edge)":   "ok",
        "Jellyfin Web (Safari/Firefox)":"ok",
        "Jellyfin Media Player (Desktop)":"ok",
        "Jellyfin Android":             "ok",
        "Jellyfin Android TV":          "ok",
        "Kodi (Jellyfin add-on)":       "ok",
        "Infuse (Jellyfin)":            "transcode",
    },
    # TrueHD/DTS-HD MA/Atmos — Infuse handles bitstream pass-through, mpv too
    "audio_truehd": {
        "Jellyfin Media Player (Desktop)":"ok",
        "Kodi (Jellyfin add-on)":       "ok",
        "Infuse (Jellyfin)":            "ok",
    },
    "audio_dts_hd_ma": {
        "Jellyfin Media Player (Desktop)":"ok",
        "Kodi (Jellyfin add-on)":       "ok",
        "Infuse (Jellyfin)":            "ok",
    },
    # PGS subtitles — Infuse and mpv-based clients render natively
    "sub_image_hdmv_pgs_subtitle": {
        "Jellyfin Media Player (Desktop)":"ok",
        "Kodi (Jellyfin add-on)":       "ok",
        "Infuse (Jellyfin)":            "ok",
        "Jellyfin Android TV":          "ok",
    },
    # ASS subtitles — mpv-based clients have full libass
    "sub_text_ass": {
        "Jellyfin Media Player (Desktop)":"ok",
        "Kodi (Jellyfin add-on)":       "ok",
        "Infuse (Jellyfin)":            "ok",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# Main evaluator
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate(file_record, *, bitrate_threshold=80_000_000, paired_media=None):
    """
    file_record: dict with extension, size_bytes, category, scan_status, probe (parsed dict).
    paired_media: dict of media file row if this is a subtitle (used for sub validation).

    Returns list of issues.
    """
    issues = []
    cat = file_record.get("category") or "junk"
    size = file_record.get("size_bytes") or 0
    status = file_record.get("scan_status") or "ok"

    # ── Junk: separate category — single info issue ──────────────────────────
    if cat == "junk":
        issues.append(_issue(
            "file_unknown_extension", "info", "non_media",
            f"Unknown file type ({file_record.get('extension') or 'no extension'})",
            "This file's extension is not in the configured allowlist of media, subtitle, image or metadata extensions. It will not be played by Plex but may be safe to keep (artwork, packaging, etc.) or to remove.",
            [],
        ))
        return issues

    # ── Image / metadata (NFO/JPG etc.) — no issues unless empty ────────────
    if cat in ("image", "metadata"):
        if size == 0:
            issues.append(_issue(
                "file_empty", "unplayable", "integrity",
                "Empty file (0 bytes)",
                "The file has no data — likely a failed download or interrupted move.",
                _all_devices("fail"),
            ))
        return issues

    # ── Subtitles ──────────────────────────────────────────────────────────────
    if cat == "subtitle":
        return _evaluate_subtitle(file_record, paired_media)

    # ── Media files from here ──────────────────────────────────────────────────
    if size == 0:
        issues.append(_issue(
            "file_empty", "unplayable", "integrity",
            "Empty media file (0 bytes)",
            "The file has zero bytes. Plex will not index this. Common cause: failed download or interrupted move.",
            _all_devices("fail"),
        ))
        return issues

    if status == "probe_failed":
        issues.append(_issue(
            "file_probe_failed", "unplayable", "integrity",
            "Cannot read file (ffprobe failed)",
            "ffprobe could not parse this file at all. The file is likely corrupt, has an unrecognised header, or is mislabelled with a media extension. Plex will fail to import it or show it as 'unavailable'.",
            _all_devices("fail"),
        ))
        return issues

    probe = file_record.get("probe") or {}
    if not probe:
        return issues

    streams = probe.get("streams", []) or []
    fmt     = probe.get("format", {}) or {}
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    sub_streams   = [s for s in streams if s.get("codec_type") == "subtitle"]

    if not video_streams:
        issues.append(_issue(
            "no_video_stream", "unplayable", "integrity",
            "No video stream",
            "The file is in a video container but has no video stream. Plex cannot play this; it may be an audio-only file or a corrupt download.",
            _all_devices("fail"),
        ))
        return issues

    vs = video_streams[0]

    issues += _check_dovi(vs)
    issues += _check_video_codec(vs)
    issues += _check_hdr(vs)
    issues += _check_container(probe, fmt, file_record.get("extension"), video_streams)
    issues += _check_audio(audio_streams)
    issues += _check_subtitles(sub_streams)
    issues += _check_resolution_fps(vs)
    issues += _check_bitrate(fmt, bitrate_threshold)

    # Enrich each issue's `affected` list with Jellyfin-equivalent entries
    for iss in issues:
        _enrich_with_jellyfin(iss)

    return issues


def _enrich_with_jellyfin(issue):
    """Append Jellyfin device statuses to an issue's `affected` list."""
    rule_key = issue.get("rule_key", "")
    plex_aff = issue.get("affected", []) or []

    # Use the most common Plex status as the default for unspecified Jellyfin devices
    if plex_aff:
        from collections import Counter
        counts = Counter(a.get("status", "ok") for a in plex_aff)
        default_status = counts.most_common(1)[0][0]
    else:
        default_status = "ok"

    issue["affected"] = plex_aff + jellyfin_aff_for_rule(rule_key, default_status)


def _evaluate_subtitle(file_record, paired_media):
    """Evaluate an external subtitle file."""
    issues = []
    path = Path(file_record["path"])

    val = validate_subtitle_file(path)

    if val.get("issue"):
        sev = "unplayable" if "Empty" in val["issue"] or "Cannot" in val["issue"] else "info"
        issues.append(_issue(
            "subtitle_invalid", sev, "subtitles",
            f"Subtitle file invalid: {val['issue']}",
            f"External subtitle file failed validation. Plex will likely skip it or fail to display. Reason: {val['issue']}",
            [],
        ))
        return issues

    if not val.get("readable"):
        issues.append(_issue(
            "subtitle_unreadable", "info", "subtitles",
            "Subtitle file unreadable",
            "Could not read the subtitle file with any common encoding. Plex may fail to load it.",
            [],
        ))

    # No paired media file in same folder?
    if not paired_media:
        issues.append(_issue(
            "subtitle_orphan", "info", "subtitles",
            "Orphan subtitle (no matching media file)",
            f"No media file in the same folder matches the subtitle's base name '{val['base_name']}'. "
            "Plex external subtitles are paired by filename — without a matching media file, this subtitle will never be loaded.",
            [],
        ))
    elif val.get("lang") is None:
        issues.append(_issue(
            "subtitle_no_lang", "info", "subtitles",
            "No language tag in filename",
            "Plex matches external subtitles to media using a 2- or 3-letter language tag in the filename "
            "(e.g. movie.en.srt). Without a tag, Plex labels this 'Unknown' and may fail to auto-select it.",
            [],
        ))

    return issues


# ═══════════════════════════════════════════════════════════════════════════════
# Individual rule sets
# ═══════════════════════════════════════════════════════════════════════════════

def _check_dovi(vs):
    issues = []
    for sd in vs.get("side_data_list", []) or []:
        sdt = sd.get("side_data_type") or ""
        if not any(k in sdt for k in ("DOVI", "Dolby Vision", "DOWI")):
            continue
        profile = sd.get("dv_profile", sd.get("profile"))
        bl_present = sd.get("bl_present_flag", 1)

        if profile is None:
            sev = "unplayable" if bl_present == 0 else "possible_transcode"
            issues.append(_issue(
                "dovi_unknown", sev, "dolby_vision",
                "Dolby Vision detected, profile unreadable",
                "ffprobe found Dolby Vision metadata but could not read the profile number. "
                + ("No base layer present — most likely Profile 5 which Plex cannot play." if bl_present == 0 else "Verify playback manually."),
                _all_devices("fail" if bl_present == 0 else "transcode"),
            ))
            continue

        p = int(profile)
        if p == 5:
            issues.append(_issue(
                "dovi_p5", "unplayable", "dolby_vision",
                "DoVi Profile 5 — UNPLAYABLE in Plex",
                "Profile 5 is single-layer Dolby Vision with NO HDR10 fallback. The colour samples use IPTPQc2 — a non-standard colour space. Plex returns 'File is unplayable. DoVi (Profile 5) color space is not supported' on every standard client. Only an LG TV with Plex HTPC and DV passthrough enabled can play it correctly. The fix is to remux to Profile 8 with `dovi_tool` injecting an HDR10 fallback, or re-encode entirely.",
                _aff(
                    {"Apple TV 4K":"fail"},{"Apple TV HD":"fail"},
                    {"iOS / iPadOS":"fail"},{"Android (mobile)":"fail"},{"Android TV":"fail"},
                    {"Chromecast w/ Google TV":"fail"},{"Chromecast (3rd gen)":"fail"},
                    {"Roku Ultra":"fail"},{"Roku Streaming Stick":"fail"},
                    {"Fire TV (4K Max / Cube)":"fail"},{"Fire TV Stick":"fail"},
                    {"Smart TVs (Samsung/LG)":"fail"},{"PlayStation 5":"fail"},
                    {"Xbox Series X/S":"fail"},{"Web browser (Chrome/Edge)":"fail"},
                    {"Web browser (Safari)":"fail"},{"Plex HTPC / Desktop":"transcode"},
                ),
            ))
        elif p == 7:
            issues.append(_issue(
                "dovi_p7", "info", "dolby_vision",
                "DoVi Profile 7 — Plays as HDR10",
                "Dual-layer Dolby Vision (UHD Blu-ray rip). Plex strips the DV enhancement layer and serves the HDR10 base layer. You see HDR10 — DV enhancement is lost.",
                _aff(
                    {"Apple TV 4K":"ok"},{"Apple TV HD":"ok"},
                    {"iOS / iPadOS":"transcode"},{"Android (mobile)":"transcode"},{"Android TV":"ok"},
                    {"Chromecast w/ Google TV":"ok"},{"Chromecast (3rd gen)":"transcode"},
                    {"Roku Ultra":"ok"},{"Roku Streaming Stick":"transcode"},
                    {"Fire TV (4K Max / Cube)":"ok"},{"Fire TV Stick":"transcode"},
                    {"Smart TVs (Samsung/LG)":"ok"},{"PlayStation 5":"ok"},{"Xbox Series X/S":"ok"},
                    {"Web browser (Chrome/Edge)":"transcode"},{"Web browser (Safari)":"transcode"},
                    {"Plex HTPC / Desktop":"ok"},
                ),
            ))
        elif p == 8:
            issues.append(_issue(
                "dovi_p8", "info", "dolby_vision",
                "DoVi Profile 8 — Plays cleanly",
                "Single-layer DV with HDR10 (8.1) or SDR (8.4) fallback. The most compatible DV format — DV-capable clients use the full DV layer, others fall back to HDR10. No transcoding forced by colour space.",
                _all_devices("ok"),
            ))
        elif p == 4:
            issues.append(_issue(
                "dovi_p4", "possible_transcode", "dolby_vision",
                "DoVi Profile 4 — Possible colour artefacts",
                "Dual-layer DV with HDR10 base, but uses Rec.709 cross-compatibility metadata. Plex support is incomplete: some clients render incorrect colours (greenish or washed-out).",
                _all_devices("transcode"),
            ))
        else:
            issues.append(_issue(
                "dovi_unusual", "possible_transcode", "dolby_vision",
                f"DoVi Profile {p} — uncommon",
                f"Dolby Vision Profile {p} is rare and Plex behaviour is undocumented. Test playback on your client.",
                _all_devices("transcode"),
            ))
    return issues


def _check_video_codec(vs):
    issues = []
    codec   = (vs.get("codec_name") or "").lower()
    profile = (vs.get("profile") or "").lower()
    pf      = (vs.get("pix_fmt") or "").lower()
    level   = vs.get("level") or 0

    OBSCURE = {
        "vp8":       "VP8 — Plex cannot direct-play VP8; always transcoded.",
        "theora":    "Ogg Theora — Plex always transcodes.",
        "flv1":      "Flash Video — always transcodes.",
        "svq3":      "Sorenson Video 3 — always transcodes.",
        "wmv1":      "WMV 7 — old Windows Media; always transcodes.",
        "wmv2":      "WMV 8 — old Windows Media; always transcodes.",
        "wmv3":      "WMV 9 — VC-1 variant; always transcodes.",
        "msmpeg4v2": "MS-MPEG-4 v2 — always transcodes.",
        "msmpeg4v3": "MS-MPEG-4 v3 (DivX 3) — always transcodes.",
        "rv40":      "RealVideo 4 — RealMedia; always transcodes and frequently fails.",
        "indeo3":    "Intel Indeo 3 — always transcodes.",
        "cinepak":   "Cinepak — always transcodes.",
        "huffyuv":   "HuffYUV lossless — always transcodes.",
        "ffvhuff":   "FFmpeg HuffYUV — always transcodes.",
        "utvideo":   "UT Video — lossless intermediate.",
        "prores":    "Apple ProRes — editing codec; always transcodes (CPU heavy).",
        "dnxhd":     "Avid DNxHD — editing codec; always transcodes.",
        "dnxhr":     "Avid DNxHR — editing codec; always transcodes.",
        "magicyuv":  "MagicYUV — lossless intermediate.",
        "v210":      "V210 uncompressed — always transcodes.",
        "cineform":  "GoPro CineForm — always transcodes.",
    }
    if codec in OBSCURE:
        sev = "unplayable" if codec == "rv40" else "always_transcode"
        issues.append(_issue(
            f"video_codec_{codec}", sev, "video_codec",
            f"{codec.upper()} — {('cannot play' if sev == 'unplayable' else 'always transcodes')}",
            OBSCURE[codec] + " The Plex server CPU/GPU has to convert every frame to H.264 in real time. On a CPU-only Plex server this means 4–10× CPU usage.",
            _all_devices("fail" if sev == "unplayable" else "transcode"),
        ))
        return issues

    if codec == "h264":
        if "10" in pf or "high 10" in profile:
            issues.append(_issue(
                "video_h264_10bit", "always_transcode", "video_codec",
                "H.264 10-bit (Hi10P) — always transcodes",
                "Almost no consumer device has hardware support for 10-bit H.264. The H.264 spec includes Hi10P, but it was never adopted outside the anime fansub community. Every Plex client falls back to software decode at best — usually it fails. Plex transcodes Hi10P to 8-bit H.264 on the server, costing significant CPU. Re-encode to HEVC 10-bit if you need 10-bit precision.",
                _aff(
                    *[{d: "transcode"} for d in DEVICES if d != "Plex HTPC / Desktop"],
                    {"Plex HTPC / Desktop": "ok"},
                ),
            ))
        if "444" in profile:
            issues.append(_issue(
                "video_h264_444", "always_transcode", "video_codec",
                "H.264 High 4:4:4 — always transcodes",
                "4:4:4 chroma subsampling preserves full colour resolution but is unsupported by every consumer hardware decoder. Plex always transcodes to 4:2:0.",
                _all_devices("transcode"),
            ))
        elif "422" in profile:
            issues.append(_issue(
                "video_h264_422", "possible_transcode", "video_codec",
                "H.264 4:2:2 — likely transcodes",
                "4:2:2 is used in production workflows. Most Plex clients cannot direct-play; expect transcoding on TVs and mobile.",
                _all_devices("transcode"),
            ))
        elif isinstance(level, int) and level > 41:
            level_disp = level / 10 if level >= 10 else level
            issues.append(_issue(
                "video_h264_high_level", "possible_transcode", "video_codec",
                f"H.264 level {level_disp} (above 4.1)",
                f"H.264 level {level_disp} requires more decoder buffer than older devices have. Apple TV HD, original Chromecast, and older Roku/Fire TV sticks max out at level 4.1. Modern devices support 5.1 and above.",
                _aff(
                    {"Apple TV 4K":"ok"},{"Apple TV HD":"transcode"},
                    {"iOS / iPadOS":"ok"},{"Android (mobile)":"ok"},{"Android TV":"ok"},
                    {"Chromecast w/ Google TV":"ok"},{"Chromecast (3rd gen)":"transcode"},
                    {"Roku Ultra":"ok"},{"Roku Streaming Stick":"transcode"},
                    {"Fire TV (4K Max / Cube)":"ok"},{"Fire TV Stick":"transcode"},
                    {"Smart TVs (Samsung/LG)":"ok"},{"PlayStation 5":"ok"},{"Xbox Series X/S":"ok"},
                    {"Web browser (Chrome/Edge)":"ok"},{"Web browser (Safari)":"ok"},
                    {"Plex HTPC / Desktop":"ok"},
                ),
            ))

    elif codec == "hevc":
        if "main 12" in profile or "12" in pf:
            issues.append(_issue(
                "video_hevc_12bit", "always_transcode", "video_codec",
                "HEVC Main 12 (12-bit) — always transcodes",
                "12-bit HEVC has no consumer hardware decoder. Plex must transcode every frame in software, which is impossibly slow without a discrete GPU.",
                _all_devices("transcode"),
            ))
        elif "main 10" in profile or "10" in pf:
            issues.append(_issue(
                "video_hevc_10bit", "possible_transcode", "video_codec",
                "HEVC Main 10 (10-bit) — fine on modern hardware",
                "10-bit HEVC is the standard for 4K HDR content. Modern hardware (Apple TV 4K, Shield, Roku Ultra, Fire TV 4K Max, recent smart TVs) decodes natively. Older devices force the server to transcode.",
                _aff(
                    {"Apple TV 4K":"ok"},{"Apple TV HD":"transcode"},
                    {"iOS / iPadOS":"ok"},{"Android (mobile)":"ok"},{"Android TV":"ok"},
                    {"Chromecast w/ Google TV":"ok"},{"Chromecast (3rd gen)":"transcode"},
                    {"Roku Ultra":"ok"},{"Roku Streaming Stick":"transcode"},
                    {"Fire TV (4K Max / Cube)":"ok"},{"Fire TV Stick":"transcode"},
                    {"Smart TVs (Samsung/LG)":"ok"},{"PlayStation 5":"ok"},{"Xbox Series X/S":"ok"},
                    {"Web browser (Chrome/Edge)":"transcode"},{"Web browser (Safari)":"ok"},
                    {"Plex HTPC / Desktop":"ok"},
                ),
            ))
        if "4:4:4" in profile or "444" in profile:
            issues.append(_issue(
                "video_hevc_444", "always_transcode", "video_codec",
                "HEVC 4:4:4 — always transcodes",
                "4:4:4 HEVC is unsupported by all consumer Plex clients.",
                _all_devices("transcode"),
            ))

    elif codec == "av1":
        if "10" in pf:
            issues.append(_issue(
                "video_av1_10bit", "always_transcode", "video_codec",
                "AV1 10-bit — almost no client direct-plays",
                "AV1 hardware decode is still rare. AV1 10-bit narrows the field further — only Apple TV 4K (3rd gen), recent NVIDIA Shield, and high-end smart TVs handle it.",
                _aff(
                    {"Apple TV 4K":"ok"},{"Apple TV HD":"fail"},
                    {"iOS / iPadOS":"transcode"},{"Android (mobile)":"transcode"},{"Android TV":"transcode"},
                    {"Chromecast w/ Google TV":"ok"},{"Chromecast (3rd gen)":"fail"},
                    {"Roku Ultra":"transcode"},{"Roku Streaming Stick":"transcode"},
                    {"Fire TV (4K Max / Cube)":"transcode"},{"Fire TV Stick":"fail"},
                    {"Smart TVs (Samsung/LG)":"transcode"},{"PlayStation 5":"transcode"},
                    {"Xbox Series X/S":"transcode"},{"Web browser (Chrome/Edge)":"transcode"},
                    {"Web browser (Safari)":"transcode"},{"Plex HTPC / Desktop":"ok"},
                ),
            ))
        else:
            issues.append(_issue(
                "video_av1", "possible_transcode", "video_codec",
                "AV1 — limited Plex client support",
                "Plex added AV1 direct-play in 2024. Modern devices (Apple TV 4K 3rd gen, Chromecast w/ Google TV, NVIDIA Shield, recent Roku, PS5) handle AV1. Older devices fall back to server transcode, which is extremely CPU heavy.",
                _aff(
                    {"Apple TV 4K":"ok"},{"Apple TV HD":"transcode"},
                    {"iOS / iPadOS":"transcode"},{"Android (mobile)":"transcode"},{"Android TV":"transcode"},
                    {"Chromecast w/ Google TV":"ok"},{"Chromecast (3rd gen)":"transcode"},
                    {"Roku Ultra":"ok"},{"Roku Streaming Stick":"transcode"},
                    {"Fire TV (4K Max / Cube)":"transcode"},{"Fire TV Stick":"transcode"},
                    {"Smart TVs (Samsung/LG)":"ok"},{"PlayStation 5":"ok"},{"Xbox Series X/S":"transcode"},
                    {"Web browser (Chrome/Edge)":"ok"},{"Web browser (Safari)":"transcode"},
                    {"Plex HTPC / Desktop":"ok"},
                ),
            ))

    elif codec == "vp9":
        issues.append(_issue(
            "video_vp9", "possible_transcode", "video_codec",
            "VP9 — limited direct-play",
            "VP9 hardware support exists on Android, Chromecast, and modern smart TVs, but Apple TV and most Roku models can't direct-play VP9 — they force a server transcode.",
            _aff(
                {"Apple TV 4K":"transcode"},{"Apple TV HD":"transcode"},
                {"iOS / iPadOS":"transcode"},{"Android (mobile)":"ok"},{"Android TV":"ok"},
                {"Chromecast w/ Google TV":"ok"},{"Chromecast (3rd gen)":"ok"},
                {"Roku Ultra":"transcode"},{"Roku Streaming Stick":"transcode"},
                {"Fire TV (4K Max / Cube)":"ok"},{"Fire TV Stick":"transcode"},
                {"Smart TVs (Samsung/LG)":"ok"},{"PlayStation 5":"transcode"},{"Xbox Series X/S":"ok"},
                {"Web browser (Chrome/Edge)":"ok"},{"Web browser (Safari)":"transcode"},
                {"Plex HTPC / Desktop":"ok"},
            ),
        ))
    elif codec == "mpeg2video":
        issues.append(_issue(
            "video_mpeg2", "possible_transcode", "video_codec",
            "MPEG-2 — Plex transcodes for modern clients",
            "MPEG-2 (DVDs, broadcast TV, .ts captures) is direct-played by very few modern devices. Plex transcodes to H.264.",
            _all_devices("transcode"),
        ))
    elif codec == "vc1":
        issues.append(_issue(
            "video_vc1", "possible_transcode", "video_codec",
            "VC-1 — Plex usually transcodes",
            "VC-1 (HD-DVD, some Blu-rays, older WMV HD) has limited hardware decoder support. Most Plex clients transcode.",
            _all_devices("transcode"),
        ))
    elif codec == "mpeg4" and "advanced" not in profile:
        issues.append(_issue(
            "video_mpeg4_part2", "possible_transcode", "video_codec",
            "MPEG-4 Part 2 (DivX/Xvid) — transcodes",
            "Old DivX/Xvid AVI content. Plex transcodes for most modern clients which dropped MPEG-4 Part 2 hardware decode years ago.",
            _all_devices("transcode"),
        ))

    if pf in {"yuv444p","yuv444p10le","yuv444p12le","gbrp","gbrp10le","rgb24","bgr24"}:
        issues.append(_issue(
            "video_chroma_444", "always_transcode", "video_codec",
            f"Non-4:2:0 chroma ({pf})",
            "Consumer hardware decoders only support 4:2:0 chroma. Anything else forces a software transcode on the Plex server.",
            _all_devices("transcode"),
        ))

    return issues


def _check_hdr(vs):
    issues = []
    ct = (vs.get("color_transfer") or "").lower()
    cp = (vs.get("color_primaries") or "").lower()
    cs = (vs.get("color_space") or "").lower()
    side_data = vs.get("side_data_list") or []

    has_hdr10_plus = any("HDR10+" in (sd.get("side_data_type") or "") or "HDR10 Plus" in (sd.get("side_data_type") or "") for sd in side_data)
    has_mastering = any("Mastering" in (sd.get("side_data_type") or "") for sd in side_data)

    if ct == "smpte2084" and "bt2020" in cp:
        issues.append(_issue(
            "hdr10", "info", "hdr",
            "HDR10 — passes through on capable displays",
            "HDR10 is the baseline open HDR format. Plex passes the metadata to the client. HDR-capable TVs render HDR. SDR clients need tone mapping, which requires Plex Pass + a hardware-accelerated server (Intel Quick Sync, NVIDIA NVENC, or Apple Silicon).",
            [{"device": d, "status": "ok" if d != "Web browser (Safari)" else "transcode"} for d in DEVICES],
        ))
        if not has_mastering:
            issues.append(_issue(
                "hdr10_no_mastering", "info", "hdr",
                "HDR10 missing mastering display metadata",
                "Without MaxCLL/MaxFALL and master display primaries, HDR10 tone mapping uses fallback values. On SDR clients this can produce crushed shadows or washed-out highlights.",
                _all_devices("partial"),
            ))

    if has_hdr10_plus:
        issues.append(_issue(
            "hdr10_plus", "info", "hdr",
            "HDR10+ dynamic metadata — Plex falls back to HDR10",
            "Plex does not preserve HDR10+ dynamic per-scene metadata; it passes only static HDR10. You lose the dynamic tone-mapping that HDR10+ provides on Samsung TVs and other HDR10+ displays.",
            _all_devices("partial"),
        ))

    if ct == "arib-std-b67":
        issues.append(_issue(
            "hdr_hlg", "possible_transcode", "hdr",
            "HLG (broadcast HDR) — varies by client",
            "Hybrid Log-Gamma (BBC, NHK). HLG-aware TVs render correctly; SDR-only clients get washed-out or over-bright output.",
            _aff(
                {"Apple TV 4K":"ok"},{"Apple TV HD":"transcode"},
                {"iOS / iPadOS":"ok"},{"Android (mobile)":"transcode"},{"Android TV":"ok"},
                {"Chromecast w/ Google TV":"ok"},{"Chromecast (3rd gen)":"transcode"},
                {"Roku Ultra":"ok"},{"Roku Streaming Stick":"transcode"},
                {"Fire TV (4K Max / Cube)":"ok"},{"Fire TV Stick":"transcode"},
                {"Smart TVs (Samsung/LG)":"ok"},{"PlayStation 5":"ok"},{"Xbox Series X/S":"ok"},
                {"Web browser (Chrome/Edge)":"transcode"},{"Web browser (Safari)":"transcode"},
                {"Plex HTPC / Desktop":"ok"},
            ),
        ))

    if "bt2020" in cp and ct not in {"smpte2084","arib-std-b67","smpte428"}:
        issues.append(_issue(
            "color_bt2020_no_hdr", "info", "hdr",
            "BT.2020 primaries with no HDR transfer — likely mislabelled",
            "The file claims wide gamut BT.2020 colour but uses an SDR transfer function. Plex passes the BT.2020 flag to the client, which may render oversaturated colours.",
            _all_devices("partial"),
        ))

    if not ct and not cs and not cp:
        issues.append(_issue(
            "color_no_metadata", "info", "hdr",
            "No colour metadata — Plex assumes sRGB",
            "Without colour-space tags, Plex defaults to sRGB / BT.709 SDR. If the content is HDR, BT.2020 or P3 it renders with wrong colours on every client.",
            _all_devices("partial"),
        ))
    return issues


def _check_container(probe, fmt, ext, video_streams):
    issues = []
    fmt_name = (fmt.get("format_name") or "").lower()
    duration = float(fmt.get("duration") or 0)

    if "rm" in fmt_name or ext == ".rmvb":
        issues.append(_issue(
            "container_realmedia", "unplayable", "container",
            "RealMedia container — Plex often fails entirely",
            "RMVB / RealMedia uses RV40 codec inside a RealMedia container. Plex transcoding is unreliable, frequently producing audio/video desync or hard failures.",
            _all_devices("fail"),
        ))

    if "avi" in fmt_name or ext == ".avi":
        issues.append(_issue(
            "container_avi", "possible_transcode", "container",
            "AVI container — limited subtitle and metadata support",
            "AVI cannot embed modern subtitles (SRT/ASS/PGS) or chapters. Plex usually transcodes AVI.",
            _all_devices("transcode"),
        ))
        for vs in video_streams:
            if vs.get("avg_frame_rate") and vs.get("r_frame_rate") and vs.get("avg_frame_rate") != vs.get("r_frame_rate"):
                issues.append(_issue(
                    "container_avi_vfr", "unplayable", "container",
                    "AVI with variable frame rate — known A/V sync issues",
                    "VFR AVI is a long-standing Plex pain point. Audio drifts ahead of video over long playback. Fix is to remux to MKV (which handles VFR cleanly) or re-encode to constant-frame-rate.",
                    _all_devices("fail"),
                ))
                break

    if ext in {".ogv",".ogm"} and "ogg" in fmt_name:
        issues.append(_issue(
            "container_ogv", "possible_transcode", "container",
            "OGG/OGV container — limited Plex support",
            "Plex's library scanner indexes OGV poorly and transcoding pipelines tend to drop subtitle tracks.",
            _all_devices("transcode"),
        ))

    if duration == 0:
        issues.append(_issue(
            "container_no_duration", "info", "container",
            "No duration metadata — broken seek bar",
            "Without duration, Plex cannot show runtime in the library or render a working seek bar. Usually means the file was truncated mid-write.",
            _all_devices("partial"),
        ))

    EXT_FORMAT = {
        ".mkv":["matroska"],".mp4":["mp4","mov"],".avi":["avi"],
        ".mov":["mov","mp4"],".ts":["mpegts"],".m2ts":["mpegts"],
        ".webm":["matroska","webm"],".flv":["flv"],".wmv":["asf"],
    }
    expected = EXT_FORMAT.get(ext, [])
    if expected and not any(e in fmt_name for e in expected):
        issues.append(_issue(
            "container_ext_mismatch", "info", "integrity",
            f"Extension '{ext}' doesn't match container '{fmt_name}'",
            "The file extension implies a different container than what's actually inside. Plex uses ffprobe-detected container, but other tools may misbehave.",
            [],
        ))

    if len(video_streams) > 1:
        issues.append(_issue(
            "container_multi_video", "info", "container",
            f"{len(video_streams)} video streams — Plex uses only the first",
            "Plex always plays the first video stream; alternate angles, bonus features, or behind-the-scenes streams are ignored.",
            [],
        ))

    return issues


def _check_audio(audio_streams):
    issues = []
    if not audio_streams:
        issues.append(_issue(
            "audio_none", "unplayable", "audio",
            "No audio streams",
            "The file has no audio. Plex will play it silently.",
            _all_devices("partial"),
        ))
        return issues

    for idx, s in enumerate(audio_streams):
        codec = (s.get("codec_name") or "").lower()
        profile = (s.get("profile") or "").lower()
        ch = s.get("channels") or 0
        tag = f"Track {idx+1} ({codec})"

        if codec == "truehd":
            atmos = "atmos" in profile or "joc" in profile
            issues.append(_issue(
                "audio_truehd", "possible_transcode", "audio",
                f"{tag}: TrueHD{' Atmos' if atmos else ''} — transcodes on most clients",
                "TrueHD is lossless Dolby. Bitstream pass-through to a TrueHD-capable AVR works on Apple TV 4K, NVIDIA Shield, and Plex HTPC; everything else transcodes to AC3 (5.1, lossy)." + (" Atmos object metadata is dropped on transcode." if atmos else ""),
                _aff(
                    {"Apple TV 4K":"ok"},{"Apple TV HD":"transcode"},
                    {"iOS / iPadOS":"transcode"},{"Android (mobile)":"transcode"},{"Android TV":"ok"},
                    {"Chromecast w/ Google TV":"transcode"},{"Chromecast (3rd gen)":"transcode"},
                    {"Roku Ultra":"transcode"},{"Roku Streaming Stick":"transcode"},
                    {"Fire TV (4K Max / Cube)":"transcode"},{"Fire TV Stick":"transcode"},
                    {"Smart TVs (Samsung/LG)":"transcode"},{"PlayStation 5":"transcode"},
                    {"Xbox Series X/S":"transcode"},{"Web browser (Chrome/Edge)":"transcode"},
                    {"Web browser (Safari)":"transcode"},{"Plex HTPC / Desktop":"ok"},
                ),
            ))
        elif codec == "dts":
            if any(x in profile for x in ("master audio","hd ma","dts-hd ma")):
                issues.append(_issue(
                    "audio_dts_hd_ma", "possible_transcode", "audio",
                    f"{tag}: DTS-HD Master Audio",
                    "Lossless DTS variant. Plex bitstreams to compatible AVRs and transcodes for everything else, falling back to DTS core 5.1 lossy.",
                    _all_devices("transcode"),
                ))
            elif "dts:x" in profile or "dtsx" in profile or "dts-x" in profile:
                issues.append(_issue(
                    "audio_dtsx", "possible_transcode", "audio",
                    f"{tag}: DTS:X object audio",
                    "DTS:X is the DTS competitor to Dolby Atmos. Transcoding strips height/object channels.",
                    _all_devices("transcode"),
                ))
            else:
                issues.append(_issue(
                    "audio_dts_core", "possible_transcode", "audio",
                    f"{tag}: DTS core",
                    "Plex passes DTS through HDMI when destination is DTS-capable AVR; for direct TV speakers, smart TVs without DTS licensing, and mobile devices, it transcodes to AAC or AC3.",
                    _aff(
                        {"Apple TV 4K":"ok"},{"Apple TV HD":"ok"},
                        {"iOS / iPadOS":"transcode"},{"Android (mobile)":"transcode"},{"Android TV":"ok"},
                        {"Chromecast w/ Google TV":"ok"},{"Chromecast (3rd gen)":"transcode"},
                        {"Roku Ultra":"ok"},{"Roku Streaming Stick":"transcode"},
                        {"Fire TV (4K Max / Cube)":"ok"},{"Fire TV Stick":"transcode"},
                        {"Smart TVs (Samsung/LG)":"transcode"},{"PlayStation 5":"ok"},
                        {"Xbox Series X/S":"ok"},{"Web browser (Chrome/Edge)":"transcode"},
                        {"Web browser (Safari)":"transcode"},{"Plex HTPC / Desktop":"ok"},
                    ),
                ))
        elif codec == "eac3":
            atmos = "atmos" in profile or "joc" in profile
            issues.append(_issue(
                "audio_eac3", "info", "audio",
                f"{tag}: Dolby Digital Plus (EAC3){' Atmos' if atmos else ''}",
                "EAC3 is widely supported. Most clients direct-play; older devices fall back to AC3 5.1 transcode.",
                _all_devices("ok"),
            ))
        elif codec in {"flac", "alac"}:
            issues.append(_issue(
                f"audio_{codec}", "always_transcode", "audio",
                f"{tag}: {codec.upper()} lossless — transcodes for streaming",
                f"Plex transcodes {codec.upper()} to AAC or MP3 for most clients to save bandwidth.",
                _all_devices("transcode"),
            ))
        elif codec.startswith("pcm_"):
            issues.append(_issue(
                "audio_pcm", "always_transcode", "audio",
                f"{tag}: Uncompressed PCM ({codec})",
                "PCM is uncompressed. Plex always transcodes to AAC/AC3 to save bandwidth.",
                _all_devices("transcode"),
            ))
        elif codec in {"wmav1","wmav2","wmapro","wma"}:
            issues.append(_issue(
                "audio_wma", "possible_transcode", "audio",
                f"{tag}: WMA — limited Plex support",
                "Windows Media Audio is largely unsupported on Plex clients.",
                _all_devices("transcode"),
            ))
        elif codec == "vorbis":
            issues.append(_issue(
                "audio_vorbis", "possible_transcode", "audio",
                f"{tag}: Vorbis — usually transcodes",
                "Vorbis is direct-played by Android/Chromecast but transcodes elsewhere.",
                _all_devices("transcode"),
            ))
        elif codec == "opus":
            issues.append(_issue(
                "audio_opus", "possible_transcode", "audio",
                f"{tag}: Opus — limited direct-play",
                "Opus has good quality but Plex clients support it inconsistently.",
                _all_devices("transcode"),
            ))

        if ch and ch > 8:
            issues.append(_issue(
                "audio_too_many_channels", "always_transcode", "audio",
                f"{tag}: {ch} channels — Plex downmixes anything > 7.1",
                "Plex caps audio at 7.1 (8 channels). More channels are downmixed during transcode.",
                _all_devices("transcode"),
            ))

    return issues


def _check_subtitles(sub_streams):
    issues = []
    IMAGE = {
        "dvd_subtitle": ("VobSub (DVD)", "Image-based DVD subtitles. Plex must burn them into the video, which forces a full video transcode every time someone enables subtitles. Massive CPU hit."),
        "dvb_subtitle": ("DVB image subs", "Image-based broadcast subtitles. Same burn-in penalty as VobSub."),
        "hdmv_pgs_subtitle": ("PGS (Blu-ray)", "PGS subtitles are pre-rendered images. Apple TV, NVIDIA Shield, and Plex HTPC render natively; web/mobile/Roku force a video transcode to burn them in."),
        "dvb_teletext": ("DVB Teletext", "Teletext subtitles are mostly unsupported in Plex; they may be silently dropped."),
        "xsub": ("XSUB", "DivX image subtitles — always burned-in, forcing video transcode."),
    }
    TEXT_HEAVY = {
        "ass": ("ASS/SSA with styling", "ASS allows complex per-line styling, fades, and karaoke. Apple TV, NVIDIA Shield, and Plex HTPC render them; Roku and web fall back to burn-in (video transcode) on complex styles."),
        "ssa": ("SSA", "Same as ASS — may force burn-in on simple clients."),
    }
    for idx, s in enumerate(sub_streams):
        codec = (s.get("codec_name") or "").lower()
        lang = (s.get("tags",{}) or {}).get("language","")
        lang_str = f" [{lang}]" if lang else ""
        if codec in IMAGE:
            name, desc = IMAGE[codec]
            issues.append(_issue(
                f"sub_image_{codec}", "possible_transcode", "subtitles",
                f"Subtitle track {idx+1}{lang_str}: {name} — burn-in transcode required",
                desc,
                _aff(
                    {"Apple TV 4K":"ok"},{"Apple TV HD":"ok"},
                    {"iOS / iPadOS":"transcode"},{"Android (mobile)":"transcode"},{"Android TV":"ok"},
                    {"Chromecast w/ Google TV":"transcode"},{"Chromecast (3rd gen)":"transcode"},
                    {"Roku Ultra":"transcode"},{"Roku Streaming Stick":"transcode"},
                    {"Fire TV (4K Max / Cube)":"transcode"},{"Fire TV Stick":"transcode"},
                    {"Smart TVs (Samsung/LG)":"transcode"},{"PlayStation 5":"transcode"},
                    {"Xbox Series X/S":"transcode"},{"Web browser (Chrome/Edge)":"transcode"},
                    {"Web browser (Safari)":"transcode"},{"Plex HTPC / Desktop":"ok"},
                ),
            ))
        elif codec in TEXT_HEAVY:
            name, desc = TEXT_HEAVY[codec]
            issues.append(_issue(
                f"sub_text_{codec}", "info", "subtitles",
                f"Subtitle track {idx+1}{lang_str}: {name} — may force burn-in",
                desc,
                _all_devices("transcode"),
            ))
    return issues


def _check_resolution_fps(vs):
    issues = []
    width = vs.get("width") or 0
    height = vs.get("height") or 0
    try:
        n, d = (vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "0/1").split("/")
        fps = float(n) / float(d) if float(d) else 0
    except Exception: fps = 0

    if width >= 7680:
        issues.append(_issue(
            "resolution_8k", "unplayable", "video_resolution",
            f"8K resolution ({width}x{height}) — Plex can't transcode 8K",
            "Plex Media Server cannot transcode 8K content. Direct play only, which means clients without 8K decode capability simply fail.",
            _aff(*[{d:"fail"} for d in DEVICES if d != "Plex HTPC / Desktop"], {"Plex HTPC / Desktop":"ok"}),
        ))

    if fps > 60:
        issues.append(_issue(
            "framerate_high", "possible_transcode", "video_framerate",
            f"High frame rate ({fps:.1f} fps)",
            "Above 60 fps, hardware decode support thins out fast. Most TVs handle up to 60fps; high-fps gaming/sports content forces a transcode for the majority of Plex clients.",
            _all_devices("transcode"),
        ))
    return issues


def _check_bitrate(fmt, threshold):
    issues = []
    bitrate = int(fmt.get("bit_rate") or 0)
    if bitrate > threshold:
        mbps = bitrate // 1_000_000
        threshold_mbps = threshold // 1_000_000
        issues.append(_issue(
            "bitrate_high", "high_bitrate", "container",
            f"High overall bitrate ({mbps} Mbps, threshold {threshold_mbps} Mbps)",
            f"Total bitrate is {mbps} Mbps. Most home Wi-Fi struggles above 80 Mbps; remote streaming is even more bandwidth-bound. Direct-play locally on wired clients is fine, but expect buffering on Wi-Fi or remote viewing. Plex remote streaming caps usually start around 80–100 Mbps.",
            _aff(
                {"Apple TV 4K":"ok"},{"Apple TV HD":"fail"},
                {"iOS / iPadOS":"transcode"},{"Android (mobile)":"transcode"},{"Android TV":"ok"},
                {"Chromecast w/ Google TV":"ok"},{"Chromecast (3rd gen)":"fail"},
                {"Roku Ultra":"ok"},{"Roku Streaming Stick":"fail"},
                {"Fire TV (4K Max / Cube)":"ok"},{"Fire TV Stick":"fail"},
                {"Smart TVs (Samsung/LG)":"ok"},{"PlayStation 5":"ok"},{"Xbox Series X/S":"ok"},
                {"Web browser (Chrome/Edge)":"transcode"},{"Web browser (Safari)":"transcode"},
                {"Plex HTPC / Desktop":"ok"},
            ),
        ))
    return issues


# ─── Custom rule evaluation ───────────────────────────────────────────────────

def _value_from_file(file_record, field):
    """Fetch a value from a file record matching the rule schema."""
    probe = file_record.get("probe") or {}
    fmt = (probe.get("format") or {}) if isinstance(probe, dict) else {}
    streams = (probe.get("streams") or []) if isinstance(probe, dict) else []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})

    if field == "extension":   return (file_record.get("extension") or "").lower() or None
    if field == "category":    return file_record.get("category")
    if field == "scan_status": return file_record.get("scan_status")
    if field == "name":        return (file_record.get("name") or "").lower()
    if field == "path":        return (file_record.get("path") or "").lower()
    if field == "size_bytes":  return file_record.get("size_bytes") or 0
    if field == "size_mb":     return (file_record.get("size_bytes") or 0) / 1048576.0
    if field == "size_gb":     return (file_record.get("size_bytes") or 0) / 1073741824.0
    if field == "monitored":   return file_record.get("monitored")
    if field == "arr_kind":    return file_record.get("arr_kind")

    # Probe-derived (look at the file_record cached column first, then probe blob)
    if field == "codec":
        return (file_record.get("codec") or video.get("codec_name") or "").lower() or None
    if field == "audio_codec":
        return (file_record.get("audio_codec") or audio.get("codec_name") or "").lower() or None
    if field == "container":
        return (file_record.get("container") or (fmt.get("format_name") or "").split(",")[0] or "").lower() or None
    if field == "resolution":
        if file_record.get("resolution"): return file_record.get("resolution")
        w, h = video.get("width") or 0, video.get("height") or 0
        return f"{w}x{h}" if (w and h) else None
    if field == "dovi_profile":
        return file_record.get("dovi_profile")
    if field == "bitrate":
        if file_record.get("bitrate"): return file_record.get("bitrate")
        try: return int(fmt.get("bit_rate") or 0) or None
        except Exception: return None
    if field == "bitrate_mbps":
        b = file_record.get("bitrate") or fmt.get("bit_rate") or 0
        try: return float(b) / 1_000_000
        except Exception: return None
    if field == "duration_sec":
        return file_record.get("duration_sec") or float(fmt.get("duration") or 0) or None
    return None


def _condition_matches(file_record, cond):
    field = cond.get("field")
    op = cond.get("op", "eq")
    target = cond.get("value")
    actual = _value_from_file(file_record, field)

    if op == "is_null":  return actual is None
    if op == "not_null": return actual is not None
    if actual is None:   return False  # other ops can't match null

    try:
        if op == "eq":  return str(actual).lower() == str(target).lower() if isinstance(actual, str) else actual == target
        if op == "neq": return str(actual).lower() != str(target).lower() if isinstance(actual, str) else actual != target
        if op == "gt":  return float(actual) > float(target)
        if op == "gte": return float(actual) >= float(target)
        if op == "lt":  return float(actual) < float(target)
        if op == "lte": return float(actual) <= float(target)
        if op == "contains":
            return str(target).lower() in str(actual).lower()
        if op == "starts_with":
            return str(actual).lower().startswith(str(target).lower())
        if op == "ends_with":
            return str(actual).lower().endswith(str(target).lower())
        if op == "in":
            if not isinstance(target, list): return False
            return any(str(actual).lower() == str(v).lower() for v in target)
    except (TypeError, ValueError):
        return False
    return False


def evaluate_custom_rule(file_record, rule):
    """Returns a single issue dict if the file matches the rule, else None."""
    spec = rule.get("spec") or {}
    conditions = spec.get("conditions") or []
    match = (spec.get("match") or "all").lower()
    if not conditions: return None

    if match == "all":
        if not all(_condition_matches(file_record, c) for c in conditions):
            return None
    else:  # any
        if not any(_condition_matches(file_record, c) for c in conditions):
            return None

    affected = rule.get("affected_devices_list") or rule.get("affected") or []
    issue = {
        "rule_key": f"custom_{rule['id']}",
        "severity": rule.get("severity", "info"),
        "category": rule.get("category", "custom"),
        "message":  rule.get("message", rule.get("name", "Custom rule")),
        "detail":   rule.get("detail", "Custom rule matched."),
        "affected": list(affected),
    }
    _enrich_with_jellyfin(issue)
    return issue
