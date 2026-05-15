"""Job catalogue tests."""

from __future__ import annotations

import pytest

from app.automation.catalogue import JobCatalogue, JobSpec


def _noop_runner(_session, _args, _ctx):  # type: ignore[no-untyped-def]
    raise NotImplementedError  # not called in these tests


def _spec(key: str, required: tuple[str, ...] = ()) -> JobSpec:
    return JobSpec(
        key=key,
        label=key.replace("_", " ").title(),
        description="",
        args_schema={"type": "object"},
        timeout_seconds=60,
        runner=_noop_runner,
        required_args=required,
    )


def test_register_and_get() -> None:
    cat = JobCatalogue()
    cat.register(_spec("a"))
    assert cat.get("a") is not None
    assert cat.get("nope") is None


def test_double_register_rejected() -> None:
    cat = JobCatalogue()
    cat.register(_spec("a"))
    with pytest.raises(ValueError, match="already registered"):
        cat.register(_spec("a"))


def test_require_raises_for_unknown() -> None:
    cat = JobCatalogue()
    with pytest.raises(KeyError):
        cat.require("nope")


def test_validate_args_required_missing() -> None:
    cat = JobCatalogue()
    cat.register(_spec("scan", required=("library_id",)))
    with pytest.raises(ValueError, match="requires arguments"):
        cat.validate_args("scan", {})


def test_validate_args_required_present() -> None:
    cat = JobCatalogue()
    cat.register(_spec("scan", required=("library_id",)))
    cat.validate_args("scan", {"library_id": "x"})  # does not raise


def test_list_all_sorted_by_label() -> None:
    cat = JobCatalogue()
    cat.register(_spec("z_job"))
    cat.register(_spec("a_job"))
    assert [s.key for s in cat.list_all()] == ["a_job", "z_job"]


def test_builtin_jobs_registered_on_default_catalogue() -> None:
    from app.automation.catalogue import get_catalogue, reset_catalogue

    reset_catalogue()
    cat = get_catalogue()
    keys = {s.key for s in cat.list_all()}
    assert {
        "scan_library",
        "healthcheck_integration",
        "sync_integration_tags",
        "evaluate_library",
    } <= keys
