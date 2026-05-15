#!/usr/bin/env bash
# Auditarr bare-metal installer (v1.6.0+).
#
# Targets LXC containers and VMs where Docker isn't available or
# desired. Tested on Debian 12 / Ubuntu 22.04 + 24.04. Other distros
# work — the script tries to detect the package manager and adapts,
# but only Debian-family installs are exercised in CI.
#
# What it does, in order:
#   1. Verify the OS family + privileges
#   2. Install system packages (python3.12, postgres, redis, ffmpeg,
#      nginx-light, build essentials)
#   3. Create the ``auditarr`` system user + ``/opt/auditarr`` tree
#   4. Lay down the backend, built frontend, and built-in plugins
#   5. Build the frontend (or skip if dist/ already shipped in tarball)
#   6. Create a Python venv at ``/opt/auditarr/venv`` and install the
#      backend in editable mode
#   7. Bootstrap Postgres: create the auditarr role + database
#   8. Generate ``/etc/auditarr/auditarr.env`` (secret key + URLs)
#   9. Run alembic migrations
#  10. Prompt for the first admin user and create it via the CLI
#  11. Install systemd units for auditarr-api + auditarr-worker
#  12. Optionally configure nginx as a reverse proxy on :80
#  13. Print "what's next"
#
# Re-running this script is safe: each step checks for prior state
# (existing user, existing DB, existing .env) and asks before
# rewriting. A ``--non-interactive`` mode with env-driven defaults is
# also supported for IaC tooling.
#
# Usage:
#   Interactive (prompts for admin credentials, nginx, etc.):
#     sudo ./install-bare-metal.sh
#
#   Fire-and-forget (auto-generates everything, prints credentials at the end):
#     sudo ./install-bare-metal.sh --auto
#     sudo ./install-bare-metal.sh -y          # same as --auto
#
#   Scripted (full env-var control, no prompts):
#     sudo AUDITARR_NONINTERACTIVE=1 AUDITARR_ADMIN_EMAIL=a@b AUDITARR_ADMIN_USERNAME=admin \
#          AUDITARR_ADMIN_PASSWORD='...' ./install-bare-metal.sh
#
# Flags:
#   --auto, -y, --fire-and-forget   Run unattended. Auto-generates a strong
#                                   admin password if AUDITARR_ADMIN_PASSWORD
#                                   isn't set. Skips the nginx prompt
#                                   (defaults to no nginx; use
#                                   AUDITARR_INSTALL_NGINX=yes to opt in).
#                                   Final summary prints the generated
#                                   credentials so the operator can log in.
#   --help, -h                      Print usage and exit.
#
# Common env-var overrides (full list in the Configuration knobs block below):
#   AUDITARR_LISTEN_HOST=0.0.0.0    Interface to bind. Default is 0.0.0.0 so
#                                   the app is reachable from other machines.
#                                   Set to 127.0.0.1 to bind loopback only
#                                   (e.g. when fronting with a reverse proxy).
#   AUDITARR_LISTEN_PORT=8000       Port the app listens on.
#   AUDITARR_DISPLAY_HOST=foo.bar   Hostname/IP printed in the "log in here"
#                                   banner. Auto-detected from ``hostname -I``
#                                   if not set.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Flag parsing ──────────────────────────────────────────────
# Consolidated-audit follow-up: ``--auto`` mode makes the installer
# fire-and-forget. Everything that previously prompted (admin
# credentials, nginx) now gets a sane default; the generated admin
# credentials are printed in the final summary so the operator can
# log in. Underlying env vars still take precedence — auto-generation
# only fills in what wasn't supplied.
AUTO_MODE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto|-y|--fire-and-forget)
            AUTO_MODE=1
            shift
            ;;
        --help|-h)
            # Print the leading comment block (header + Usage + Flags).
            # The boundary is the ``set -euo pipefail`` line — everything
            # before it is comment-only documentation.
            sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf "Unknown argument: %s\n" "$1" >&2
            printf "Run with --help for usage.\n" >&2
            exit 64  # EX_USAGE
            ;;
    esac
done

# ── Configuration knobs ───────────────────────────────────────
APP_USER="${AUDITARR_USER:-auditarr}"
APP_GROUP="${AUDITARR_GROUP:-auditarr}"
APP_HOME="${AUDITARR_HOME:-/opt/auditarr}"
APP_CONFIG_DIR="${AUDITARR_CONFIG_DIR:-/etc/auditarr}"
APP_STATE_DIR="${AUDITARR_STATE_DIR:-/var/lib/auditarr}"
APP_LOG_DIR="${AUDITARR_LOG_DIR:-/var/log/auditarr}"
PG_DB="${AUDITARR_PG_DB:-auditarr}"
PG_USER="${AUDITARR_PG_USER:-auditarr}"
PG_HOST="${AUDITARR_PG_HOST:-127.0.0.1}"
PG_PORT="${AUDITARR_PG_PORT:-5432}"
REDIS_URL="${AUDITARR_REDIS_URL:-redis://127.0.0.1:6379/0}"
# LISTEN_HOST is what gunicorn binds to. Default to 0.0.0.0 so the
# app is reachable from any interface — operators on LXC containers
# or VMs need this to hit the app from a browser on another machine.
# Override with AUDITARR_LISTEN_HOST=127.0.0.1 if you want to bind
# only the loopback (e.g. when fronting with a reverse proxy on
# the same host).
LISTEN_HOST="${AUDITARR_LISTEN_HOST:-0.0.0.0}"
LISTEN_PORT="${AUDITARR_LISTEN_PORT:-8000}"
# LOCAL_HOST is what same-box callers use (nginx upstream, health
# check curl). 0.0.0.0 is a wildcard bind address, not a routable
# destination — anything trying to *connect* to it fails. When the
# app is bound to 0.0.0.0, callers on the same host reach it via
# 127.0.0.1. When bound to anything else, that same value works
# as a destination.
if [[ "$LISTEN_HOST" == "0.0.0.0" || "$LISTEN_HOST" == "::" ]]; then
    LOCAL_HOST="127.0.0.1"
else
    LOCAL_HOST="$LISTEN_HOST"
fi
# DISPLAY_HOST is what we print in the "log in here" banner. When
# bound to a wildcard, give the operator a URL they can actually
# use from another machine — try the host's primary LAN IP first,
# then the hostname, then fall back to LOCAL_HOST. Operators can
# override with AUDITARR_DISPLAY_HOST if they know better (e.g.
# they're behind NAT and want to show a public hostname).
if [[ -n "${AUDITARR_DISPLAY_HOST:-}" ]]; then
    DISPLAY_HOST="$AUDITARR_DISPLAY_HOST"
elif [[ "$LISTEN_HOST" == "0.0.0.0" || "$LISTEN_HOST" == "::" ]]; then
    DISPLAY_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [[ -z "$DISPLAY_HOST" ]]; then
        DISPLAY_HOST="$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "$LOCAL_HOST")"
    fi
else
    DISPLAY_HOST="$LISTEN_HOST"
fi
INSTALL_NGINX="${AUDITARR_INSTALL_NGINX:-prompt}"  # yes | no | prompt
NON_INTERACTIVE="${AUDITARR_NONINTERACTIVE:-0}"

# ── Apply --auto defaults ─────────────────────────────────────
# When the operator launched with ``--auto``, force non-interactive
# mode and default nginx to off (most operators want to wire their
# own reverse proxy; the bare-metal API binds to 0.0.0.0:8000 so
# they can curl it after install to confirm health).
if [[ "$AUTO_MODE" == "1" ]]; then
    NON_INTERACTIVE=1
    if [[ "$INSTALL_NGINX" == "prompt" ]]; then
        INSTALL_NGINX="no"
    fi
fi

# Track the generated admin password so the final "what's next"
# banner can echo it back. Empty when env-supplied; populated when
# we auto-generated. (Email + username don't need their own tracker
# — the final banner reads from the live env vars regardless of
# whether they were supplied or auto-defaulted.)
GENERATED_ADMIN_PASSWORD=""

# ── Pretty output helpers ─────────────────────────────────────
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'
    YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

step()  { printf "${BOLD}==>${RESET} %s\n" "$1"; }
info()  { printf "    %s\n" "$1"; }
note()  { printf "    ${DIM}%s${RESET}\n" "$1"; }
warn()  { printf "    ${YELLOW}!${RESET} %s\n" "$1" >&2; }
ok()    { printf "    ${GREEN}✓${RESET} %s\n" "$1"; }
die()   { printf "${RED}✗${RESET} %s\n" "$1" >&2; exit 1; }

# Prompt helpers honoring NON_INTERACTIVE.
prompt() {
    # $1: prompt text, $2: env-var-name for non-interactive default
    local text="$1" envvar="$2" default="${3:-}" value=""
    if [[ "$NON_INTERACTIVE" == "1" ]]; then
        value="${!envvar:-$default}"
        if [[ -z "$value" ]]; then
            die "$envvar must be set in non-interactive mode"
        fi
        printf "%s\n" "$value"
        return
    fi
    if [[ -n "$default" ]]; then
        read -r -p "    $text [$default]: " value
        value="${value:-$default}"
    else
        read -r -p "    $text: " value
    fi
    printf "%s\n" "$value"
}

prompt_secret() {
    # Like prompt(), but doesn't echo. $1 = label, $2 = envvar.
    local text="$1" envvar="$2" value=""
    if [[ "$NON_INTERACTIVE" == "1" ]]; then
        value="${!envvar:-}"
        if [[ -z "$value" ]]; then
            die "$envvar must be set in non-interactive mode"
        fi
        printf "%s\n" "$value"
        return
    fi
    read -r -s -p "    $text: " value
    echo "" >&2
    printf "%s\n" "$value"
}

confirm() {
    # $1 = prompt, returns 0 for yes, 1 for no. Defaults to yes.
    local text="$1" reply=""
    if [[ "$NON_INTERACTIVE" == "1" ]]; then
        return 0
    fi
    read -r -p "    $text [Y/n]: " reply
    case "${reply,,}" in
        n|no) return 1 ;;
        *) return 0 ;;
    esac
}

# ── Banner ────────────────────────────────────────────────────
cat <<'BANNER'

  ┌──────────────────────────────────────────────┐
  │   Auditarr bare-metal setup (LXC / VM)       │
  │   For Docker installs use ./install.sh       │
  └──────────────────────────────────────────────┘

BANNER

if [[ "$AUTO_MODE" == "1" ]]; then
    # Using ``printf '%s\n'`` rather than embedding variables in the
    # format string keeps shellcheck happy (SC2059) and is also the
    # safer pattern — if a color var ever contained a stray %s, the
    # format-string version would interpret it.
    printf '%s\n' "  ${BOLD}${GREEN}Fire-and-forget mode active.${RESET}"
    printf '%s\n' "  ${DIM}Will auto-generate any missing admin credentials.${RESET}"
    printf '%s\n' "  ${DIM}Binding to ${LISTEN_HOST}:${LISTEN_PORT} (override with AUDITARR_LISTEN_HOST).${RESET}"
    printf '%s\n' "  ${DIM}Nginx defaults to off (set AUDITARR_INSTALL_NGINX=yes to enable).${RESET}"
    printf '\n'
fi

# ── Prerequisites ─────────────────────────────────────────────
step "Checking environment"

if [[ "$EUID" -ne 0 ]]; then
    die "This installer needs to run as root (use sudo)."
fi

if [[ ! -f /etc/os-release ]]; then
    die "Unable to detect OS — /etc/os-release missing."
fi

# shellcheck disable=SC1091
. /etc/os-release
OS_ID="${ID:-unknown}"
OS_LIKE="${ID_LIKE:-}"

case "$OS_ID" in
    debian|ubuntu)
        PKG_MGR="apt"
        ;;
    *)
        case "$OS_LIKE" in
            *debian*) PKG_MGR="apt" ;;
            *)
                warn "OS $OS_ID isn't officially supported — proceeding,"
                warn "but you may need to install system packages by hand."
                PKG_MGR="manual"
                ;;
        esac
        ;;
esac

ok "Detected $PRETTY_NAME (package manager: $PKG_MGR)"

# Verify the tarball contents are where we expect.
[[ -d backend ]]  || die "backend/ not found — run this script from the extracted release tarball."
[[ -d frontend ]] || die "frontend/ not found — same."
[[ -f backend/pyproject.toml ]] || die "backend/pyproject.toml missing."

ok "Release tarball layout looks correct"

# ── Safety: refuse to run from inside the install destination ──
#
# The installer copies $SCRIPT_DIR into $APP_HOME, then builds the
# frontend in-tree, then rsyncs frontend/dist/ on top of
# $APP_HOME/frontend/. If $SCRIPT_DIR and $APP_HOME are the same
# directory, that final rsync uses --delete and destroys the build
# output it just produced (this manifests as "file has vanished"
# warnings and exit code 24, which is fatal under set -e -o pipefail).
#
# The fix is to extract the release tarball somewhere else (e.g.
# /tmp/auditarr-release/) and run the installer from there. The
# installer then copies into /opt/auditarr cleanly.
#
# Resolve both paths to their canonical form so a symlinked
# /opt/auditarr or trailing slash doesn't fool the comparison.
canonical_script_dir="$(readlink -f "$SCRIPT_DIR")"
canonical_app_home="$(readlink -f -m "$APP_HOME")"
if [[ "$canonical_script_dir" == "$canonical_app_home" ]]; then
    cat >&2 <<EOF

✗ ERROR: This installer is running from inside its install target.

  Script directory: $canonical_script_dir
  Install target:   $canonical_app_home (\$APP_HOME)

  These must be different directories. If they're the same, the
  build step writes into the same path the installer is trying to
  rsync TO, and --delete destroys the build output mid-transfer
  (you'll see "file has vanished" rsync warnings).

  Fix: extract the release tarball somewhere else, then run the
  installer from there. For example:

      mkdir -p /tmp/auditarr-release
      tar -xzf auditarr-*.tar.gz -C /tmp/auditarr-release --strip-components=1
      cd /tmp/auditarr-release
      sudo ./install-bare-metal.sh

  If you DO want to install under a different prefix than
  /opt/auditarr, set AUDITARR_HOME before running:

      sudo AUDITARR_HOME=/srv/auditarr ./install-bare-metal.sh

EOF
    exit 1
fi

# ── Safety: detect a pre-existing conflicting auditarr.service ──
#
# Some boxes have a leftover ``auditarr.service`` from an unrelated
# install (e.g. a custom Python script at /home/Auditarr/server.py).
# That unit will keep restart-looping and shadow our new
# auditarr-api.service in monitoring tools. Surface it now so the
# operator can disable it before our install completes.
if systemctl list-unit-files 'auditarr.service' --no-legend 2>/dev/null \
    | grep -q '^auditarr\.service'; then
    existing_exec="$(systemctl show -p ExecStart --value auditarr.service 2>/dev/null | head -1)"
    cat >&2 <<EOF

⚠ WARNING: A pre-existing ``auditarr.service`` is registered on this host.

  Its ExecStart is:
      $existing_exec

  This installer creates ``auditarr-api.service`` and
  ``auditarr-worker.service`` (note the hyphenated suffixes). The
  pre-existing ``auditarr.service`` is unrelated and will keep
  restart-looping after our install completes, which can be
  confusing in monitoring.

  Before continuing, in another terminal please run:

      sudo systemctl disable --now auditarr.service
      sudo rm -f /etc/systemd/system/auditarr.service
      sudo systemctl daemon-reload

EOF
    if [[ "${AUDITARR_NONINTERACTIVE:-0}" != "1" ]]; then
        read -rp "Continue anyway? [y/N] " ack
        [[ "$ack" =~ ^[Yy] ]] || die "Aborted by operator."
    else
        warn "AUDITARR_NONINTERACTIVE=1 — continuing despite the conflict."
    fi
fi

# ── System packages ───────────────────────────────────────────
step "Installing system packages"

if [[ "$PKG_MGR" == "apt" ]]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq

    # We need Python 3.12 specifically. Debian 12 ships 3.11, Ubuntu
    # 24.04 ships 3.12. On Debian 12 we add the deadsnakes-equivalent
    # repository through the python3-launchpadlib + add-apt-repository
    # path; falling back to compiling from source is out of scope for
    # this script — the operator should run Ubuntu 24.04 or pick
    # a 3.12-bearing distro.
    PYTHON_BIN=""
    for candidate in python3.12 python3.13; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done

    if [[ -z "$PYTHON_BIN" ]]; then
        # Try to install python3.12 from the distro repo first.
        if apt-cache show python3.12 >/dev/null 2>&1; then
            apt-get install -y -qq python3.12 python3.12-venv python3.12-dev
            PYTHON_BIN="python3.12"
        else
            warn "python3.12 not available in this distro's repository."
            warn "On Debian 12, add deadsnakes or run Ubuntu 24.04 instead."
            die "Python 3.12 is required."
        fi
    fi

    # Base packages.
    apt-get install -y -qq \
        "${PYTHON_BIN}-venv" "${PYTHON_BIN}-dev" \
        build-essential \
        postgresql postgresql-contrib \
        redis-server \
        ffmpeg \
        nginx-light \
        ca-certificates \
        curl \
        openssl \
        sudo

    # Node 22 (LTS) for the frontend build. If the operator's
    # release tarball already contains frontend/dist/, we skip the
    # build and don't need Node — checked after the layout step.
    #
    # Stage 15 audit fix (Issue 23): accept Node 18 too. Vite 5+
    # (what the frontend uses) requires Node >=18, and Ubuntu 22.04
    # LTS ships with Node 18 — without this, the script reinstalls
    # Node on top of a perfectly working install and risks ending
    # up with two divergent /usr/bin/node binaries.
    if [[ ! -d frontend/dist ]]; then
        if ! command -v node >/dev/null 2>&1 || ! node -v | grep -qE '^v(18|20|22|24)\.'; then
            info "Installing Node.js 22 (NodeSource)…"
            curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
            apt-get install -y -qq nodejs
        fi
    fi

    ok "System packages installed"
elif [[ "$PKG_MGR" == "manual" ]]; then
    warn "Skipping package install on $OS_ID."
    info "Please ensure the following are installed before continuing:"
    info "  python3.12 (with venv + dev headers)"
    info "  postgresql server"
    info "  redis-server"
    info "  ffmpeg"
    info "  build-essential / gcc / make"
    info "  nginx (optional)"
    if ! confirm "Continue anyway?"; then
        die "Aborted by operator."
    fi
    # Stage 15 audit fix (Issue 23): the Debian branch above uses
    # ``command -v`` to find an installed Python and would die with
    # a clear message if neither python3.12 nor python3.13 is present.
    # The manual branch previously hardcoded PYTHON_BIN="python3.12"
    # without checking. On non-Debian systems without that binary,
    # every subsequent ``$PYTHON_BIN -m venv ...`` call would fail
    # with a confusing "command not found" buried in the venv step.
    # Now we mirror the Debian branch's detection: try 3.12, then
    # 3.13, then die with a pointed message.
    PYTHON_BIN=""
    for candidate in python3.12 python3.13; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
    if [[ -z "$PYTHON_BIN" ]]; then
        warn "Neither python3.12 nor python3.13 is on PATH."
        warn "Install one (with venv + dev headers) and re-run this script."
        die "Python 3.12+ is required."
    fi
    info "Using $PYTHON_BIN at $(command -v "$PYTHON_BIN")"
fi

# Make sure postgres + redis are running.
systemctl enable --now postgresql || warn "Couldn't auto-start postgresql."
systemctl enable --now redis-server || warn "Couldn't auto-start redis-server."

# ── Service user + directories ────────────────────────────────
step "Creating service user and directories"

if id -u "$APP_USER" >/dev/null 2>&1; then
    ok "User $APP_USER already exists"
else
    useradd \
        --system \
        --create-home \
        --home-dir "$APP_HOME" \
        --shell /usr/sbin/nologin \
        --comment "Auditarr service user" \
        "$APP_USER"
    ok "Created system user $APP_USER"
fi

mkdir -p "$APP_HOME" "$APP_CONFIG_DIR" "$APP_STATE_DIR" "$APP_LOG_DIR"
chown -R "$APP_USER:$APP_GROUP" "$APP_HOME" "$APP_STATE_DIR" "$APP_LOG_DIR"
chown root:"$APP_GROUP" "$APP_CONFIG_DIR"
chmod 0750 "$APP_CONFIG_DIR"
ok "Directories prepared:"
note "  $APP_HOME        (app)"
note "  $APP_CONFIG_DIR  (env + secrets)"
note "  $APP_STATE_DIR   (data — scan caches, etc.)"
note "  $APP_LOG_DIR     (logs)"

# ── Lay down code ─────────────────────────────────────────────
step "Installing application files"

# rsync if available, fallback to cp -a. Both preserve permissions
# and skip our own development junk via include/exclude lists.
INSTALL_CMD="rsync -a --delete"
if ! command -v rsync >/dev/null 2>&1; then
    INSTALL_CMD="cp -a"
fi

# ``safe_rsync`` wraps rsync with two safety properties:
#
# 1. Exit code 24 ("some files vanished") is treated as a benign
#    warning, not a fatal error. This happens when logrotate, an
#    antivirus, or another writer touches the source tree during
#    transfer. We still log it so the operator can investigate
#    if it keeps recurring.
#
# 2. Refuses to run when the source directory is an ancestor or
#    descendant of the destination directory. This is the
#    self-destructive case that caused the original bug report:
#    if you rsync ``foo/bar/`` (with --delete) into ``foo/``, rsync
#    happily deletes ``bar/`` from the destination view because
#    it doesn't exist in the source's flat listing. We catch that
#    here and refuse explicitly with a clear error.
#
# Both safety checks are scoped to this helper so the rest of the
# script can use it as a drop-in for ``rsync -a --delete``.
safe_rsync() {
    local src="$1"; shift
    local dst="$1"; shift
    # All remaining args are extra rsync flags (e.g. --exclude).
    local extra=( "$@" )

    local csrc cdst
    csrc="$(readlink -f -m "${src%/}")"
    cdst="$(readlink -f -m "${dst%/}")"

    # Self / parent / child relationship check.
    if [[ "$csrc" == "$cdst" ]]; then
        die "safe_rsync: source and destination are the same ($csrc)."
    fi
    if [[ "$cdst" == "$csrc"/* ]]; then
        die "safe_rsync: destination ($cdst) is inside source ($csrc) — refusing to recurse."
    fi
    if [[ "$csrc" == "$cdst"/* ]]; then
        die "safe_rsync: source ($csrc) is inside destination ($cdst) — refusing because --delete would wipe the source."
    fi

    # Tolerate exit 24 (vanished files); everything else stays fatal.
    set +e
    rsync -a --delete "${extra[@]}" "${src%/}/" "${dst%/}/"
    local code=$?
    set -e
    if [[ $code -eq 0 ]]; then
        return 0
    elif [[ $code -eq 24 ]]; then
        warn "rsync reported vanished files during ${src} → ${dst} (exit 24). Continuing; this is usually a benign concurrent-write artifact."
        return 0
    else
        die "rsync failed (exit $code) for ${src} → ${dst}."
    fi
}

# Backend + plugins.
# Layout: built-in plugins ship inside ``$APP_HOME/backend/plugins``
# (copied as part of the backend tree). The separate ``$APP_HOME/plugins``
# directory is for USER-installed plugins; the installer creates it
# empty. Previously this directory got a duplicate copy of the
# built-ins via a second rsync/cp pass, which made the loader scan
# both dirs and log "plugin.id_shadowed" warnings for every plugin
# at startup. The env file sets AUDITARR_BUILTIN_PLUGIN_DIR + 
# AUDITARR_PLUGIN_DIR so the loader knows where each is.
mkdir -p "$APP_HOME/backend" "$APP_HOME/plugins"
if [[ "$INSTALL_CMD" == "rsync -a --delete" ]]; then
    safe_rsync backend "$APP_HOME/backend" \
        --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='.pytest_cache' --exclude='.mypy_cache' --exclude='.ruff_cache' \
        --exclude='htmlcov' --exclude='.coverage'
else
    rm -rf "$APP_HOME/backend"
    cp -a backend "$APP_HOME/backend"
    find "$APP_HOME/backend" "$APP_HOME/plugins" \
        \( -name __pycache__ -o -name '*.pyc' \) -prune -exec rm -rf {} + 2>/dev/null || true
fi

# Frontend: either copy the pre-built dist/ from the tarball, or
# build it now if Node is available.
#
# When building, the build runs inside ``$SCRIPT_DIR/frontend``, then
# we copy ``$SCRIPT_DIR/frontend/dist/`` into ``$APP_HOME/frontend/``.
# The same-directory guard near the top of this script already
# prevents the case where those two paths overlap; ``safe_rsync``'s
# parent/child check is belt-and-braces in case anyone bypasses the
# guard with a clever symlink.
if [[ -d frontend/dist ]]; then
    info "Using prebuilt frontend/dist from the release tarball"
    mkdir -p "$APP_HOME/frontend"
    if [[ "$INSTALL_CMD" == "rsync -a --delete" ]]; then
        safe_rsync frontend/dist "$APP_HOME/frontend"
    else
        rm -rf "$APP_HOME/frontend"
        cp -a frontend/dist "$APP_HOME/frontend"
    fi
else
    info "Building frontend from source (this takes ~1-2 minutes)…"
    # Stage 15 audit fix (Issue 23): use a subshell so the cwd change
    # is local — no pushd/popd pairing to worry about. Previously,
    # ``set -e`` + a failure inside ``npm install`` or ``npm run build``
    # would skip the ``popd``, leaving the script's working directory
    # as ``frontend/`` and corrupting every subsequent relative path
    # reference. With a subshell ``(...)``, the directory change is
    # discarded on subshell exit regardless of success or failure.
    #
    # The trailing ``|| die`` also gives the operator a pointed
    # error message instead of relying on ``set -e``'s generic abort.
    (
        cd frontend
        npm install --no-audit --no-fund --no-progress
        npm run build
    ) || die "Frontend build failed — see the output above for the underlying error."
    mkdir -p "$APP_HOME/frontend"
    if [[ "$INSTALL_CMD" == "rsync -a --delete" ]]; then
        safe_rsync frontend/dist "$APP_HOME/frontend"
    else
        rm -rf "$APP_HOME/frontend"
        cp -a frontend/dist "$APP_HOME/frontend"
    fi
fi

# Docs: copy the project-root ``docs/`` tree to ``$APP_HOME/docs``.
# The backend defaults ``docs_dir = ./docs`` which resolves to
# ``/opt/auditarr/backend/docs`` at runtime (relative to the
# systemd WorkingDirectory) — but the source ships docs at the
# *project* root, not under backend/. Without this copy + the
# AUDITARR_DOCS_DIR env var override, the loader logs
# ``docs.dir_missing`` and the in-app help/search surface is empty.
if [[ -d docs ]]; then
    mkdir -p "$APP_HOME/docs"
    if [[ "$INSTALL_CMD" == "rsync -a --delete" ]]; then
        safe_rsync docs "$APP_HOME/docs"
    else
        rm -rf "$APP_HOME/docs"
        cp -a docs "$APP_HOME/docs"
    fi
fi

# Migration note: earlier installer versions double-copied the
# built-in plugins into ``$APP_HOME/plugins`` (the user dir),
# which made the loader log ``plugin.id_shadowed`` for each one
# on every startup. Detect that condition and remove the
# duplicates, so an upgrade install gets a clean log. We only
# remove the seven canonical built-in plugin directories — any
# user-added plugin in $APP_HOME/plugins stays untouched.
if [[ -d "$APP_HOME/plugins" && -d "$APP_HOME/backend/plugins" ]]; then
    cleaned=0
    for builtin in "$APP_HOME"/backend/plugins/*/; do
        [[ -d "$builtin" ]] || continue
        name="$(basename "$builtin")"
        dupe="$APP_HOME/plugins/$name"
        if [[ -d "$dupe" ]]; then
            rm -rf "$dupe"
            cleaned=$((cleaned + 1))
        fi
    done
    if [[ "$cleaned" -gt 0 ]]; then
        note "Removed $cleaned built-in plugin dupes from \$APP_HOME/plugins (upgrade-only cleanup)"
    fi
fi

chown -R "$APP_USER:$APP_GROUP" "$APP_HOME"
ok "Application files installed under $APP_HOME"

# ── Python venv + backend install ─────────────────────────────
step "Creating Python virtual environment"

VENV_DIR="$APP_HOME/venv"
if [[ -d "$VENV_DIR" ]]; then
    info "Existing venv detected — reusing it"
else
    sudo -u "$APP_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# Upgrade pip and install the backend in editable mode. Using
# editable install so the operator can patch backend/ in-place and
# restart the service without re-running the installer.
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip setuptools wheel
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --quiet -e "$APP_HOME/backend"

# Also install gunicorn — backend pyproject doesn't list it because
# the Docker image uses gunicorn from a system package layer; under
# bare-metal we install it into the venv.
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --quiet 'gunicorn>=22.0' 'uvicorn[standard]>=0.32'
ok "Backend installed into venv"

# ── PostgreSQL bootstrap ──────────────────────────────────────
step "Configuring PostgreSQL"

# Generate (or reuse) a password for the auditarr DB role. We store
# it in /etc/auditarr/auditarr.env so the env file is the single
# source of truth.
if [[ -f "$APP_CONFIG_DIR/auditarr.env" ]] \
        && grep -q '^AUDITARR_DATABASE_URL=' "$APP_CONFIG_DIR/auditarr.env"; then
    PG_PASSWORD="$(grep '^AUDITARR_DATABASE_URL=' "$APP_CONFIG_DIR/auditarr.env" \
                   | sed -E 's|.+://[^:]+:([^@]+)@.+|\1|')"
    note "Reusing existing DB credentials from $APP_CONFIG_DIR/auditarr.env"
else
    PG_PASSWORD="$(openssl rand -hex 24)"
fi

# Create the role + db if they don't exist. We run all psql via
# ``sudo -u postgres`` since the default Debian pg_hba uses ``peer``
# auth for the postgres superuser.
ROLE_EXISTS="$(sudo -u postgres psql -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'" 2>/dev/null || true)"
if [[ "$ROLE_EXISTS" != "1" ]]; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
        "CREATE ROLE \"$PG_USER\" LOGIN PASSWORD '$PG_PASSWORD';" >/dev/null
    ok "Created Postgres role $PG_USER"
else
    # Update the password anyway — keeps the .env and DB in sync.
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
        "ALTER ROLE \"$PG_USER\" WITH LOGIN PASSWORD '$PG_PASSWORD';" >/dev/null
    ok "Postgres role $PG_USER already existed (password rotated)"
fi

DB_EXISTS="$(sudo -u postgres psql -tAc \
    "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" 2>/dev/null || true)"
if [[ "$DB_EXISTS" != "1" ]]; then
    sudo -u postgres createdb -O "$PG_USER" "$PG_DB"
    ok "Created Postgres database $PG_DB"
else
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
        "ALTER DATABASE \"$PG_DB\" OWNER TO \"$PG_USER\";" >/dev/null
    ok "Postgres database $PG_DB already existed"
fi

DATABASE_URL="postgresql+asyncpg://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PG_DB}"

# ── Environment file ──────────────────────────────────────────
step "Writing $APP_CONFIG_DIR/auditarr.env"

if [[ -f "$APP_CONFIG_DIR/auditarr.env" ]]; then
    SECRET_KEY="$(grep '^AUDITARR_SECRET_KEY=' "$APP_CONFIG_DIR/auditarr.env" | cut -d= -f2- || true)"
    if [[ -z "$SECRET_KEY" ]]; then
        SECRET_KEY="$(openssl rand -base64 48 | tr -d '\n')"
    fi
    note "Updating existing env file (secret key preserved)"
else
    SECRET_KEY="$(openssl rand -base64 48 | tr -d '\n')"
fi

cat > "$APP_CONFIG_DIR/auditarr.env" <<EOF
# Auditarr configuration. Generated by install-bare-metal.sh.
# Edit and restart auditarr-api.service + auditarr-worker.service.

# Core
AUDITARR_SECRET_KEY=${SECRET_KEY}
AUDITARR_HOST=${LISTEN_HOST}
AUDITARR_PORT=${LISTEN_PORT}
# Lowercase per settings.py Literal — pydantic_settings is strict
# about Literal types and rejects "INFO" / "WARNING" / etc.
AUDITARR_LOG_LEVEL=info
# Bare-metal installs are production by default. Override with
# AUDITARR_ENV=development before re-running the installer if you
# want dev defaults (more verbose logging, dev-mode CORS origins).
AUDITARR_ENV=production

# Storage
AUDITARR_DATABASE_URL=${DATABASE_URL}
AUDITARR_REDIS_URL=${REDIS_URL}
AUDITARR_STATE_DIR=${APP_STATE_DIR}
# Built-in plugins ship inside the backend tree; the user plugin
# directory is empty by default and intended for operator-installed
# plugins (mount it as a volume in Docker, or drop files in for
# bare-metal). Listing them separately lets the loader log the
# distinction and avoids "id_shadowed" false-positives.
AUDITARR_BUILTIN_PLUGIN_DIR=${APP_HOME}/backend/plugins
AUDITARR_PLUGIN_DIR=${APP_HOME}/plugins
AUDITARR_DOCS_DIR=${APP_HOME}/docs
AUDITARR_FRONTEND_DIST=${APP_HOME}/frontend

# Migrations run as part of the api unit's ExecStartPre, not at every
# import — leave this on for the first boot and turn it off after.
AUDITARR_RUN_MIGRATIONS=1

# Set to 0 to disable WebSocket auth temporarily for debugging.
AUDITARR_WS_REQUIRE_AUTH=1

# CORS allowed origins — only needed if the frontend is served from
# a DIFFERENT host than the API. The bundled frontend is served by
# the FastAPI app itself (same-origin), so no override is needed for
# the default install. Format: comma-separated list of origins,
# e.g. AUDITARR_ALLOWED_ORIGINS=https://auditarr.example.com,https://10.10.0.5
EOF
chown root:"$APP_GROUP" "$APP_CONFIG_DIR/auditarr.env"
chmod 0640 "$APP_CONFIG_DIR/auditarr.env"
ok "Wrote env file (mode 0640, group readable by $APP_GROUP)"

# ── Migrations + admin bootstrap ──────────────────────────────
step "Running database migrations"

# Load the env file into THIS shell, then exec the venv binary as
# the app user with those vars passed through. We use ``set -a``
# rather than ``xargs -d`` because the latter is a GNU extension
# and may not exist on busybox-based LXC images. Source-ing also
# handles values containing whitespace correctly (a quoted password
# with a space would break the xargs split).
_load_env() {
    # shellcheck disable=SC2046,SC1090
    set -a
    . "$APP_CONFIG_DIR/auditarr.env"
    set +a
}
_run_as_app() {
    # $@: command to run. Env is the env file's contents plus any
    # AUDITARR_* var currently exported in the caller shell (so
    # one-shot bootstrap secrets like AUDITARR_ADMIN_PASSWORD, which
    # we deliberately don't persist to the env file, still reach
    # the child process).
    (
        _load_env
        env_args=()
        # First pass: every var in the env file.
        declare -A seen=()
        while IFS= read -r line; do
            var="${line%%=*}"
            env_args+=("$var=${!var}")
            seen["$var"]=1
        done < <(grep -E '^[A-Z_][A-Z_0-9]*=' "$APP_CONFIG_DIR/auditarr.env")
        # Second pass: any AUDITARR_* var that's currently exported
        # in the caller shell but isn't in the env file. This covers
        # one-shot install-time secrets (admin password) without
        # accidentally leaking unrelated env. Uses ``compgen -A
        # variable`` to enumerate names, which is portable across
        # bash 4+ and doesn't choke on values containing newlines.
        while IFS= read -r var; do
            [[ -n "${seen[$var]:-}" ]] && continue
            [[ -z "${!var:-}" ]] && continue
            env_args+=("$var=${!var}")
        done < <(compgen -A variable | grep -E '^AUDITARR_')
        sudo -u "$APP_USER" env "${env_args[@]}" "$@"
    )
}

# Run migrations. We cd into $APP_HOME/backend before invoking
# alembic — defense in depth. alembic.ini is also configured with
# ``%(here)s`` paths so script_location + prepend_sys_path resolve
# correctly regardless of cwd, but cd-ing first keeps the call
# robust against any future alembic-ini edits that forget the
# %(here)s prefix.
_run_as_app bash -c "cd \"$APP_HOME/backend\" && \"$VENV_DIR/bin/alembic\" -c \"$APP_HOME/backend/alembic.ini\" upgrade head"
ok "Schema migrated to head"

step "Creating first admin user"

EXISTING_ADMIN_COUNT="$(_run_as_app "$VENV_DIR/bin/auditarr" user count-admins 2>/dev/null || echo "0")"
# Trim whitespace just in case the CLI ever changes its output.
EXISTING_ADMIN_COUNT="${EXISTING_ADMIN_COUNT//[[:space:]]/}"

if [[ "$EXISTING_ADMIN_COUNT" -gt 0 ]]; then
    ok "Admin user already present — skipping bootstrap"
else
    # In auto mode, generate any admin credential the operator
    # didn't supply via env var. Email + username get sensible
    # defaults; password is a 32-character base64-encoded random
    # string (sufficient entropy that brute-force isn't practical).
    # Generated values are printed in the final "what's next"
    # banner so the operator can log in immediately.
    if [[ "$AUTO_MODE" == "1" ]]; then
        if [[ -z "${AUDITARR_ADMIN_EMAIL:-}" ]]; then
            AUDITARR_ADMIN_EMAIL="admin@localhost"
        fi
        if [[ -z "${AUDITARR_ADMIN_USERNAME:-}" ]]; then
            AUDITARR_ADMIN_USERNAME="admin"
        fi
        if [[ -z "${AUDITARR_ADMIN_PASSWORD:-}" ]]; then
            # base64 of 24 bytes ≈ 32 printable chars; well above
            # the 12-char minimum enforced below.
            AUDITARR_ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -d '=' | tr '/+' '_-')"
            GENERATED_ADMIN_PASSWORD="$AUDITARR_ADMIN_PASSWORD"
        fi
        export AUDITARR_ADMIN_EMAIL AUDITARR_ADMIN_USERNAME AUDITARR_ADMIN_PASSWORD
        info "Auto-generating admin credentials (will print at end)…"
    else
        info "Provide credentials for the first admin user."
    fi

    ADMIN_EMAIL="$(prompt 'Admin email' AUDITARR_ADMIN_EMAIL 'admin@localhost')"
    ADMIN_USERNAME="$(prompt 'Admin username' AUDITARR_ADMIN_USERNAME 'admin')"
    ADMIN_PASSWORD="$(prompt_secret 'Admin password (min 12 chars)' AUDITARR_ADMIN_PASSWORD)"
    if [[ ${#ADMIN_PASSWORD} -lt 12 ]]; then
        die "Admin password must be at least 12 characters."
    fi

    export AUDITARR_ADMIN_EMAIL="$ADMIN_EMAIL"
    export AUDITARR_ADMIN_USERNAME="$ADMIN_USERNAME"
    export AUDITARR_ADMIN_PASSWORD="$ADMIN_PASSWORD"
    _run_as_app "$VENV_DIR/bin/auditarr" user bootstrap-admin \
        --email "$ADMIN_EMAIL" \
        --username "$ADMIN_USERNAME" \
        --password-from-env AUDITARR_ADMIN_PASSWORD
    unset AUDITARR_ADMIN_PASSWORD
    ok "Admin user $ADMIN_USERNAME created"
fi

# ── systemd units ─────────────────────────────────────────────
step "Installing systemd units"

cat > /etc/systemd/system/auditarr-api.service <<EOF
[Unit]
Description=Auditarr API (gunicorn/uvicorn)
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target
Requires=postgresql.service redis-server.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_HOME/backend
EnvironmentFile=$APP_CONFIG_DIR/auditarr.env

# Migrations are also run on boot to catch upgrades; idempotent.
ExecStartPre=$VENV_DIR/bin/alembic -c $APP_HOME/backend/alembic.ini upgrade head
ExecStart=$VENV_DIR/bin/gunicorn \\
    --bind \${AUDITARR_HOST}:\${AUDITARR_PORT} \\
    --workers 2 \\
    --worker-class uvicorn.workers.UvicornWorker \\
    --timeout 60 \\
    --graceful-timeout 30 \\
    --access-logfile - \\
    --error-logfile - \\
    app.main:app

Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGTERM

# Hardening — see systemd.exec(5). All paths the app legitimately
# writes to are listed under ReadWritePaths.
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ReadWritePaths=$APP_STATE_DIR $APP_LOG_DIR $APP_HOME
ProtectClock=yes
RestrictRealtime=yes
LockPersonality=yes
RestrictSUIDSGID=yes

# Resource limits — generous but not infinite. Adjust based on the
# size of your library.
LimitNOFILE=65536
MemoryHigh=1G
MemoryMax=2G

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/auditarr-worker.service <<EOF
[Unit]
Description=Auditarr background worker (arq)
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target
Requires=postgresql.service redis-server.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_HOME/backend
EnvironmentFile=$APP_CONFIG_DIR/auditarr.env

ExecStart=$VENV_DIR/bin/arq app.worker.WorkerSettings

Restart=on-failure
RestartSec=5
TimeoutStopSec=60
KillSignal=SIGTERM

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ReadWritePaths=$APP_STATE_DIR $APP_LOG_DIR $APP_HOME
ProtectClock=yes
RestrictRealtime=yes
LockPersonality=yes
RestrictSUIDSGID=yes

LimitNOFILE=65536
MemoryHigh=1G
MemoryMax=2G

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable auditarr-api.service auditarr-worker.service >/dev/null
systemctl restart auditarr-api.service auditarr-worker.service
ok "Installed and started auditarr-api.service + auditarr-worker.service"

# ── Stage 19: update watcher ──────────────────────────────────
step "Installing the bare-metal update watcher"

# Lay down the helper script under /opt/auditarr/updater/ so it
# stays alongside the application. Re-running the installer
# refreshes the script along with the rest of the tree.
mkdir -p "$APP_HOME/updater"
if [[ -f "$SCRIPT_DIR/updater/auditarr-update-bare-metal.sh" ]]; then
    install -m 0755 -o root -g root \
        "$SCRIPT_DIR/updater/auditarr-update-bare-metal.sh" \
        "$APP_HOME/updater/auditarr-update-bare-metal.sh"
    ok "Installed update watcher script"
else
    warn "updater/auditarr-update-bare-metal.sh not found in tarball"
    warn "Auto-updates from the UI will be disabled."
fi

# Updater env file (kept separate from auditarr.env so operators can
# enable / disable / repoint update sources without touching app
# config). Default disables auto-updates — the operator must set
# AUDITARR_RELEASE_TARBALL_URL to opt in.
if [[ ! -f "$APP_CONFIG_DIR/updater.env" ]]; then
    cat > "$APP_CONFIG_DIR/updater.env" <<EOF
# Auditarr updater configuration (bare-metal install).
#
# Auto-updates are OPT-IN. Uncomment and set the URLs below to enable.
# %s is substituted with the requested version, e.g. "1.4.0".
#
# AUDITARR_RELEASE_TARBALL_URL=https://github.com/auditarr/auditarr/releases/download/v%s/auditarr-%s.tar.gz
# AUDITARR_RELEASE_CHECKSUM_URL=https://github.com/auditarr/auditarr/releases/download/v%s/auditarr-%s.tar.gz.sha256
#
# Reuse install settings so the helper finds the right paths.
AUDITARR_STATE_DIR=$APP_STATE_DIR
AUDITARR_HOME=$APP_HOME
AUDITARR_CONFIG_DIR=$APP_CONFIG_DIR
AUDITARR_VENV_DIR=$VENV_DIR
AUDITARR_USER=$APP_USER
AUDITARR_GROUP=$APP_GROUP
EOF
    chown root:"$APP_GROUP" "$APP_CONFIG_DIR/updater.env"
    chmod 0640 "$APP_CONFIG_DIR/updater.env"
    ok "Wrote $APP_CONFIG_DIR/updater.env (auto-updates DISABLED by default)"
fi

# systemd unit for the watcher.
cat > /etc/systemd/system/auditarr-update-watcher.service <<EOF
[Unit]
Description=Auditarr update watcher (bare-metal)
After=network-online.target auditarr-api.service
Wants=network-online.target
# We do NOT Require= auditarr-api — the watcher needs to run even
# when the API is down so it can roll back from a failed apply.

[Service]
Type=simple
User=root
Group=root
# Watcher needs root because it stops/starts other systemd units and
# rsyncs into /opt/auditarr. Defence-in-depth: hardening below
# restricts what root can write.
EnvironmentFile=$APP_CONFIG_DIR/updater.env

ExecStart=$APP_HOME/updater/auditarr-update-bare-metal.sh

Restart=on-failure
RestartSec=10

# Hardening — watcher only legitimately writes under STATE_DIR,
# APP_HOME, and (briefly) the unit files it manages.
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ReadWritePaths=$APP_STATE_DIR $APP_HOME
LockPersonality=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=multi-user.target
EOF

# Update the app's main env file to tell the backend it's bare-metal.
# This stays a hint — the backend's auto-detector would land on
# bare-metal anyway, but pinning it makes the contract explicit and
# avoids the auto-detector firing if the operator runs in some weird
# nested container.
if ! grep -q '^AUDITARR_UPDATE_INSTALL_MODE=' "$APP_CONFIG_DIR/auditarr.env"; then
    echo "" >> "$APP_CONFIG_DIR/auditarr.env"
    echo "# Stage 19: install-mode pin (set by install-bare-metal.sh)" >> "$APP_CONFIG_DIR/auditarr.env"
    echo "AUDITARR_UPDATE_INSTALL_MODE=bare-metal" >> "$APP_CONFIG_DIR/auditarr.env"
fi

systemctl daemon-reload
systemctl enable auditarr-update-watcher.service >/dev/null
systemctl restart auditarr-update-watcher.service
ok "Installed and started auditarr-update-watcher.service"
note "Auto-updates are DISABLED until you set AUDITARR_RELEASE_TARBALL_URL"
note "in $APP_CONFIG_DIR/updater.env and restart auditarr-update-watcher."

# ── nginx reverse proxy (optional) ────────────────────────────
INSTALL_NGINX_ANSWER="$INSTALL_NGINX"
if [[ "$INSTALL_NGINX" == "prompt" ]]; then
    if confirm "Configure nginx to reverse-proxy port 80 → $LISTEN_HOST:$LISTEN_PORT?"; then
        INSTALL_NGINX_ANSWER="yes"
    else
        INSTALL_NGINX_ANSWER="no"
    fi
fi

if [[ "$INSTALL_NGINX_ANSWER" == "yes" ]]; then
    step "Configuring nginx reverse proxy"
    SERVER_NAME_DEFAULT="$(hostname -f 2>/dev/null || hostname)"
    cat > /etc/nginx/sites-available/auditarr <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $SERVER_NAME_DEFAULT _;
    client_max_body_size 50M;

    # API + frontend share the same FastAPI app — the backend serves
    # /assets/ and / from \$AUDITARR_FRONTEND_DIST.
    location / {
        proxy_pass http://$LOCAL_HOST:$LISTEN_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # WebSocket upgrade for /api/v1/ws.
    location /api/v1/ws {
        proxy_pass http://$LOCAL_HOST:$LISTEN_PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 86400;
    }
}
EOF
    # Remove the default site if it's there so our default_server wins.
    rm -f /etc/nginx/sites-enabled/default
    ln -sf /etc/nginx/sites-available/auditarr /etc/nginx/sites-enabled/auditarr
    nginx -t >/dev/null 2>&1 || die "nginx config test failed."
    systemctl enable --now nginx >/dev/null
    systemctl reload nginx
    ok "nginx configured — Auditarr is reachable on http://$SERVER_NAME_DEFAULT/"
fi

# ── Final status + next steps ─────────────────────────────────
step "Verifying the API is up"

# Give it a moment to bind, then probe /health.
# Use LOCAL_HOST (127.0.0.1 when bound to 0.0.0.0) — curl can't
# actually connect to a 0.0.0.0 destination.
sleep 2
HEALTH_URL="http://${LOCAL_HOST}:${LISTEN_PORT}/api/v1/health"
if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    ok "API responded to $HEALTH_URL"
else
    warn "API didn't respond at $HEALTH_URL yet."
    warn "Check: journalctl -u auditarr-api -n 50 --no-pager"
fi

cat <<NEXT

${BOLD}Done.${RESET}

Auditarr is running on $LISTEN_HOST:$LISTEN_PORT.

  ${BOLD}Access:${RESET}
    http://${DISPLAY_HOST}:${LISTEN_PORT}/

  ${BOLD}Services:${RESET}
    systemctl status auditarr-api auditarr-worker

  ${BOLD}Logs:${RESET}
    journalctl -u auditarr-api -f
    journalctl -u auditarr-worker -f

  ${BOLD}Env file:${RESET}
    $APP_CONFIG_DIR/auditarr.env

  ${BOLD}CLI (run as $APP_USER):${RESET}
    sudo -u $APP_USER bash -c 'set -a; . $APP_CONFIG_DIR/auditarr.env; set +a; $VENV_DIR/bin/auditarr --help'

  ${BOLD}Update path:${RESET}
    1. Stop services:        systemctl stop auditarr-api auditarr-worker
    2. Re-extract new tarball over $APP_HOME (keeping the env file)
    3. Re-run this installer — it's idempotent.

NEXT

# ── Generated-credentials banner ──────────────────────────────
# Print this AFTER the main "Done." block (and outside the heredoc)
# so the credentials line is at the very bottom of the output where
# operators are most likely to see it. Only shown when we actually
# generated values — env-supplied installs don't echo the password.
if [[ -n "$GENERATED_ADMIN_PASSWORD" ]]; then
    # Build the inner lines without color codes in the format string
    # (SC2059). Colors are concatenated around the rendered line.
    # Width: 57 char inside the border (matching the box drawing).
    _line_top="┌─ First-time login credentials ─────────────────────────┐"
    _line_bot="└─────────────────────────────────────────────────────────┘"
    _line_blank="│                                                         │"
    _url="http://${DISPLAY_HOST}:${LISTEN_PORT}/"
    _email="${AUDITARR_ADMIN_EMAIL:-admin@localhost}"
    _user="${AUDITARR_ADMIN_USERNAME:-admin}"
    # Inside-box width is 55 chars (between │ … │ minus 3 leading spaces).
    _row_url=$(printf '│   URL:      %-44s│' "$_url")
    _row_email=$(printf '│   Email:    %-44s│' "$_email")
    _row_user=$(printf '│   Username: %-44s│' "$_user")
    # Password gets yellow coloring INSIDE the cell. The width math
    # needs to subtract the printable length, not the byte length —
    # so we build the cell content plain first, then re-wrap with
    # color codes around just the value.
    _pad_pw=$(printf '%-44s' "$GENERATED_ADMIN_PASSWORD")
    _row_pw="│   Password: ${YELLOW}${_pad_pw}${RESET}│"
    _hint=$(printf '│   %-53s│' "Change the password after first login.")

    printf '\n'
    printf '%s\n' "${BOLD}${_line_top}${RESET}"
    printf '%s\n' "${BOLD}${_line_blank}${RESET}"
    printf '%s\n' "${BOLD}${_row_url}${RESET}"
    printf '%s\n' "${BOLD}${_row_email}${RESET}"
    printf '%s\n' "${BOLD}${_row_user}${RESET}"
    printf '%s\n' "${BOLD}${_row_pw}${RESET}"
    printf '%s\n' "${BOLD}${_line_blank}${RESET}"
    printf '%s\n' "${BOLD}${_hint}${RESET}"
    printf '%s\n' "${BOLD}${_line_bot}${RESET}"
    printf '\n'
fi
