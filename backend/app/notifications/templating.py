"""Notification templating.

Channels can override the default subject/body via the ``subject_template``
and ``body_template`` keys in their ``config``. Anything not overridden
falls back to the built-in default.

Templates render with Jinja2 in autoescape-off mode (these are plaintext
or markdown, not HTML — channel providers do their own escaping where
needed).

Variables available to templates:

  * ``severity``       — label (``warn``, ``high``, etc.)
  * ``severity_rank``  — numeric rank
  * ``rule_id``        — UUID of the rule that fired
  * ``rule_name``      — human-readable rule name
  * ``media_file_id``  — UUID of the file
  * ``path``           — file's absolute path
  * ``filename``       — base name
  * ``library_name``   — library the file belongs to
  * ``message``        — the rule's ``notify.message`` if set
  * ``time``           — ISO-8601 UTC timestamp
"""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateError

# StrictUndefined catches typos in templates loudly instead of producing
# silently-empty strings. We catch ``TemplateError`` in the dispatcher
# and fall back to the default rendering on failure.
_env = Environment(
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

DEFAULT_SUBJECT = "[Auditarr] {{ severity|upper }} · {{ rule_name }}"
DEFAULT_BODY = """\
{{ rule_name }} matched {{ filename }} (severity {{ severity }}).

File: {{ path }}
Library: {{ library_name }}
{% if message %}

{{ message }}
{% endif %}
"""


def render(template_source: str, variables: dict[str, Any]) -> str:
    """Render a template against ``variables``. Raises on missing keys."""
    return _env.from_string(template_source).render(**variables)


def render_subject(template: str | None, variables: dict[str, Any]) -> str:
    """Render the subject template, falling back to the default."""
    try:
        return render(template or DEFAULT_SUBJECT, variables)
    except TemplateError:
        # Fall back to the default if the operator's template is broken,
        # so we still deliver something rather than dropping the alert.
        return render(DEFAULT_SUBJECT, variables)


def render_body(template: str | None, variables: dict[str, Any]) -> str:
    """Render the body template, falling back to the default."""
    try:
        return render(template or DEFAULT_BODY, variables)
    except TemplateError:
        return render(DEFAULT_BODY, variables)
