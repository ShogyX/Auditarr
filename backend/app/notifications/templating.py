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
  * ``auto_delete``    — Stage 06 (v1.7): True when the rule that
                         fired this notification ALSO contains a
                         ``delete`` action. The default body
                         template renders a "No action required —
                         the file is being deleted" badge when
                         this is truthy (plan §359-360).
"""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateError, select_autoescape

# StrictUndefined catches typos in templates loudly instead of producing
# silently-empty strings. We catch ``TemplateError`` in the dispatcher
# and fall back to the default rendering on failure.
#
# autoescape: current providers (SMTP text body, JSON webhook payloads)
# don't HTML-render the output, so escaping is a no-op functionally.
# We enable ``select_autoescape`` anyway as defence-in-depth — a future
# plugin that decides to set ``html_body`` from the same template
# would otherwise be a silent XSS foot-gun. The autoescape filter
# only fires for ``.html`` / ``.xml`` template names; the bare-string
# templates used here keep their current behaviour.
_env = Environment(
    autoescape=select_autoescape(("html", "htm", "xml")),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

DEFAULT_SUBJECT = "[Auditarr] {{ severity|upper }} · {{ rule_name }}"
# Stage 06 (v1.7) — when the same rule that fired the notification
# also has a ``delete`` action attached, the dispatcher passes
# ``auto_delete: True`` in the variables. The body template
# inserts a clearly-visible badge so the operator's eye doesn't
# slide off thinking "I need to go investigate this" when in fact
# the file has already been moved to trash.
DEFAULT_BODY = """\
{{ rule_name }} matched {{ filename }} (severity {{ severity }}).

File: {{ path }}
Library: {{ library_name }}
{% if auto_delete %}

[Auto-delete] No action required — the file is being deleted by this rule.
{% endif %}
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
