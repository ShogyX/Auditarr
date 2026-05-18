/**
 * v1.9 Stage 3.4 — Container label normalization.
 *
 * JS counterpart of ``backend/app/utils/container_label.py``; the
 * two MUST stay in sync. Maps raw ffprobe ``format_name`` values
 * to friendly operator-facing labels.
 *
 *   matroska              → MKV
 *   matroska,webm         → MKV
 *   webm                  → WEBM   (distinct from MKV)
 *   mov                   → MP4
 *   mov,mp4,m4a,3gp,3g2,mj2 → MP4
 *   mp4 / m4a / m4v       → MP4
 *   mpegts / ts           → TS
 *   avi                   → AVI
 *   flv / f4v             → FLV
 *   ogg / ogv             → OGG
 *   …                     → upper(input)   (graceful fallback)
 *
 * Used by Categories, the Files table, and FileDetailDrawer.
 */

const CONTAINER_MAP: Readonly<Record<string, string>> = Object.freeze({
  // Matroska family
  matroska: "MKV",
  "matroska,webm": "MKV",
  mkv: "MKV",
  // WebM gets its own label even though it shares the matroska
  // demuxer — operators expect "WEBM" for .webm files.
  webm: "WEBM",
  // MP4 / QuickTime family
  mov: "MP4",
  mp4: "MP4",
  m4a: "MP4",
  m4v: "MP4",
  "mov,mp4,m4a,3gp,3g2,mj2": "MP4",
  // MPEG transport / program streams
  mpegts: "TS",
  ts: "TS",
  mpeg: "MPEG",
  mpegps: "MPEG",
  // Misc
  avi: "AVI",
  flv: "FLV",
  f4v: "FLV",
  ogg: "OGG",
  ogv: "OGG",
  wav: "WAV",
  wave: "WAV",
  flac: "FLAC",
  aac: "AAC",
  asf: "ASF",
  wma: "WMA",
  wmv: "WMV",
});

/** Return the friendly label for a raw ffprobe container value.
 *  ``null`` / empty → ``null`` so the caller picks the unknown
 *  rendering. Unknown non-empty input is upper-cased and returned
 *  as a safe fallback. */
export function containerLabel(raw: string | null | undefined): string | null {
  if (raw === null || raw === undefined) return null;
  const s = raw.trim().toLowerCase();
  if (!s) return null;
  const direct = CONTAINER_MAP[s];
  if (direct !== undefined) return direct;
  // First-token fallback — handles a caller passing the un-split
  // format_name when it isn't in the table verbatim.
  const firstParts = s.split(",", 1);
  const first = firstParts[0]?.trim() ?? "";
  if (first) {
    const mapped = CONTAINER_MAP[first];
    if (mapped !== undefined) return mapped;
  }
  return raw.trim().toUpperCase();
}
