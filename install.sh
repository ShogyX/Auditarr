#!/usr/bin/env bash
# Auditarr — install.sh has been renamed.
#
# Starting with v1.7 the Docker installer is named
# `install-docker.sh` so it's unambiguous next to the bare-metal
# installer (`install-bare-metal.sh`). This shim exists only to tell
# operators about the rename — it does not chain into the real
# installer, because silently chaining would hide the new name from
# anyone running `./install.sh` out of muscle memory.

set -u

if [[ -t 1 ]]; then
    BOLD=$'\033[1m'
    YELLOW=$'\033[33m'
    RESET=$'\033[0m'
else
    BOLD=""; YELLOW=""; RESET=""
fi

cat >&2 <<EOF

${YELLOW}${BOLD}install.sh has been renamed to install-docker.sh.${RESET}

  For a Docker install:        ./install-docker.sh
  For a bare-metal install:    ./install-bare-metal.sh

  Re-run the command above.

EOF

exit 64
