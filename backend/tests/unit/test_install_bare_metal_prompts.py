"""Stage 01 — structural assertions about install-bare-metal.sh.

We can't drive interactive ``read -r`` prompts from pytest, so we
verify the script's *shape*: the helpers do what the plan says, and
the long-running steps print operator-visible notices.

The plan's contract:

* In interactive mode (``NON_INTERACTIVE != 1``), ``prompt()`` and
  ``prompt_secret()`` must look up the env var and use it as a
  *default* — not a silent override. Concretely, the function body
  must contain both an env-var lookup (``${!envvar``) AND a
  ``read -r -p`` for interactive input — so we can be sure the
  operator is still asked.

* Before the ``pip install`` step, the script prints an explicit
  "3-5 minutes" wait notice.

* In interactive mode, creating the first admin user does *not*
  require ``AUDITARR_ADMIN_PASSWORD`` to be pre-set — the admin
  block must be reachable without that env var.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "install-bare-metal.sh"


def _function_body(text: str, name: str) -> str:
    """Extract the body of a bash function ``name() { ... }``.

    We use a deliberately simple parser that looks for the
    function header and walks brace depth. It's enough for the
    style of bash in this repo (no nested anonymous heredocs that
    contain unbalanced braces).
    """
    header_re = re.compile(rf"^{re.escape(name)}\(\)\s*\{{\s*$", re.MULTILINE)
    match = header_re.search(text)
    assert match, f"could not find {name}() definition"
    start = match.end()
    depth = 1
    i = start
    while i < len(text) and depth > 0:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    raise AssertionError(f"unterminated {name}() body")


def test_prompt_helper_consults_env_var_and_reads_in_interactive_mode() -> None:
    text = SCRIPT.read_text()
    body = _function_body(text, "prompt")
    # Both halves: env var lookup, AND a terminal read with the
    # ``-p`` prompt flag.
    assert re.search(r"\$\{!envvar[:-]?[^}]*\}", body) or "${!envvar" in body, (
        "prompt() does not appear to look up the env var"
    )
    assert "read -r -p" in body, (
        "prompt() does not interactively read from the terminal"
    )


def test_prompt_secret_helper_consults_env_var_and_reads_in_interactive_mode() -> None:
    text = SCRIPT.read_text()
    body = _function_body(text, "prompt_secret")
    assert "${!envvar" in body, (
        "prompt_secret() does not appear to look up the env var"
    )
    assert "read -r -s -p" in body, (
        "prompt_secret() does not interactively read a secret"
    )
    # Must NOT echo the secret default in interactive mode. We
    # approximate this by asserting the prompt label uses
    # ``[unchanged]`` rather than the actual value.
    assert "[unchanged]" in body, (
        "prompt_secret() should display [unchanged] rather than the value"
    )


def test_prompt_helper_shows_default_prefix_in_interactive_mode() -> None:
    """The plan says: show ``[default: <value>]`` when the env var is
    set, so the operator can see what they'd accept by pressing Enter."""
    text = SCRIPT.read_text()
    body = _function_body(text, "prompt")
    assert "default:" in body, (
        "prompt() should label its default with 'default:'"
    )


def test_venv_step_prints_3_5_minute_wait_notice() -> None:
    text = SCRIPT.read_text()
    # The plan asks for the literal "3–5 minutes" (en-dash). Accept
    # the ASCII fallback "3-5 minutes" too, since some shells munge
    # non-ASCII.
    assert ("3–5 minutes" in text) or ("3-5 minutes" in text), (
        "install-bare-metal.sh is missing the '3-5 minutes' wait notice "
        "before the pip install step"
    )


def test_admin_block_does_not_require_password_env_var_in_interactive_mode() -> None:
    """The admin-creation block must be reachable in interactive mode
    without the env var pre-set. Operationally we assert that the
    block uses ``prompt_secret`` for the password (which itself
    handles the absent-env-var case in interactive mode) rather than
    branching on the env var being non-empty."""
    text = SCRIPT.read_text()
    # Find the admin step header and the next ~80 lines.
    marker = 'step "Creating first admin user"'
    idx = text.find(marker)
    assert idx >= 0, "admin-user step header not found"
    block = text[idx : idx + 4000]
    assert "prompt_secret" in block, (
        "admin-user block should use prompt_secret for the password"
    )
    # And it must NOT die immediately because the env var is empty
    # in interactive mode — i.e. there is no
    # ``[[ -z "${AUDITARR_ADMIN_PASSWORD:-}" ]] && die`` guard.
    forbidden = re.search(
        r'\[\[\s*-z\s+"\$\{?AUDITARR_ADMIN_PASSWORD[^}]*\}?"\s*\]\]\s*&&\s*die',
        block,
    )
    assert forbidden is None, (
        "admin block die-s when AUDITARR_ADMIN_PASSWORD is unset — "
        "should fall through to prompt_secret in interactive mode"
    )


def test_admin_password_confirm_loop_present_in_interactive_mode() -> None:
    """Addendum A.9 — interactive admin password is asked twice with
    confirm-match and re-prompted on mismatch / too short."""
    text = SCRIPT.read_text()
    idx = text.find('step "Creating first admin user"')
    assert idx >= 0
    block = text[idx : idx + 4000]
    # Two prompt_secret calls — primary + confirm.
    assert block.count("prompt_secret 'Admin password") >= 1, "missing primary password prompt"
    assert "Confirm admin password" in block, (
        "interactive admin password should have a confirmation prompt"
    )
    # A length check inside a retry loop.
    assert "Passwords didn't match" in block or "Passwords don't match" in block, (
        "interactive admin password should re-prompt on mismatch"
    )
    assert "Too short" in block, (
        "interactive admin password should re-prompt when < 12 chars"
    )


def test_bare_metal_banner_points_at_install_docker() -> None:
    """When the user reads the bare-metal banner and they're actually
    running Docker, the suggested fallback should be the new name."""
    text = SCRIPT.read_text()
    assert "./install-docker.sh" in text, (
        "bare-metal banner should reference ./install-docker.sh, not the "
        "legacy ./install.sh"
    )
