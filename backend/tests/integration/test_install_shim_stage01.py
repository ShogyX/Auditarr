"""Stage 01 — verify the install.sh -> install-docker.sh rename.

We can't run the installers end-to-end (they need docker, sudo,
real systemd units), but we can:

1. Confirm both files exist at the right paths.
2. Confirm ``bash -n`` parses both without syntax errors.
3. Confirm the shim actually exits non-zero (64) and prints the
   rename notice — so an operator running ``./install.sh`` out of
   muscle memory cannot silently chain into the real installer.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Resolve the repo root: tests/integration/<file>.py -> repo root is
# three parents up (this file -> integration -> tests -> backend -> repo).
REPO_ROOT = Path(__file__).resolve().parents[3]


def _bash_available() -> bool:
    return shutil.which("bash") is not None


@pytest.mark.skipif(not _bash_available(), reason="bash not on PATH")
def test_install_docker_script_exists_and_parses() -> None:
    script = REPO_ROOT / "install-docker.sh"
    assert script.is_file(), f"install-docker.sh missing at {script}"
    # Syntax-check only — does not execute.
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"install-docker.sh failed bash -n:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )


@pytest.mark.skipif(not _bash_available(), reason="bash not on PATH")
def test_install_sh_shim_exists_and_parses() -> None:
    script = REPO_ROOT / "install.sh"
    assert script.is_file(), f"install.sh shim missing at {script}"
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"install.sh shim failed bash -n:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )


@pytest.mark.skipif(not _bash_available(), reason="bash not on PATH")
def test_install_sh_shim_exits_64_with_rename_notice() -> None:
    """The shim must NOT silently chain into install-docker.sh.

    It prints a clear rename notice on stderr and exits 64 so the
    operator notices the new name. (64 = EX_USAGE from sysexits.h —
    a conventional "command-line usage" exit code.)
    """
    script = REPO_ROOT / "install.sh"
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 64, (
        f"install.sh shim should exit 64, got {result.returncode}"
    )
    combined = result.stdout + result.stderr
    assert "install-docker.sh" in combined, (
        "Shim must mention the new script name. Output:\n" + combined
    )
    assert "renamed" in combined.lower(), (
        "Shim must announce the rename. Output:\n" + combined
    )


@pytest.mark.skipif(not _bash_available(), reason="bash not on PATH")
def test_install_docker_banner_no_longer_says_v1_0() -> None:
    """The legacy banner said 'Auditarr v1.0 setup'. The rename came
    with a banner refresh — make sure the old string is gone."""
    text = (REPO_ROOT / "install-docker.sh").read_text()
    assert "v1.0 setup" not in text, (
        "install-docker.sh still contains the legacy 'v1.0 setup' banner"
    )
    assert "Docker setup" in text, (
        "install-docker.sh banner should read 'Docker setup'"
    )


@pytest.mark.skipif(not _bash_available(), reason="bash not on PATH")
def test_install_bare_metal_still_parses() -> None:
    """Sanity: the bare-metal script's Stage 01 edits didn't break syntax."""
    script = REPO_ROOT / "install-bare-metal.sh"
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"install-bare-metal.sh failed bash -n:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
