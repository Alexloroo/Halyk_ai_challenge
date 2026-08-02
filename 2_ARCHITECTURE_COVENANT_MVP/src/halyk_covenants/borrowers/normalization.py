from __future__ import annotations

import re
import unicodedata

_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
_LEGAL_FORMS = frozenset(
    {
        "тоо",
        "ао",
        "ип",
        "llp",
        "ltd",
        "limited",
        "inc",
        "corp",
        "corporation",
        "jsc",
    }
)


def normalize_name(value: str, *, strip_legal_form: bool = False) -> str:
    """Normalize an entity name without transliterating or inventing identity."""
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    normalized = _PUNCTUATION.sub(" ", normalized)
    tokens = _WHITESPACE.sub(" ", normalized).strip().split()
    if strip_legal_form:
        tokens = [token for token in tokens if token not in _LEGAL_FORMS]
    return " ".join(tokens)


def normalize_identifier_key(value: str) -> str:
    return normalize_name(value).replace(" ", "").upper()


def normalize_identifier_value(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()
