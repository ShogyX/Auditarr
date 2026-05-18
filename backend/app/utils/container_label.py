"""Container label normalization (v1.9 Stage 3.4).

ffprobe reports the container as ``format_name``, which is a
comma-separated list of every demuxer that can read the file. The
scanner already keeps only the FIRST entry
(see ``ffprobe._parse_payload``), but that first entry is still a
demuxer name — ``matroska``, ``mov,mp4,m4a,3gp,3g2,mj2`` (after the
scanner's split, ``mov``), ``mpegts``, etc. None of those are the
labels an operator wants to read in the UI.

This util maps the raw demuxer string to a friendly label:

  * ``matroska``  → ``MKV``
  * ``matroska,webm`` → ``MKV`` (we keep .webm distinct on its own
    via the extension fallback so a WebM file shows WEBM, not MKV)
  * ``mov``       → ``MP4``    (the scanner already trimmed
    ``mov,mp4,m4a,...`` down to ``mov`` — both that and the raw
    comma-list resolve to ``MP4``)
  * ``mp4``       → ``MP4``
  * ``mpegts``    → ``TS``
  * ``avi``       → ``AVI``
  * ``flv``       → ``FLV``
  * ``ogg``       → ``OGG``
  * ``wav``       → ``WAV``
  * ``webm``      → ``WEBM``
  * unknown       → upper-cased input, or ``None`` if input was
    None / blank.

There is a JS counterpart at ``frontend/src/lib/containerLabel.ts``;
the two MUST stay in sync. The mapping is intentionally
case-insensitive on input and always returns upper-case on output.
"""

from __future__ import annotations

# Source of truth. Keys are lower-cased; the lookup folds case
# before matching so callers don't have to.
#
# When the scanner stores the FULL raw format-name (the comma
# string), we map the whole thing too, so callers passing either
# ``matroska`` (post-split) or ``matroska,webm`` (raw) both land on
# ``MKV``. The MP4 family is the noisiest one — every common
# Apple-derived container shares the same demuxer alias list.
_CONTAINER_MAP: dict[str, str] = {
    # Matroska family
    "matroska": "MKV",
    "matroska,webm": "MKV",
    "mkv": "MKV",
    # WebM is its own label even though it shares the matroska
    # demuxer — operators expect "WEBM" for .webm files.
    "webm": "WEBM",
    # MP4 / QuickTime family
    "mov": "MP4",
    "mp4": "MP4",
    "m4a": "MP4",
    "m4v": "MP4",
    "mov,mp4,m4a,3gp,3g2,mj2": "MP4",
    # MPEG transport / program streams
    "mpegts": "TS",
    "ts": "TS",
    "mpeg": "MPEG",
    "mpegps": "MPEG",
    # Misc
    "avi": "AVI",
    "flv": "FLV",
    "f4v": "FLV",
    "ogg": "OGG",
    "ogv": "OGG",
    "wav": "WAV",
    "wave": "WAV",
    "flac": "FLAC",
    "aac": "AAC",
    "asf": "ASF",
    "wma": "WMA",
    "wmv": "WMV",
}


def container_label(raw: str | None) -> str | None:
    """Return the friendly container label for a raw ffprobe value.

    ``None`` / empty input → ``None`` (so callers can render
    "unknown" however they like). Unknown non-empty input is
    upper-cased and returned as-is — so a future ffprobe version
    surfacing a new container we haven't catalogued still gives the
    operator something sensible to read.
    """
    if raw is None:
        return None
    s = raw.strip().lower()
    if not s:
        return None
    if s in _CONTAINER_MAP:
        return _CONTAINER_MAP[s]
    # First-token fallback — handles the ``mov,mp4,m4a,...`` case
    # if a caller passed the un-split format_name AND it's not in
    # the table verbatim.
    first = s.split(",", 1)[0].strip()
    if first and first in _CONTAINER_MAP:
        return _CONTAINER_MAP[first]
    return raw.strip().upper()


__all__ = ["container_label"]
