"""Optimization system (Stage 10).

Profiles describe transcoding presets, the queue (Stage 7) collects work,
and the worker here actually runs ffmpeg and swaps the result into place.
"""

from app.optimization.ffmpeg_runner import (
    TranscodeRequest,
    TranscodeResult,
    build_ffmpeg_argv,
    run_transcode,
    validate_output,
)
from app.optimization.profile_schema import (
    SUPPORTED_AUDIO_CODECS,
    SUPPORTED_CONTAINERS,
    SUPPORTED_VIDEO_CODECS,
    ProfileDefinition,
)
from app.optimization.worker import OptimizationWorker, WorkerReport

__all__ = [
    "OptimizationWorker",
    "ProfileDefinition",
    "SUPPORTED_AUDIO_CODECS",
    "SUPPORTED_CONTAINERS",
    "SUPPORTED_VIDEO_CODECS",
    "TranscodeRequest",
    "TranscodeResult",
    "WorkerReport",
    "build_ffmpeg_argv",
    "run_transcode",
    "validate_output",
]
