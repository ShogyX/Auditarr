#!/usr/bin/env bash
# Auditarr one-shot installer (v1.0).
#
# Walks a new operator through:
#   1. Verifying docker + docker compose are installed
#   2. Generating a strong secret key
#   3. Prompting for first admin credentials
#   4. Prompting for library bind-mount paths
#   5. Writing .env from .env.example
#   6. Bringing up the stack
#
# Idempotent: re-running it detects an existing .env and offers to
# rewrite or keep it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Pretty output helpers ─────────────────────────────────────
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'
    DIM=$'\033[2m'
    GREEN=$'\033[32m'
    RED=$'\033[31m'
    RESET=$'\033[0m'
else
    BOLD=""; DIM=""; GREEN=""; RED=""; RESET=""
fi

step() { printf "${BOLD}==>${RESET} %s\n" "$1"; }
info() { printf "    %s\n" "$1"; }
note() { printf "    ${DIM}%s${RESET}\n" "$1"; }
ok()   { printf "    ${GREEN}✓${RESET} %s\n" "$1"; }
die()  { printf "${RED}✗${RESET} %s\n" "$1" >&2; exit 1; }

# ── Banner ────────────────────────────────────────────────────
cat <<'BANNER'

  ┌─────────────────────────────────┐
  │       Auditarr v1.0 setup       │
  └─────────────────────────────────┘

BANNER

# ── Prerequisites ─────────────────────────────────────────────
step "Checking prerequisites"

command -v docker >/dev/null 2>&1 || die "docker not found in PATH. Install Docker Engine first."
if ! docker compose version >/dev/null 2>&1; then
    die "docker compose plugin not found. Install the v2 plugin (docker-compose-plugin)."
fi
ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"
ok "docker compose $(docker compose version --short)"

command -v openssl >/dev/null 2>&1 || die "openssl not found in PATH (needed for secret_key generation)."
ok "openssl"

if [[ ! -f docker-compose.yml ]]; then
    die "docker-compose.yml not found in $(pwd). Run this script from the Auditarr root directory."
fi
ok "docker-compose.yml found"

# ── Existing .env handling ────────────────────────────────────
SKIP_CONFIG=0
if [[ -f .env ]]; then
    step "An existing .env was detected"
    read -r -p "    Overwrite? [y/N] " reply
    if [[ "${reply,,}" != "y" ]]; then
        info "Keeping existing .env. Skipping config prompts."
        SKIP_CONFIG=1
    else
        cp .env ".env.backup-$(date +%Y%m%d-%H%M%S)"
        ok "Backed up old .env"
    fi
fi

if [[ "$SKIP_CONFIG" -eq 0 ]]; then
    [[ -f .env.example ]] || die ".env.example missing — reinstall the Auditarr release."

    # ── Secret key ────────────────────────────────────────────
    step "Generating secret key"
    SECRET_KEY=$(openssl rand -hex 32)
    ok "32-byte secret_key generated"

    # ── Admin credentials ─────────────────────────────────────
    step "First admin user"
    note "These will be created on first boot. You can change them later in the UI."
    read -r -p "    Username (default: admin): " admin_user
    admin_user="${admin_user:-admin}"
    while :; do
        read -r -p "    Email: " admin_email
        [[ "$admin_email" == *@*.* ]] && break
        info "  Need a valid email."
    done
    while :; do
        read -r -s -p "    Password (min 16 chars): " admin_pw; echo
        read -r -s -p "    Confirm password: " admin_pw_confirm; echo
        if [[ "$admin_pw" != "$admin_pw_confirm" ]]; then
            info "  Passwords don't match."
            continue
        fi
        if [[ ${#admin_pw} -lt 16 ]]; then
            info "  Too short (${#admin_pw} chars; need ≥16)."
            continue
        fi
        break
    done
    ok "Admin user: $admin_user <$admin_email>"

    # ── Library paths ─────────────────────────────────────────
    step "Library bind mounts (optional)"
    note "These are host paths the Auditarr container will read from."
    note "You can add more later by editing docker-compose.override.yml."
    LIBRARY_PATHS=()
    while :; do
        read -r -p "    Library path (blank to finish): " p
        [[ -z "$p" ]] && break
        if [[ ! -d "$p" ]]; then
            info "  Path $p doesn't exist on this host. Add anyway? [y/N]"
            read -r reply
            [[ "${reply,,}" != "y" ]] && continue
        fi
        LIBRARY_PATHS+=("$p")
        ok "Added $p"
    done

    # ── Write .env ────────────────────────────────────────────
    step "Writing .env"
    cp .env.example .env

    # Use Python instead of sed -i so paths with slashes/special chars
    # are safe and we don't depend on GNU sed.
    SECRET_KEY="$SECRET_KEY" \
    ADMIN_USER="$admin_user" \
    ADMIN_EMAIL="$admin_email" \
    ADMIN_PW="$admin_pw" \
    python3 <<'PYTHON'
import os
import re

env = open(".env").read()


def replace_key(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(f"{key}={value}", text)
    return text + ("\n" if not text.endswith("\n") else "") + f"{key}={value}\n"


env = replace_key(env, "AUDITARR_SECRET_KEY", os.environ["SECRET_KEY"])
env = replace_key(env, "AUDITARR_BOOTSTRAP_ADMIN_USERNAME", os.environ["ADMIN_USER"])
env = replace_key(env, "AUDITARR_BOOTSTRAP_ADMIN_EMAIL", os.environ["ADMIN_EMAIL"])
env = replace_key(env, "AUDITARR_BOOTSTRAP_ADMIN_PASSWORD", os.environ["ADMIN_PW"])

with open(".env", "w") as f:
    f.write(env)
PYTHON
    chmod 600 .env
    ok ".env written (mode 600)"

    # ── docker-compose.override.yml for library mounts ────────
    if [[ ${#LIBRARY_PATHS[@]} -gt 0 ]]; then
        step "Generating docker-compose.override.yml for library mounts"
        {
            echo "# Auto-generated by install.sh"
            echo "# Adjust freely; re-running install.sh will offer to regenerate."
            echo "services:"
            echo "  app:"
            echo "    volumes:"
            i=1
            for p in "${LIBRARY_PATHS[@]}"; do
                echo "      - $p:/mnt/library-$i:ro"
                i=$((i + 1))
            done
        } > docker-compose.override.yml
        ok "${#LIBRARY_PATHS[@]} mount(s) configured (read-only)"
        note "In the UI, add these as libraries pointing at /mnt/library-1, /mnt/library-2, ..."
    fi
fi

# ── Bring up the stack ────────────────────────────────────────
step "Starting Auditarr"
note "This may take a few minutes on first run while images pull."
docker compose pull 2>&1 | sed 's/^/    /'
docker compose up -d 2>&1 | sed 's/^/    /'

# Optionally start the worker profile.
read -r -p "    Start the background worker profile too? [Y/n] " reply
if [[ "${reply,,}" != "n" ]]; then
    docker compose --profile worker up -d 2>&1 | sed 's/^/    /'
    ok "Worker started"
fi

# ── Done ──────────────────────────────────────────────────────
cat <<EOF

${GREEN}✓ Auditarr is up.${RESET}

  ${BOLD}Web UI:${RESET}      http://localhost:8000
  ${BOLD}Sign in as:${RESET}  ${admin_user:-(existing admin)}
  ${BOLD}Logs:${RESET}        docker compose logs -f app

Next steps:

  1. Open the UI and sign in.
  2. (Optional) Install the host updater helper:
       sudo cp docker/updater/auditarr-update.service /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl enable --now auditarr-update
  3. See docs/getting-started/installation.md for the full operator
     reference, or open Help & updates in the UI.

EOF
