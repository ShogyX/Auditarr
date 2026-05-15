"""File classifier.

Maps a file's extension (and, when available, its ffprobe streams) to one
of the canonical categories the rest of the system reasons about:

* ``media`` — primary video/audio container
* ``subtitle`` — SRT/SSA/VTT/PGS/etc.
* ``image`` — artwork, posters, thumbnails
* ``metadata`` — NFO, JSON sidecars, library metadata
* ``junk`` — Apple Double files, Thumbs.db, .DS_Store, partial downloads
* ``unknown`` — anything else (preserved but ignored by most rules)

Classification by extension is fast and usually right; ffprobe-driven
refinement only kicks in when the extension is ambiguous or the file
deserves stream-level inspection.
"""

from __future__ import annotations

from dataclasses import dataclass

# Extension sets — stored without leading dot, lowercased.
VIDEO_EXTS = frozenset(
    {
        "mkv", "mp4", "m4v", "mov", "avi", "wmv", "ts", "m2ts", "mts",
        "vob", "iso", "webm", "ogv", "flv", "3gp", "rm", "rmvb", "mpg",
        "mpeg", "mxf", "asf", "divx", "f4v", "qt",
    }
)
AUDIO_EXTS = frozenset(
    {
        "mp3", "flac", "wav", "aac", "ogg", "oga", "opus", "m4a", "m4b",
        "wma", "ape", "alac", "aiff", "aif", "dsd", "dsf", "wv",
    }
)
SUBTITLE_EXTS = frozenset(
    {"srt", "ssa", "ass", "vtt", "sub", "idx", "sup", "smi", "stl"}
)
IMAGE_EXTS = frozenset(
    {"jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff", "heic", "avif"}
)
METADATA_EXTS = frozenset({"nfo", "xml", "json", "yml", "yaml"})
JUNK_EXTS = frozenset(
    {"db", "ini", "tmp", "part", "!ut", "crdownload", "ds_store", "lnk"}
)
JUNK_FILENAMES = frozenset(
    {".ds_store", "thumbs.db", "desktop.ini", ".directory"}
)
JUNK_PREFIXES = ("._",)  # Apple Double resource forks


@dataclass(slots=True)
class ClassifyResult:
    category: str  # media | subtitle | image | metadata | junk | unknown
    is_video: bool = False
    is_audio: bool = False


def classify(filename: str, *, has_video_stream: bool | None = None) -> ClassifyResult:
    """Classify a file by name. Pass ``has_video_stream=True`` for an ffprobe-confirmed video.

    The function never raises — anything unfamiliar lands in ``unknown``.
    """
    name = filename.strip()
    name_lc = name.lower()

    # Junk by full name or prefix.
    if name_lc in JUNK_FILENAMES:
        return ClassifyResult(category="junk")
    for prefix in JUNK_PREFIXES:
        if name_lc.startswith(prefix):
            return ClassifyResult(category="junk")

    ext = _ext(name_lc)
    if ext in JUNK_EXTS:
        return ClassifyResult(category="junk")
    if ext in SUBTITLE_EXTS:
        return ClassifyResult(category="subtitle")
    if ext in IMAGE_EXTS:
        return ClassifyResult(category="image")
    if ext in METADATA_EXTS:
        return ClassifyResult(category="metadata")
    if ext in VIDEO_EXTS:
        return ClassifyResult(category="media", is_video=True)
    if ext in AUDIO_EXTS:
        return ClassifyResult(category="media", is_audio=True)

    # Fallback: trust ffprobe if it told us about streams.
    if has_video_stream is True:
        return ClassifyResult(category="media", is_video=True)

    return ClassifyResult(category="unknown")


def should_probe(filename: str) -> bool:
    """Return True if it's worth running ffprobe on this file.

    We only probe ``media`` candidates — running ffprobe on every JPEG or
    NFO file would burn IO on every scan for no gain.
    """
    return classify(filename).category == "media"


def _ext(name_lower: str) -> str:
    if "." not in name_lower:
        return ""
    return name_lower.rsplit(".", 1)[1]
