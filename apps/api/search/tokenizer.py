"""Tokenization for BM25 over mixed Chinese/English/code-identifier text.

Design choice (ratified in PRD V2.1): Chinese is tokenized as overlapping
character bigrams, not unigrams and not dictionary words.

* **Unigrams** would make every Chinese query match almost every document,
  because common characters are everywhere. BM25's IDF would then flatten and
  ranking quality collapses.
* **A dictionary** would need a bundled lexicon (a data dependency) and still
  mistokenizes code identifiers and product names, which are exactly what this
  corpus is full of.
* **Bigrams** are dependency-free, deterministic, good enough for exact-term
  recall, and leave *semantic* matching to the embedding path. The two paths are
  complementary by design: BM25 handles precise terms, vectors handle intent.

ASCII runs are split on non-alphanumeric boundaries and also on camelCase, so
``resourceVersionId`` and ``resource_version_id`` both yield the same tokens.
This makes code identifiers searchable regardless of the caller's casing style.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["detokenize_for_highlight", "tokenize"]

# A CJK ideograph block. Bigrams are formed only within a run of these.
_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_RUN_RE = re.compile(f"[{_CJK}]+")
# ASCII-ish word runs: letters, digits, underscore, hyphen.
_ASCII_RUN_RE = re.compile(r"[A-Za-z0-9_@./-]+")
# camelCase / PascalCase boundary: split before an uppercase that follows
# lowercase/digit, or before an uppercase followed by lowercase.
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _normalize_text(text: str) -> str:
    """NFKC-normalize and casefold so full-width and case differences vanish."""

    return unicodedata.normalize("NFKC", text).casefold()


def _normalize_ascii_run(run: str) -> str:
    """NFKC-normalize an ASCII run *without* casefolding.

    camelCase boundaries depend on case, so casefolding must happen after the
    split, not before it.
    """

    return unicodedata.normalize("NFKC", run)


def _split_ascii(run: str) -> list[str]:
    """Split one ASCII run into searchable terms."""

    terms: list[str] = []
    # Split identifiers on punctuation first (``resource_version_id``).
    for piece in re.split(r"[_@./\-]+", run):
        if not piece:
            continue
        # Then split camelCase (``resourceVersionId``).
        for part in _CAMEL_RE.split(piece):
            if part:
                terms.append(part)
    return terms


def tokenize(text: str) -> list[str]:
    """Return the token sequence for *text*, preserving order and repeats.

    Repeats matter: BM25 term frequency is computed per field, so callers must
    not deduplicate the result.
    """

    if not text:
        return []
    # NFKC only. Casefolding happens per ASCII run *after* camelCase splitting,
    # because splitting depends on case information.
    normalized = unicodedata.normalize("NFKC", text)
    tokens: list[str] = []
    position = 0
    length = len(normalized)

    while position < length:
        cjk = _CJK_RUN_RE.match(normalized, position)
        if cjk:
            run = cjk.group(0)
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
            position = cjk.end()
            continue
        ascii_run = _ASCII_RUN_RE.match(normalized, position)
        if ascii_run:
            # Split on case *before* casefolding, otherwise camelCase collapses
            # into one lowercase blob (``resourceVersionId`` -> ``resourceversionid``).
            tokens.extend(
                part.casefold()
                for part in _split_ascii(_normalize_ascii_run(ascii_run.group(0)))
            )
            position = ascii_run.end()
            continue
        position += 1

    return [token for token in tokens if token]


def detokenize_for_highlight(tokens: list[str]) -> str:
    """Best-effort readable text for a token list (used only for fixtures)."""

    return " ".join(tokens)
