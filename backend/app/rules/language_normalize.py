"""Language code normalization (v1.9 Stage 4.1).

Operators write rules like ``audio_languages contains "en"`` and
expect the rule to match files whose ffprobe-derived audio
streams are tagged ``eng``, ``English``, ``en-US``, or any of the
other 20+ forms a particular muxer might use. Pre-1.9 the rules
engine did strict string equality, so ``"en"`` matched ONLY files
tagged exactly ``"en"`` — operators repeatedly hit cases where
identical-by-meaning languages wouldn't match.

The normalizer folds every common form to its ISO 639-1 code
when one exists, else to the lower-cased trimmed string. Both
sides of a comparison run through it at evaluation time:

  * the rule's expected value (typically operator-supplied like
    "en" or "English")
  * each element of the file's actual list (the ffprobe-tagged
    values, which can be anything from ``eng`` to ``en-US`` to
    ``Eng (5.1)``)

The mapping is curated, not generated — we deliberately don't
ship the entire ISO 639 database because:

  1. The 100-ish languages an Auditarr install actually
     encounters are a tiny subset.
  2. Aliases ("English", "eng", "en-US") are operator-facing
     and should be hand-picked, not algorithmically derived.
  3. New entries are added on demand as operators report
     mismatches — keeps the table understandable.

The same util powers the frontend Language picker (Stage 4.1's
auto-complete) via a parallel JS table; the two MUST stay in
sync.
"""

from __future__ import annotations

# Each key on the LEFT is a normalized canonical code (ISO 639-1).
# The list on the RIGHT is every alternate form an operator or a
# muxer might write. The lookup builds the reverse map.
#
# Ordering inside each list doesn't matter — the table is consumed
# only as a flat alias → canonical map.
_CANONICAL_TO_ALIASES: dict[str, list[str]] = {
    "en": ["en", "eng", "english", "en-us", "en-gb", "en_us", "en_gb", "en-au"],
    "es": ["es", "spa", "spanish", "español", "espanol", "es-es", "es-mx", "es-419"],
    "fr": ["fr", "fre", "fra", "french", "français", "francais", "fr-fr", "fr-ca"],
    "de": ["de", "ger", "deu", "german", "deutsch", "de-de", "de-at"],
    "it": ["it", "ita", "italian", "italiano"],
    "pt": ["pt", "por", "portuguese", "português", "portugues", "pt-br", "pt-pt"],
    "ja": ["ja", "jpn", "japanese", "日本語", "ja-jp"],
    "zh": [
        "zh",
        "chi",
        "zho",
        "chinese",
        "中文",
        "zh-cn",
        "zh-tw",
        "zh-hk",
        "zh-hans",
        "zh-hant",
        "cmn",
        "yue",
        "mandarin",
        "cantonese",
    ],
    "ko": ["ko", "kor", "korean", "한국어"],
    "ru": ["ru", "rus", "russian", "русский"],
    "ar": ["ar", "ara", "arabic"],
    "hi": ["hi", "hin", "hindi"],
    "nl": ["nl", "nld", "dut", "dutch", "nederlands"],
    "sv": ["sv", "swe", "swedish", "svenska"],
    "no": ["no", "nor", "norwegian", "nob", "nno", "norsk"],
    "da": ["da", "dan", "danish", "dansk"],
    "fi": ["fi", "fin", "finnish", "suomi"],
    "pl": ["pl", "pol", "polish", "polski"],
    "tr": ["tr", "tur", "turkish", "türkçe"],
    "he": ["he", "heb", "hebrew", "iw"],  # iw is the deprecated form
    "el": ["el", "gre", "ell", "greek"],
    "th": ["th", "tha", "thai"],
    "vi": ["vi", "vie", "vietnamese"],
    "uk": ["uk", "ukr", "ukrainian"],
    "cs": ["cs", "cze", "ces", "czech"],
    "hu": ["hu", "hun", "hungarian", "magyar"],
    "ro": ["ro", "rum", "ron", "romanian", "română"],
    "id": ["id", "ind", "indonesian"],
    "ms": ["ms", "may", "msa", "malay"],
    "fa": ["fa", "per", "fas", "persian", "farsi"],
}


def _build_alias_index() -> dict[str, str]:
    """Flatten _CANONICAL_TO_ALIASES into ``alias_lower → canonical``."""
    out: dict[str, str] = {}
    for canonical, aliases in _CANONICAL_TO_ALIASES.items():
        for alias in aliases:
            out[alias.strip().lower()] = canonical
        # The canonical itself is also a legal input.
        out[canonical.strip().lower()] = canonical
    return out


_ALIAS_INDEX: dict[str, str] = _build_alias_index()


def normalize_language(raw: str | None) -> str | None:
    """Fold a language identifier to its canonical ISO 639-1 code.

    Returns ``None`` for ``None`` / empty / whitespace-only input.
    Unknown inputs (anything not in the alias table) fall through
    to the lower-cased trimmed string — that way a future muxer's
    new spelling is comparable to itself, just not collapsed onto
    a canonical form.

    Side effects: none. Pure function, safe to call inside the
    rules engine's hot evaluation loop.
    """
    if raw is None:
        return None
    s = raw.strip().lower()
    if not s:
        return None
    # Strip subtag in form "en-US" — try the full string first, then
    # fall back to the primary tag. That lets us add explicit
    # entries for region variants if we ever need to (e.g. zh-TW
    # vs zh-CN) without losing the bare "zh" mapping.
    if s in _ALIAS_INDEX:
        return _ALIAS_INDEX[s]
    primary = s.split("-", 1)[0]
    if primary in _ALIAS_INDEX:
        return _ALIAS_INDEX[primary]
    return s


def normalize_languages(values: list[str] | None) -> list[str]:
    """Normalize a list of language identifiers, dropping
    None/empty entries. Order is preserved; duplicates that
    collapse to the same canonical code are deduplicated."""
    if not values:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not isinstance(v, str):
            continue
        n = normalize_language(v)
        if n is None or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


# Exposed so frontend codegen / docs can enumerate the canonical
# list without re-parsing the dict.
def canonical_languages() -> list[str]:
    """Return the sorted list of ISO 639-1 codes the normalizer
    knows about. Useful for the frontend Language picker."""
    return sorted(_CANONICAL_TO_ALIASES.keys())


__all__ = [
    "normalize_language",
    "normalize_languages",
    "canonical_languages",
]
