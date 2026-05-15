"""Validation schema tests (Stage 21).

Pin the safety contract for runtime-editable settings:

1. Whitelist gating — keys NOT in RUNTIME_EDITABLE are rejected with
   a clear error pointing at the env file.
2. Per-key range/type enforcement — out-of-range values are rejected.
3. Schema/Settings sync — every editable key exists on the Settings
   model (a typo here would silently shadow nothing).
4. Per-key extra rules — plugin_gallery_url accepts empty (= disabled)
   but rejects non-http(s) values.
5. Secret length bounds — too-short and too-long plaintexts are
   rejected before they reach encryption.
"""

from __future__ import annotations

import pytest

from app.core.runtime_settings_schema import (
    RUNTIME_EDITABLE,
    SECRETS,
    RuntimeSettingValidationError,
    _verify_keys_match_settings,
    describe_runtime_settings,
    describe_secrets,
    is_runtime_editable,
    validate_runtime_setting,
    validate_secret,
)


# ── Whitelist gating ─────────────────────────────────────────
class TestWhitelist:
    def test_known_key_accepted(self) -> None:
        # Any whitelisted key with a valid value passes.
        assert validate_runtime_setting("log_level", "info") == "info"

    def test_unknown_key_rejected_with_env_hint(self) -> None:
        # The error message must guide the operator to the env file
        # rather than just say "no". Stage 20+ tests this from the
        # API; here we pin the message shape.
        with pytest.raises(RuntimeSettingValidationError) as exc:
            validate_runtime_setting("secret_key", "anything")
        assert "not a runtime-editable" in str(exc.value)
        assert "env file" in str(exc.value)

    def test_restart_required_keys_all_rejected(self) -> None:
        # Spot-check the most-dangerous keys: changing any of these
        # at runtime would crash the app. Pin them as explicitly
        # rejected so a future refactor can't silently expose one.
        forbidden = [
            "secret_key",
            "jwt_algorithm",
            "database_url",
            "redis_url",
            "host",
            "port",
            "data_dir",
            "plugin_dir",
            "frontend_dist",
        ]
        for key in forbidden:
            assert not is_runtime_editable(key), f"{key} should NOT be runtime-editable"
            with pytest.raises(RuntimeSettingValidationError):
                validate_runtime_setting(key, "anything")


# ── Schema / Settings sync ──────────────────────────────────
class TestSchemaSync:
    def test_every_whitelisted_key_exists_on_settings(self) -> None:
        """If this fails, a key was added to RUNTIME_EDITABLE but
        the matching attribute on Settings was removed or typo'd —
        the override would silently apply to nothing."""
        missing = _verify_keys_match_settings()
        assert missing == [], (
            f"Schema references keys that don't exist on Settings: "
            f"{missing}"
        )


# ── Range / type enforcement ────────────────────────────────
class TestRangeEnforcement:
    def test_log_level_pattern(self) -> None:
        # Allowed values.
        for level in ("debug", "info", "warning", "error", "critical"):
            assert validate_runtime_setting("log_level", level) == level
        # Disallowed.
        for bad in ("DEBUG", "verbose", "trace", ""):
            with pytest.raises(RuntimeSettingValidationError):
                validate_runtime_setting("log_level", bad)

    def test_log_level_type_mismatch(self) -> None:
        with pytest.raises(RuntimeSettingValidationError):
            validate_runtime_setting("log_level", 42)

    def test_access_token_ttl_range(self) -> None:
        assert validate_runtime_setting("access_token_ttl_minutes", 1) == 1
        assert validate_runtime_setting("access_token_ttl_minutes", 1440) == 1440
        with pytest.raises(RuntimeSettingValidationError):
            validate_runtime_setting("access_token_ttl_minutes", 0)
        with pytest.raises(RuntimeSettingValidationError):
            validate_runtime_setting("access_token_ttl_minutes", 99999)

    def test_scanner_concurrency_range(self) -> None:
        for v in (1, 4, 32):
            assert validate_runtime_setting("scanner_worker_concurrency", v) == v
        for bad in (0, 33, -1, 1000):
            with pytest.raises(RuntimeSettingValidationError):
                validate_runtime_setting("scanner_worker_concurrency", bad)

    def test_housekeeping_zero_is_allowed(self) -> None:
        # 0 means "keep forever" — must not be confused with "out of
        # range".
        for key in (
            "housekeeping_delivery_retention_days",
            "housekeeping_update_check_retention_days",
            "housekeeping_rule_evaluation_retention_days",
            "housekeeping_job_run_retention_days",
        ):
            assert validate_runtime_setting(key, 0) == 0

    def test_install_mode_pattern(self) -> None:
        for v in ("auto", "docker", "bare-metal", "unmanaged"):
            assert validate_runtime_setting("update_install_mode", v) == v
        for bad in ("kubernetes", "baremetal", "DOCKER", ""):
            with pytest.raises(RuntimeSettingValidationError):
                validate_runtime_setting("update_install_mode", bad)

    def test_ws_require_auth_boolean(self) -> None:
        assert validate_runtime_setting("ws_require_auth", True) is True
        assert validate_runtime_setting("ws_require_auth", False) is False
        # Pydantic v2 boolean fields accept the standard truthy/falsy
        # strings ("yes"/"no", "true"/"false", "1"/"0", "on"/"off").
        # This matches how the rest of the codebase parses env-var
        # booleans, so we accept it here rather than fight the
        # framework. What we DO reject is non-boolean garbage.
        for truthy in ("yes", "true", "on", "1"):
            assert validate_runtime_setting("ws_require_auth", truthy) is True
        for falsy in ("no", "false", "off", "0"):
            assert validate_runtime_setting("ws_require_auth", falsy) is False
        # Non-boolean strings + non-coercible types remain rejected.
        for bad in ("maybe", "definitely", "", []):
            with pytest.raises(RuntimeSettingValidationError):
                validate_runtime_setting("ws_require_auth", bad)


class TestUpdateFeedUrl:
    def test_http_and_https_accepted(self) -> None:
        for url in (
            "https://api.example.com/feed.json",
            "http://192.168.1.10:9000/auditarr/feed",
        ):
            assert validate_runtime_setting("update_feed_url", url) == url

    def test_non_http_rejected(self) -> None:
        for bad in ("ftp://x/", "file:///etc/passwd", "javascript:alert(1)"):
            with pytest.raises(RuntimeSettingValidationError):
                validate_runtime_setting("update_feed_url", bad)

    def test_empty_rejected(self) -> None:
        # update_feed_url is required to be non-empty (unlike
        # plugin_gallery_url which uses empty to mean "disabled").
        with pytest.raises(RuntimeSettingValidationError):
            validate_runtime_setting("update_feed_url", "")


class TestPluginGalleryUrl:
    def test_empty_means_disabled(self) -> None:
        assert validate_runtime_setting("plugin_gallery_url", "") == ""

    def test_http_accepted(self) -> None:
        url = "https://example.com/gallery.json"
        assert validate_runtime_setting("plugin_gallery_url", url) == url

    def test_non_http_rejected_when_non_empty(self) -> None:
        with pytest.raises(RuntimeSettingValidationError) as exc:
            validate_runtime_setting("plugin_gallery_url", "ftp://example/x")
        assert "http(s)" in str(exc.value)


# ── Secret validation ───────────────────────────────────────
class TestSecretValidation:
    def test_unknown_secret_rejected(self) -> None:
        with pytest.raises(RuntimeSettingValidationError):
            validate_secret("aws_access_key", "x" * 32)

    def test_too_short_rejected(self) -> None:
        with pytest.raises(RuntimeSettingValidationError) as exc:
            validate_secret("virustotal_api_key", "short")
        assert "at least" in str(exc.value)

    def test_too_long_rejected(self) -> None:
        # 200 chars > 128 max
        with pytest.raises(RuntimeSettingValidationError) as exc:
            validate_secret("virustotal_api_key", "x" * 200)
        assert "at most" in str(exc.value)

    def test_minimum_length_accepted(self) -> None:
        # exactly min_length = 32 must pass
        assert validate_secret("virustotal_api_key", "a" * 32) == "a" * 32

    def test_non_string_rejected(self) -> None:
        with pytest.raises(RuntimeSettingValidationError):
            validate_secret("virustotal_api_key", 12345)  # type: ignore[arg-type]


# ── Describe payload contract ────────────────────────────────
class TestDescribe:
    def test_describe_returns_one_entry_per_field(self) -> None:
        out = describe_runtime_settings()
        keys = {entry["key"] for entry in out}
        assert keys == {spec.key for spec in RUNTIME_EDITABLE}

    def test_describe_includes_required_metadata(self) -> None:
        for entry in describe_runtime_settings():
            for field in (
                "key", "label", "description", "category",
                "type", "default", "constraints", "impact",
                # Stage 2 additions.
                "group", "sensitivity", "restart_required",
            ):
                assert field in entry, f"missing {field} in {entry['key']}"

    def test_describe_stage2_metadata_defaults(self) -> None:
        """Every entry has the Stage-2 defaults applied. ``group``
        may be ``None`` for un-grouped fields; ``sensitivity`` is
        always ``"normal"`` on today's set; ``restart_required`` is
        always ``False`` today."""
        for entry in describe_runtime_settings():
            assert entry["sensitivity"] in ("normal", "elevated")
            assert isinstance(entry["restart_required"], bool)
            assert entry["group"] is None or isinstance(entry["group"], str)

    def test_describe_known_groupings_present(self) -> None:
        """The Stage-2 sub-groupings are wired through. Spot-check the
        four canonical groups operators will see in the UI."""
        by_key = {e["key"]: e for e in describe_runtime_settings()}
        # auth/tokens
        assert by_key["access_token_ttl_minutes"]["group"] == "tokens"
        assert by_key["refresh_token_ttl_days"]["group"] == "tokens"
        # auth/websocket
        assert by_key["ws_require_auth"]["group"] == "websocket"
        # rate_limiting/login
        assert by_key["auth_rate_limit_attempts"]["group"] == "login"
        # housekeeping/retention
        assert by_key["housekeeping_delivery_retention_days"]["group"] == "retention"
        # integrations/virustotal
        assert by_key["virustotal_enabled"]["group"] == "virustotal"

    def test_describe_secrets_marks_test_handler_flag(self) -> None:
        out = describe_secrets()
        by_key = {e["key"]: e for e in out}
        # VirusTotal has a test handler.
        assert by_key["virustotal_api_key"]["has_test_handler"] is True

    def test_describe_secrets_returns_one_entry_per_secret(self) -> None:
        out = describe_secrets()
        keys = {entry["key"] for entry in out}
        assert keys == {spec.key for spec in SECRETS}
