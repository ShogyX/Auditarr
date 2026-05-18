"""Tdarr integration plugin.

Tdarr's Node/Server API lives at ``/api/v2/`` and is the same one the
official Tdarr UI uses. It does not require an API key by default; later
builds added optional bearer auth which we honor via the ``token`` secret
field (omit it on installs that don't require it).

What ships in this version:
* ``healthcheck`` — ``GET /api/v2/status`` returns the Tdarr build and a
  list of attached nodes. ``status="ok"`` requires the response to parse;
  if at least one node is offline we downgrade to ``degraded``.
* ``discover_libraries`` — ``GET /api/v2/cruddb`` with ``collection=LibrarySettingsJSONDB``
  enumerates configured Tdarr libraries (the on-disk roots Tdarr watches).
* ``sync_tags`` — Tdarr's file index is large and stream-oriented; mirroring
  per-file status into tags is deferred. Returns ``[]`` for now.

Stage 08 (v1.7) added the third-party transcode hand-off surface:
* ``submit_transcode_job`` — POST ``/api/v2/cruddb`` against
  ``FileJSONDB`` with ``mode=insert`` to add a file to Tdarr's
  queue, referencing a Tdarr plugin id the operator picked. The
  upstream job id is the returned document's ``_id``.
* ``list_transcode_profiles`` — POST ``/api/v2/cruddb`` against
  ``PluginsJSONDB`` to enumerate available Tdarr plugins; the
  Auditarr profile editor renders them in a picker.
* ``get_transcode_job_status`` — POST ``/api/v2/cruddb`` to look
  up the file's current ``transcodeStage`` field. Tdarr's state
  vocabulary maps to Auditarr's enum via ``_TDARR_STATE_TO_AUDITARR``.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.http import async_client

from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    JobSubmitResult,
    TagSync,
    TranscodeJobSpec,
    TranscodeJobStatus,
    TranscodeProfileSummary,
)
from app.plugins import Plugin, PluginContext


# Stage 08 (v1.7) — Tdarr's per-file ``transcodeStage`` values
# mapped to Auditarr's job-status enum. Tdarr documents these in
# its source; the most common ones are:
#
#   * ``""`` / missing  — new file, not yet picked up.
#   * ``"Currently processing"`` — running.
#   * ``"Transcode success"`` — completed.
#   * ``"Transcode error"`` — failed.
#
# Anything not in the map flows through as ``"unknown"`` so the
# worker keeps polling rather than guessing.
_TDARR_STATE_TO_AUDITARR: dict[str, str] = {
    "": "pending",
    "queued": "pending",
    "currently processing": "running",
    "transcode success": "completed",
    "transcode error": "failed",
}


class TdarrProvider(IntegrationProvider):
    kind = "tdarr"
    label = "Tdarr"
    config_schema: dict[str, Any] = {
        "type": "object",
        "required": ["base_url"],
        "properties": {
            "base_url": {
                "type": "string",
                "title": "Server URL",
                "description": "e.g. http://tdarr.local:8265",
            },
            "verify_ssl": {"type": "boolean", "title": "Verify TLS", "default": True},
            "timeout_seconds": {
                "type": "integer",
                "title": "Timeout (s)",
                "default": 20,
                "minimum": 1,
                "maximum": 120,
            },
        },
    }
    # Token is optional — empty string is allowed for builds without auth.
    # We declare it so the operator can supply one when needed, but the
    # manager treats empty/missing as "no auth header".
    secret_fields: tuple[str, ...] = ()

    def __init__(self, log: Any) -> None:
        self._log = log

    def _client(self, config: IntegrationConfig) -> httpx.AsyncClient:
        base_url = str(config.options.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Tdarr integration is missing 'base_url'")
        headers = {"Accept": "application/json"}
        token = str(config.secrets.get("token", "")).strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return async_client(
            base_url=base_url,
            timeout=float(config.options.get("timeout_seconds", 20)),
            verify=bool(config.options.get("verify_ssl", True)),
            headers=headers,
        )

    # ── IntegrationProvider ──────────────────────────────────────
    async def healthcheck(self, config: IntegrationConfig) -> HealthReport:
        try:
            async with self._client(config) as client:
                response = await client.get("/api/v2/status")
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            return HealthReport(status="error", detail=f"HTTP error: {exc}")
        except ValueError as exc:
            return HealthReport(status="error", detail=str(exc))

        # Tdarr returns either a top-level dict or a list of nodes. Normalize.
        nodes: list[dict[str, Any]] = []
        if isinstance(payload, list):
            nodes = payload
        elif isinstance(payload, dict):
            nodes = payload.get("nodes") or [payload]

        offline = [n for n in nodes if isinstance(n, dict) and n.get("status") == "offline"]
        version = next(
            (n.get("version") for n in nodes if isinstance(n, dict) and n.get("version")),
            None,
        )
        status = "ok" if not offline else "degraded"
        detail = (
            f"{len(nodes) - len(offline)} of {len(nodes)} node(s) online"
            if nodes
            else "Tdarr responded"
        )
        return HealthReport(
            status=status,
            detail=detail,
            metadata={"version": version, "nodes": len(nodes)},
        )

    async def discover_libraries(
        self, config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        async with self._client(config) as client:
            # Tdarr's CRUD endpoint pages collections.
            response = await client.post(
                "/api/v2/cruddb",
                json={
                    "data": {
                        "collection": "LibrarySettingsJSONDB",
                        "mode": "getAll",
                    }
                },
            )
            response.raise_for_status()
            payload = response.json() or []

        out: list[DiscoveredLibrary] = []
        for lib in payload if isinstance(payload, list) else []:
            if not isinstance(lib, dict):
                continue
            root = lib.get("folder")
            if not root:
                continue
            # Tdarr libraries don't carry a kind; treat them as mixed and let
            # the operator pick what to do on promote.
            out.append(
                DiscoveredLibrary(
                    upstream_id=str(lib.get("_id") or lib.get("id") or ""),
                    name=str(lib.get("name") or root.rstrip("/").rsplit("/", 1)[-1]),
                    kind="mixed",
                    root_path=str(root),
                    metadata={
                        "scan_found_count": lib.get("scanFoundCount"),
                        "transcode_queue": lib.get("transcodeQueue"),
                    },
                )
            )
        return out

    async def sync_tags(self, _config: IntegrationConfig) -> list[TagSync]:
        return []

    # ── Stage 08 (v1.7) — transcode hand-off ─────────────────────
    async def submit_transcode_job(
        self,
        config: IntegrationConfig,
        job_spec: TranscodeJobSpec,
    ) -> JobSubmitResult:
        """Queue a file in Tdarr referencing a Tdarr-side plugin.

        Plan §437. The job spec's ``metadata.provider_profile_id``
        carries the Tdarr plugin id the operator picked in the
        Auditarr profile editor (e.g.
        ``"Tdarr_Plugin_henk_h265"``). When missing, we fail with
        a clear error pointing the operator to pick one — we don't
        guess a plugin because Tdarr's plugin set is operator-
        specific.

        The Tdarr endpoint is ``POST /api/v2/cruddb`` against
        ``FileJSONDB`` with ``mode=insert`` — the same write the
        Tdarr UI uses when an operator drags a file into a flow.
        """
        provider_profile_id = job_spec.metadata.get("provider_profile_id")
        if not provider_profile_id or not isinstance(provider_profile_id, str):
            return JobSubmitResult(
                status="rejected",
                detail=(
                    "Tdarr requires a provider profile id (the Tdarr "
                    "plugin name). Edit the Auditarr profile and pick "
                    "one from the plugin list."
                ),
            )

        try:
            async with self._client(config) as client:
                response = await client.post(
                    "/api/v2/cruddb",
                    json={
                        "data": {
                            "collection": "FileJSONDB",
                            "mode": "insert",
                            "docs": [
                                {
                                    # Tdarr's file row carries the
                                    # absolute path on the node's view
                                    # of the filesystem.
                                    "file": job_spec.input_path,
                                    # Reference the operator-picked
                                    # plugin / flow.
                                    "DB": "FileJSONDB",
                                    "transcodeChosenPlugin":
                                        provider_profile_id,
                                    # Auditarr correlation metadata —
                                    # Tdarr ignores unknown keys.
                                    "auditarr_item_id": job_spec.item_id,
                                    "auditarr_transcode_scope":
                                        job_spec.transcode_scope,
                                }
                            ],
                        }
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            return JobSubmitResult(
                status="error",
                detail=f"Tdarr HTTP error: {exc!s}",
            )
        except ValueError as exc:
            return JobSubmitResult(status="error", detail=str(exc))

        # Tdarr returns the inserted document(s). We accept either
        # ``[doc]`` or ``{docs:[doc]}`` shapes defensively — both
        # have been observed across versions.
        doc: dict[str, Any] | None = None
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                doc = first
        elif isinstance(payload, dict):
            if isinstance(payload.get("docs"), list) and payload["docs"]:
                first = payload["docs"][0]
                if isinstance(first, dict):
                    doc = first
            else:
                doc = payload

        upstream_id = (
            str(doc.get("_id") or doc.get("id") or "")
            if doc is not None
            else ""
        )
        if not upstream_id:
            return JobSubmitResult(
                status="error",
                detail=(
                    "Tdarr accepted the request but did not return a "
                    "job id; cannot correlate completion. Body: "
                    f"{payload!r}"
                ),
            )
        return JobSubmitResult(
            status="accepted",
            upstream_job_id=upstream_id,
            detail=f"queued in Tdarr as {upstream_id}",
        )

    async def list_transcode_profiles(
        self, config: IntegrationConfig
    ) -> list[TranscodeProfileSummary]:
        """Enumerate Tdarr's available transcode plugins.

        Plan §438. Each plugin is one of:
          * a built-in Tdarr plugin (e.g. ``Tdarr_Plugin_MC93_Migz1``).
          * a community plugin (Tdarr ships a community pack).
          * a custom plugin the operator wrote.

        We treat all three the same: each is a row in
        ``PluginsJSONDB`` with an ``id`` + a ``name`` + a
        description. The Auditarr profile editor renders the list
        and stores the chosen id in the profile's settings.
        """
        try:
            async with self._client(config) as client:
                # v1.9 audit fix (OP-13) — try plugins first
                # (legacy Tdarr), then flows (Tdarr v2 "flows"
                # surface). Both use the same cruddb endpoint
                # with different collection names. Some Tdarr
                # builds also wrap payloads in {"data": [...]};
                # the parser below tolerates both list and
                # wrapped-list responses.
                discovered: list[dict[str, Any]] = []
                for collection in ("PluginsJSONDB", "FlowsJSONDB"):
                    try:
                        response = await client.post(
                            "/api/v2/cruddb",
                            json={
                                "data": {
                                    "collection": collection,
                                    "mode": "getAll",
                                }
                            },
                        )
                        response.raise_for_status()
                        payload = response.json() or []
                    except httpx.HTTPError as exc:
                        if self._log is not None:
                            self._log.debug(
                                "tdarr.profiles.collection_error",
                                collection=collection,
                                error=str(exc),
                            )
                        continue
                    except ValueError:
                        continue
                    # Some Tdarr versions wrap responses in {"data": [...]};
                    # newer ones return the array directly. Handle both.
                    if isinstance(payload, dict):
                        if isinstance(payload.get("data"), list):
                            items = payload["data"]
                        else:
                            items = [payload]
                    elif isinstance(payload, list):
                        items = payload
                    else:
                        continue
                    for item in items:
                        if isinstance(item, dict):
                            # Tag the source so the picker can
                            # render "(plugin)" vs "(flow)".
                            item.setdefault("_collection", collection)
                            discovered.append(item)
        except httpx.HTTPError:
            return []

        out: list[TranscodeProfileSummary] = []
        for plugin in discovered:
            plugin_id = plugin.get("id") or plugin.get("_id")
            if not plugin_id:
                continue
            name = plugin.get("Name") or plugin.get("name") or str(plugin_id)
            description = plugin.get("Description") or plugin.get("description")
            collection = plugin.get("_collection", "")
            # Suffix the rendered name with "(flow)" for FlowsJSONDB
            # entries so operators see the distinction in the
            # picker dropdown.
            display_name = str(name)
            if collection == "FlowsJSONDB":
                display_name += " (flow)"
            out.append(
                TranscodeProfileSummary(
                    id=str(plugin_id),
                    name=display_name,
                    description=str(description) if description else None,
                    metadata={
                        "Type": plugin.get("Type"),
                        "Stage": plugin.get("Stage"),
                        "Collection": collection,
                    },
                )
            )
        return out

    async def get_transcode_job_status(
        self,
        config: IntegrationConfig,
        upstream_job_id: str,
    ) -> TranscodeJobStatus:
        """Poll Tdarr for one file's current transcode state.

        Plan §444. Tdarr exposes per-file state via
        ``FileJSONDB.<id>.transcodeStage``. We map Tdarr's strings
        to Auditarr's status enum via ``_TDARR_STATE_TO_AUDITARR``;
        unknown values flow through as ``"unknown"`` so the
        worker keeps polling rather than committing to a wrong
        terminal state.
        """
        try:
            async with self._client(config) as client:
                response = await client.post(
                    "/api/v2/cruddb",
                    json={
                        "data": {
                            "collection": "FileJSONDB",
                            "mode": "getById",
                            "docID": upstream_job_id,
                        }
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            return TranscodeJobStatus(
                status="unknown",
                detail=f"Tdarr HTTP error: {exc!s}",
            )
        except ValueError as exc:
            return TranscodeJobStatus(status="unknown", detail=str(exc))

        # Tdarr may return the doc directly or wrapped in {docs:[doc]}.
        doc: dict[str, Any] | None = None
        if isinstance(payload, dict):
            if isinstance(payload.get("docs"), list) and payload["docs"]:
                first = payload["docs"][0]
                if isinstance(first, dict):
                    doc = first
            else:
                doc = payload
        elif isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                doc = first

        if doc is None:
            return TranscodeJobStatus(
                status="unknown",
                detail="Tdarr returned an empty/unparseable payload",
            )

        raw_stage = str(doc.get("transcodeStage") or "").strip().lower()
        mapped = _TDARR_STATE_TO_AUDITARR.get(raw_stage, "unknown")

        # Tdarr emits a per-file ``transcodePercent`` (0..100) when
        # actively running; surface it through so the optimization
        # API can pass through real-time progress.
        progress: int | None = None
        try:
            pct = doc.get("transcodePercent")
            if pct is not None:
                progress = max(0, min(100, int(pct)))
        except (TypeError, ValueError):
            progress = None

        return TranscodeJobStatus(
            status=mapped,
            detail=raw_stage or None,
            progress_pct=progress,
            metadata={
                "transcodeStage": raw_stage,
                "_id": doc.get("_id") or doc.get("id"),
            },
        )


# ── v1.9 Stage 8.2 — Tdarr handoff helpers ──────────────────────


# Codec keywords we look for in Tdarr plugin names + descriptions
# to bias the score. The numeric weights are calibrated so that
# a plugin whose name contains the target codec wins over one
# that merely mentions it in the description.
_CODEC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "h264": ("h264", "h.264", "x264", "avc"),
    "h265": ("h265", "h.265", "x265", "hevc"),
    "av1": ("av1",),
    "vp9": ("vp9",),
}

_HARDWARE_KEYWORDS: tuple[str, ...] = (
    "nvenc",
    "qsv",
    "quicksync",
    "vaapi",
    "videotoolbox",
    "amf",
)


def score_stack(
    plugin: dict[str, Any],
    *,
    target_codec: str | None = None,
    prefer_hardware: bool = False,
) -> int:
    """v1.9 Stage 8.2 — score a Tdarr plugin against a transcode
    intent.

    Tdarr's plugin list is operator-specific (community plugins,
    custom plugins, built-ins all mixed); without a structured
    output-codec field on every entry, picking automatically is
    a heuristic. This scorer ranks by:

      * +20 if ``target_codec`` is in the plugin name.
      * +10 if ``target_codec`` is in the plugin description.
      * +5  if any hardware acceleration keyword is present AND
            ``prefer_hardware`` is True.
      * +2  if the plugin's ``Stage`` is the conventional
            ``Pre-processing`` stage (Tdarr's most common
            transcode stage; rules out ``Pre-cache`` /
            ``Post-processing`` / etc.).

    Returns 0 when nothing matched — operators get a stable
    "no auto-pick" signal rather than a random tiebreaker.

    Pure function; no I/O. Designed to be cheap to call across
    every plugin in the list so the caller can sort.
    """
    name = str(plugin.get("Name") or plugin.get("name") or "").lower()
    desc = str(
        plugin.get("Description") or plugin.get("description") or ""
    ).lower()
    stage = str(plugin.get("Stage") or "").lower()

    score = 0
    matched_codec = False

    if target_codec:
        target_norm = target_codec.lower().strip()
        keywords = _CODEC_KEYWORDS.get(target_norm, (target_norm,))
        if any(k in name for k in keywords):
            score += 20
            matched_codec = True
        elif any(k in desc for k in keywords):
            score += 10
            matched_codec = True

    if prefer_hardware:
        haystack = name + " " + desc
        if any(hk in haystack for hk in _HARDWARE_KEYWORDS):
            score += 5

    # The stage bonus only adds signal when we already have a
    # codec or hardware reason to consider this plugin —
    # otherwise every plugin gets +2 and the score noise drowns
    # out genuine matches.
    if matched_codec and ("pre-processing" in stage or "pre processing" in stage):
        score += 2

    return score


def pick_best_plugin(
    plugins: list[dict[str, Any]],
    *,
    target_codec: str | None = None,
    prefer_hardware: bool = False,
) -> dict[str, Any] | None:
    """v1.9 Stage 8.2 — convenience wrapper over ``score_stack``.

    Returns the highest-scoring plugin whose score is > 0, or
    None when nothing matched. We don't fall back to the
    first-alphabetical plugin: the calling rule should pass an
    explicit ``provider_profile_id`` rather than picking
    randomly when the heuristic is uncertain.
    """
    if not plugins:
        return None
    scored = [
        (score_stack(p, target_codec=target_codec, prefer_hardware=prefer_hardware), p)
        for p in plugins
        if isinstance(p, dict)
    ]
    scored.sort(key=lambda kv: kv[0], reverse=True)
    if not scored or scored[0][0] <= 0:
        return None
    return scored[0][1]


def build_output_name(
    *,
    input_path: str,
    target_codec: str | None = None,
    suffix: str | None = None,
) -> str:
    """v1.9 Stage 8.2 — derive an output filename for a Tdarr
    transcode.

    Tdarr's plugins do their own output naming by default —
    this helper exists for two distinct use cases:

      1. Operators who want Auditarr to preview the expected
         post-transcode name on the queue card before the job
         runs.
      2. Plugins / flows that respect a hint key like
         ``output_name`` in the job document.

    Rules:
      * Replace the extension with ``.mkv`` (Tdarr's default
        container for video re-encodes).
      * If ``target_codec`` is supplied, append ``.<codec>``
        before the extension (so ``Movie.mkv`` becomes
        ``Movie.hevc.mkv``).
      * If ``suffix`` is supplied, append it before any
        codec hint (so the operator's "transcoded" suffix
        renders as ``Movie.transcoded.hevc.mkv``).
      * Path components above the filename are preserved.

    Pure function; idempotent on already-formatted names so a
    re-run on the same path returns the same output."""
    import os

    head, tail = os.path.split(input_path or "")
    if not tail:
        return input_path
    stem, _ext = os.path.splitext(tail)
    parts = [stem]
    if suffix:
        s = suffix.strip().lstrip(".")
        if s and s.lower() not in stem.lower():
            parts.append(s)
    if target_codec:
        c = target_codec.strip().lower().lstrip(".")
        if c and c not in stem.lower():
            parts.append(c)
    new_name = ".".join(parts) + ".mkv"
    return os.path.join(head, new_name) if head else new_name


def register(context: PluginContext) -> Plugin:
    context.register_integration(TdarrProvider(log=context.logger()))
    return Plugin(context)
