#!/usr/bin/env bash
# Unit tests for the bare-metal updater watcher's URL-derivation logic.
#
# We can't run the watcher's main poll loop in a test environment
# (needs systemd + real /opt/auditarr), but we CAN extract and verify
# the regex that derives RELEASE_URL_TEMPLATE from UPDATE_FEED_URL.
# This pins the v1.8.2 contract: a GitHub api.github.com feed URL
# resolves to GitHub's auto-generated source-tarball URL.

set -euo pipefail

PASS=0
FAIL=0

assert_eq() {
    local got="$1" want="$2" name="$3"
    if [[ "$got" == "$want" ]]; then
        printf '  ok %s\n' "$name"
        PASS=$((PASS + 1))
    else
        printf '  FAIL %s\n    got:  %q\n    want: %q\n' "$name" "$got" "$want"
        FAIL=$((FAIL + 1))
    fi
}

assert_empty() {
    local got="$1" name="$2"
    if [[ -z "$got" ]]; then
        printf '  ok %s\n' "$name"
        PASS=$((PASS + 1))
    else
        printf '  FAIL %s\n    expected empty, got: %q\n' "$name" "$got"
        FAIL=$((FAIL + 1))
    fi
}

# This is the regex+derivation block copied verbatim from
# updater/auditarr-update-bare-metal.sh (apply_update function).
# If this test starts failing, the watcher script's logic has
# diverged from the contract this test pins.
derive_release_url() {
    local feed_url="$1"
    local current_template="${2:-}"
    local result="$current_template"
    if [[ -z "$result" && "$feed_url" =~ ^https://api\.github\.com/repos/([^/]+)/([^/]+)/commits/[^/]+$ ]]; then
        local owner="${BASH_REMATCH[1]}"
        local repo="${BASH_REMATCH[2]}"
        result="https://github.com/${owner}/${repo}/archive/%s.tar.gz"
    fi
    if [[ -z "$result" && "$feed_url" =~ ^https://api\.github\.com/repos/([^/]+)/([^/]+)/releases/latest$ ]]; then
        local owner="${BASH_REMATCH[1]}"
        local repo="${BASH_REMATCH[2]}"
        result="https://github.com/${owner}/${repo}/archive/refs/tags/v%s.tar.gz"
    fi
    printf '%s' "$result"
}

echo "v1.8.2 watcher URL-derivation tests"
echo "==================================="

# Test 1 — ShogyX/Auditarr (the actual upstream).
echo "test_shogyx_auditarr:"
got="$(derive_release_url "https://api.github.com/repos/ShogyX/Auditarr/releases/latest")"
assert_eq "$got" \
    "https://github.com/ShogyX/Auditarr/archive/refs/tags/v%s.tar.gz" \
    "derives ShogyX/Auditarr tarball URL"

# Test 2 — Generic two-segment GitHub repo.
echo "test_generic_github_repo:"
got="$(derive_release_url "https://api.github.com/repos/foo/bar/releases/latest")"
assert_eq "$got" \
    "https://github.com/foo/bar/archive/refs/tags/v%s.tar.gz" \
    "derives generic owner/repo URL"

# Test 3 — Explicit template wins over auto-derivation.
echo "test_explicit_template_wins:"
explicit="https://example.com/releases/v%s.tgz"
got="$(derive_release_url "https://api.github.com/repos/ShogyX/Auditarr/releases/latest" "$explicit")"
assert_eq "$got" "$explicit" "explicit template not overridden"

# Test 4 — Non-GitHub feed leaves template empty.
echo "test_non_github_feed:"
got="$(derive_release_url "https://mirror.example.com/auditarr/feed.json")"
assert_empty "$got" "non-GitHub feed: template stays empty"

# Test 5 — http:// (not https) rejected.
echo "test_http_rejected:"
got="$(derive_release_url "http://api.github.com/repos/ShogyX/Auditarr/releases/latest")"
assert_empty "$got" "http URL not accepted (github.com is https-only)"

# Test 6 — Trailing slash rejected (malformed).
echo "test_trailing_slash_rejected:"
got="$(derive_release_url "https://api.github.com/repos/ShogyX/Auditarr/releases/latest/")"
assert_empty "$got" "trailing slash rejected"

# Test 7 — Wrong path prefix rejected.
echo "test_wrong_path_rejected:"
got="$(derive_release_url "https://api.github.com/users/ShogyX")"
assert_empty "$got" "non-releases path rejected"

# Test 8 — Empty feed URL.
echo "test_empty_feed_url:"
got="$(derive_release_url "")"
assert_empty "$got" "empty feed URL: template stays empty"

# Test 9 — Sample URL renders correctly with version substitution.
echo "test_url_renders_with_version:"
template="$(derive_release_url "https://api.github.com/repos/ShogyX/Auditarr/releases/latest")"
# shellcheck disable=SC2059
rendered="$(printf "$template" "1.8.2")"
assert_eq "$rendered" \
    "https://github.com/ShogyX/Auditarr/archive/refs/tags/v1.8.2.tar.gz" \
    "%s substitution produces a real URL"

# Test 10 — Hyphenated repo names work.
echo "test_hyphenated_repo:"
got="$(derive_release_url "https://api.github.com/repos/foo-bar/baz-qux/releases/latest")"
assert_eq "$got" \
    "https://github.com/foo-bar/baz-qux/archive/refs/tags/v%s.tar.gz" \
    "hyphenated owner+repo handled"

# Test 11 — v1.9.x commits/<branch> feed → SHA-tarball URL.
echo "test_commits_main_feed:"
got="$(derive_release_url "https://api.github.com/repos/ShogyX/Auditarr/commits/main")"
assert_eq "$got" \
    "https://github.com/ShogyX/Auditarr/archive/%s.tar.gz" \
    "commits/main feed derives SHA tarball URL"

# Test 12 — Non-default branch (commits/develop).
echo "test_commits_develop_feed:"
got="$(derive_release_url "https://api.github.com/repos/foo/bar/commits/develop")"
assert_eq "$got" \
    "https://github.com/foo/bar/archive/%s.tar.gz" \
    "commits/develop feed derives SHA tarball URL"

# Test 13 — SHA tarball URL substitutes correctly with a real commit SHA.
echo "test_sha_tarball_substitution:"
template="$(derive_release_url "https://api.github.com/repos/ShogyX/Auditarr/commits/main")"
# shellcheck disable=SC2059
rendered="$(printf "$template" "70a38e8298050b9dcda5aaaa36826f2cdb2f955d")"
assert_eq "$rendered" \
    "https://github.com/ShogyX/Auditarr/archive/70a38e8298050b9dcda5aaaa36826f2cdb2f955d.tar.gz" \
    "%s substitution produces a SHA-keyed tarball URL"

# Test 14 — Commits feed beats releases feed only when the URL shape matches.
echo "test_commits_feed_does_not_match_releases:"
got="$(derive_release_url "https://api.github.com/repos/foo/bar/commits")"
assert_empty "$got" "commits with no branch segment rejected"

echo
echo "Result: $PASS passed, $FAIL failed"
if (( FAIL > 0 )); then
    exit 1
fi
