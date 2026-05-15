"""Stage 17 polish: defensive parsing in Plex/Jellyfin telemetry.

The fetchers run against real Plex/Jellyfin servers whose API
responses vary across versions and plugin configurations. We don't
trust nested .get() chains — one bad entry must not crash a poll.
These tests pin the defensive behavior.
"""

from __future__ import annotations

import datetime as _dt

from plugins.jellyfin.backend import _jellyfin_session_to_event
from plugins.plex.backend import _plex_history_to_event


# ── Plex ─────────────────────────────────────────────────────
class TestPlexHistoryParser:
    def test_minimal_well_formed_entry_returns_event(self) -> None:
        entry = {
            "ratingKey": "12345",
            "viewedAt": 1715000000,
            "duration": 7200000,
            "Player": {"platform": "Roku", "title": "Living Room"},
            "Media": [
                {
                    "videoCodec": "hevc",
                    "bitrate": 18000,
                    "width": 3840,
                    "height": 2160,
                    "container": "mkv",
                    "Part": [
                        {
                            "file": "/data/movies/test.mkv",
                            "videoDecision": "transcode",
                            "audioDecision": "copy",
                            "container": "mkv",
                        }
                    ],
                }
            ],
        }
        dto = _plex_history_to_event(entry)
        assert dto is not None
        assert dto.upstream_id == "plex:12345:1715000000"
        assert dto.source_path == "/data/movies/test.mkv"
        assert dto.decision == "transcode"
        assert dto.device_kind == "Roku"
        assert dto.device_name == "Living Room"
        assert dto.source_codec == "hevc"
        assert dto.source_bitrate_kbps == 18000
        assert dto.duration_s == 7200

    def test_missing_rating_key_returns_none(self) -> None:
        assert _plex_history_to_event({"viewedAt": 1}) is None

    def test_missing_viewed_at_returns_none(self) -> None:
        assert _plex_history_to_event({"ratingKey": "1"}) is None

    def test_string_viewed_at_is_accepted(self) -> None:
        """Some Plex builds return viewedAt as a string."""
        entry = {
            "ratingKey": "1",
            "viewedAt": "1715000000",
            "Media": [{"Part": [{"file": "/x.mkv"}]}],
        }
        dto = _plex_history_to_event(entry)
        assert dto is not None
        assert dto.started_at.year >= 2024

    def test_garbage_viewed_at_returns_none(self) -> None:
        """A non-numeric viewedAt is dropped rather than crashing."""
        entry = {
            "ratingKey": "1",
            "viewedAt": "not-a-timestamp",
            "Media": [{"Part": [{"file": "/x.mkv"}]}],
        }
        assert _plex_history_to_event(entry) is None

    def test_null_player_object_doesnt_crash(self) -> None:
        """Some history records carry Player: null."""
        entry = {
            "ratingKey": "1",
            "viewedAt": 1715000000,
            "Player": None,
            "Media": [{"Part": [{"file": "/x.mkv"}]}],
        }
        dto = _plex_history_to_event(entry)
        assert dto is not None
        assert dto.device_kind is None
        assert dto.device_name is None

    def test_empty_media_returns_none(self) -> None:
        entry = {"ratingKey": "1", "viewedAt": 1, "Media": []}
        assert _plex_history_to_event(entry) is None

    def test_empty_parts_returns_none(self) -> None:
        entry = {
            "ratingKey": "1",
            "viewedAt": 1,
            "Media": [{"Part": []}],
        }
        assert _plex_history_to_event(entry) is None

    def test_missing_file_returns_none(self) -> None:
        entry = {
            "ratingKey": "1",
            "viewedAt": 1,
            "Media": [{"Part": [{"videoDecision": "directplay"}]}],
        }
        assert _plex_history_to_event(entry) is None

    def test_malformed_media_shape_returns_none(self) -> None:
        """Some plugins return Media as a non-list (bug, but we
        shouldn't crash on it)."""
        entry = {
            "ratingKey": "1",
            "viewedAt": 1,
            "Media": "not a list",
        }
        assert _plex_history_to_event(entry) is None

    def test_direct_play_classification(self) -> None:
        entry = {
            "ratingKey": "1",
            "viewedAt": 1715000000,
            "Media": [
                {
                    "Part": [
                        {
                            "file": "/x.mkv",
                            "videoDecision": "directplay",
                            "audioDecision": "directplay",
                        }
                    ]
                }
            ],
        }
        dto = _plex_history_to_event(entry)
        assert dto is not None
        assert dto.decision == "direct_play"

    def test_direct_stream_classification(self) -> None:
        entry = {
            "ratingKey": "1",
            "viewedAt": 1715000000,
            "Media": [
                {
                    "Part": [
                        {
                            "file": "/x.mkv",
                            "videoDecision": "copy",
                            "audioDecision": "copy",
                        }
                    ]
                }
            ],
        }
        dto = _plex_history_to_event(entry)
        assert dto is not None
        assert dto.decision == "direct_stream"


# ── Jellyfin ─────────────────────────────────────────────────
class TestJellyfinSessionParser:
    CUTOFF = _dt.datetime(2024, 1, 1, tzinfo=_dt.UTC)

    def test_minimal_well_formed_session_returns_event(self) -> None:
        session = {
            "Id": "sess-1",
            "Client": "Jellyfin Web",
            "DeviceName": "Chrome on Mac",
            "LastPlaybackCheckIn": "2024-05-11T12:00:00.0000000Z",
            "PlayState": {"PlayMethod": "Transcode"},
            "NowPlayingItem": {
                "Id": "item-1",
                "Path": "/data/movies/x.mkv",
                "Container": "mkv",
                "RunTimeTicks": 72_000_000_000,  # 7200s
                "MediaStreams": [
                    {
                        "Type": "Video",
                        "Codec": "hevc",
                        "BitRate": 18_000_000,
                        "Width": 3840,
                        "Height": 2160,
                    }
                ],
            },
            "TranscodingInfo": {
                "TranscodeReasons": ["VideoCodecNotSupported"],
                "VideoCodec": "h264",
                "Bitrate": 6_000_000,
            },
        }
        dto = _jellyfin_session_to_event(session, self.CUTOFF)
        assert dto is not None
        assert dto.upstream_id == "jellyfin:sess-1:item-1"
        assert dto.source_path == "/data/movies/x.mkv"
        assert dto.decision == "transcode"
        assert dto.source_codec == "hevc"
        assert dto.source_bitrate_kbps == 18_000  # bps → kbps
        assert dto.target_codec == "h264"
        assert dto.target_bitrate_kbps == 6_000
        assert dto.reason_code == "video.codec.unsupported"
        assert dto.duration_s == 7200

    def test_missing_now_playing_item_returns_none(self) -> None:
        assert _jellyfin_session_to_event({"Id": "s1"}, self.CUTOFF) is None

    def test_null_now_playing_item_returns_none(self) -> None:
        assert (
            _jellyfin_session_to_event(
                {"Id": "s1", "NowPlayingItem": None}, self.CUTOFF
            )
            is None
        )

    def test_missing_path_returns_none(self) -> None:
        session = {
            "Id": "s1",
            "NowPlayingItem": {"Id": "i1"},  # no Path
        }
        assert _jellyfin_session_to_event(session, self.CUTOFF) is None

    def test_null_play_state_doesnt_crash(self) -> None:
        """Some sessions have PlayState: null — must default to
        direct_play rather than crash."""
        session = {
            "Id": "s1",
            "PlayState": None,
            "NowPlayingItem": {"Id": "i1", "Path": "/x.mkv"},
        }
        dto = _jellyfin_session_to_event(session, self.CUTOFF)
        assert dto is not None
        assert dto.decision == "direct_play"

    def test_null_transcoding_info_doesnt_crash(self) -> None:
        session = {
            "Id": "s1",
            "PlayState": {"PlayMethod": "Transcode"},
            "TranscodingInfo": None,
            "NowPlayingItem": {"Id": "i1", "Path": "/x.mkv"},
        }
        dto = _jellyfin_session_to_event(session, self.CUTOFF)
        assert dto is not None
        assert dto.reason_code is None

    def test_malformed_media_streams_doesnt_crash(self) -> None:
        """Streams may contain garbage entries on broken servers."""
        session = {
            "Id": "s1",
            "PlayState": {"PlayMethod": "DirectPlay"},
            "NowPlayingItem": {
                "Id": "i1",
                "Path": "/x.mkv",
                "MediaStreams": [
                    "not a dict",
                    None,
                    {"Type": "Video", "Codec": "h264"},
                ],
            },
        }
        dto = _jellyfin_session_to_event(session, self.CUTOFF)
        assert dto is not None
        assert dto.source_codec == "h264"

    def test_fallback_to_cutoff_when_check_in_missing(self) -> None:
        session = {
            "Id": "s1",
            "NowPlayingItem": {"Id": "i1", "Path": "/x.mkv"},
        }
        dto = _jellyfin_session_to_event(session, self.CUTOFF)
        assert dto is not None
        assert dto.started_at == self.CUTOFF

    def test_play_method_directstream(self) -> None:
        session = {
            "Id": "s1",
            "PlayState": {"PlayMethod": "DirectStream"},
            "NowPlayingItem": {"Id": "i1", "Path": "/x.mkv"},
        }
        dto = _jellyfin_session_to_event(session, self.CUTOFF)
        assert dto is not None
        assert dto.decision == "direct_stream"
