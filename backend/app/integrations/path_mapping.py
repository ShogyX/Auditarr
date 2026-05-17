"""Path remapping for integrations.

Plex, Jellyfin, Sonarr, etc. all report file paths relative to *their*
view of the filesystem, which is almost never identical to Auditarr's
view. Plex on bare-metal might see ``/data/movies/Dune.mkv`` while
Auditarr runs in a container and indexes the same file as
``/mnt/media/Movies/Dune.mkv``.

Each integration carries an optional ``path_mappings`` list in its
``config.options``::

    [
      {"from": "/data/movies",   "to": "/mnt/media/Movies"},
      {"from": "/data/tv",       "to": "/mnt/media/TV"},
      {"from": "/data/anime",    "to": "/mnt/media/Anime"}
    ]

The :func:`remap_path` helper applies the longest-matching ``from``
prefix and rewrites it to ``to``. If no mapping matches the path is
returned unchanged, which is the "assume 1:1" default the operator
gets when they haven't configured anything.

:class:`DriftDetector` aggregates resolution outcomes across a batch
and flags an integration as "drift suspected" when more than half of
incoming paths fail to resolve to a known media file. The cron job
attaches the verdict to the integration's health status so the UI can
prompt the operator to configure mappings.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class PathMapping:
    """A single ``from → to`` prefix rewrite."""

    src_prefix: str  # path as seen by the integration
    dst_prefix: str  # path as seen by Auditarr (matches MediaFile.path)


def parse_mappings(raw: object) -> list[PathMapping]:
    """Coerce the JSON shape in ``config.options['path_mappings']``
    into typed mappings, dropping malformed entries silently. Operators
    edit this in the UI as a list of `{from, to}` objects; we accept
    both ``{"from", "to"}`` and ``{"src_prefix", "dst_prefix"}`` for
    forward compatibility with a future typed schema."""
    if not isinstance(raw, list):
        return []
    out: list[PathMapping] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        src = entry.get("from") or entry.get("src_prefix")
        dst = entry.get("to") or entry.get("dst_prefix")
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        src = src.rstrip("/")
        dst = dst.rstrip("/")
        if not src or not dst:
            continue
        out.append(PathMapping(src_prefix=src, dst_prefix=dst))
    # Longest src_prefix wins so the most specific mapping is applied.
    # Sort once, callers iterate.
    out.sort(key=lambda m: len(m.src_prefix), reverse=True)
    return out


def remap_path(path: str, mappings: list[PathMapping]) -> str:
    """Apply the longest matching ``src_prefix`` and return the
    rewritten path. If no mapping matches, the path is returned
    unchanged (the "assume 1:1" behavior).

    The match is prefix-based and respects directory boundaries —
    a mapping of ``/data/tv`` does **not** match ``/data/tvshows``.
    """
    for m in mappings:
        if path == m.src_prefix or path.startswith(m.src_prefix + "/"):
            suffix = path[len(m.src_prefix):]
            return m.dst_prefix + suffix
    return path


def remap_path_inverse(path: str, mappings: list[PathMapping]) -> str:
    """Inverse of :func:`remap_path`.

    Stage 08 (v1.7) — converts an Auditarr-side path (matching
    ``MediaFile.path``) back to the integration-side path (what
    Plex / Jellyfin / Tdarr would report on the Part / file
    record). Used by ``PlexProvider._resolve_rating_key_from_path``
    so transcode hand-offs can find the right Plex item without
    an operator pre-supplying the ratingKey.

    Picks the longest matching ``dst_prefix`` (the Auditarr side)
    so the most specific mapping wins. Falls through to the
    unchanged path when no mapping matches (the "assume 1:1
    layout" behaviour mirrors :func:`remap_path`).

    The match respects directory boundaries — a mapping of
    ``/data/tv`` does **not** match ``/data/tvshows`` (same
    semantics as :func:`remap_path`).
    """
    # Caller-supplied mappings may already be sorted longest-src-
    # first; we re-sort by dst_prefix for the inverse direction.
    candidates = sorted(mappings, key=lambda m: len(m.dst_prefix), reverse=True)
    for m in candidates:
        if path == m.dst_prefix or path.startswith(m.dst_prefix + "/"):
            suffix = path[len(m.dst_prefix):]
            return m.src_prefix + suffix
    return path


def remap_path_chain(
    path: str,
    integration_mappings: list[PathMapping],
    global_mappings: list[PathMapping],
) -> str:
    """Apply per-integration mappings first, then global ones.

    Stage 5 (audit follow-up): operators reported wanting to define
    a path mapping once and have it apply across every integration.
    The chain runs the integration list first because those are the
    most specific (they typically compensate for a particular
    integration's view of paths). Global mappings run second as a
    catch-all — useful for operators with multiple integrations
    pointing at the same underlying storage layout.

    Either list can be empty; if both are empty the path is
    returned unchanged.
    """
    out = remap_path(path, integration_mappings)
    return remap_path(out, global_mappings)


@dataclass(slots=True)
class DriftReport:
    """Outcome of resolving a batch of integration paths to MediaFile rows.

    The ``poll_playback`` cron uses this to decide whether to attach a
    "path drift" badge to the integration's health. The UI surfaces
    this so operators know they need to configure mappings rather than
    getting silently empty suggestions.
    """

    seen: int = 0  # total paths examined
    resolved: int = 0  # paths that matched a MediaFile row
    unresolved_sample: list[str] = field(default_factory=list)
    has_mappings_configured: bool = False

    @property
    def resolution_rate(self) -> float:
        if self.seen == 0:
            return 1.0
        return self.resolved / self.seen

    @property
    def drift_suspected(self) -> bool:
        """We flag drift when the resolution rate is poor AND the
        operator hasn't tried configuring mappings yet (no mappings
        configured), OR when even configured mappings still leave the
        bulk unresolved.

        The threshold is intentionally lenient — playback events for
        files Auditarr genuinely hasn't indexed (deleted, in libraries
        the user hasn't added, watched-elsewhere content) are normal
        noise. We only complain when the majority go unresolved.
        """
        if self.seen < 5:
            return False  # too few samples to draw a conclusion
        return self.resolution_rate < 0.5

    def detail(self) -> str:
        """Human-readable health-detail string for the integration."""
        if not self.drift_suspected:
            return ""
        pct = (1.0 - self.resolution_rate) * 100
        unresolved = self.seen - self.resolved
        if not self.has_mappings_configured:
            return (
                f"{unresolved} of {self.seen} playback paths "
                f"don't resolve to indexed files ({pct:.0f}%). "
                "Configure path mappings on this integration."
            )
        return (
            f"{unresolved} of {self.seen} playback paths "
            f"don't resolve even with configured mappings ({pct:.0f}%). "
            "Check that mappings cover every library prefix."
        )


# ── Common config-schema fragment ──────────────────────────────
# Providers paste this into their ``config_schema`` so the field
# shows up consistently across Plex/Jellyfin/Sonarr/etc. The frontend
# renders the array-of-objects shape as a small editable table.
PATH_MAPPINGS_SCHEMA_FRAGMENT: dict[str, object] = {
    "type": "array",
    "title": "Path mappings",
    "description": (
        "Rewrite paths reported by the integration to match how "
        "Auditarr indexes them. If empty, paths are used 1:1. "
        "Example: from=/data/movies, to=/mnt/media/Movies."
    ),
    "default": [],
    "items": {
        "type": "object",
        "required": ["from", "to"],
        "properties": {
            "from": {"type": "string", "title": "From (integration view)"},
            "to": {"type": "string", "title": "To (Auditarr view)"},
        },
    },
}
