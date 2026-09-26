"""Natural, article-insensitive sort keys for library display names.

Used to order the Bazarr-synced library (series and movie names) the way a
human expects: ignoring a leading English or French article, ignoring case
and accents, and treating embedded digit runs numerically so "Show 2" sorts
before "Show 10" rather than after it.

The key is a plain string so it can be stored in a column and used directly
in a SQL `ORDER BY` with no collation extension - see
`audio_to_subs/db/models.py` (`BazarrCache.sort_key`) and
`audio_to_subs/api/routes/wanted.py`.
"""

import re
import unicodedata

# One leading article, in English or French, followed by whitespace, or the
# elided French form "l'" (straight or U+2019 curly apostrophe) with no
# space. Matched case-insensitively against the already-casefolded name.
# Only stripped when something remains afterwards, so a name that *is* just
# an article (e.g. "A", "Les") is left whole instead of being reduced to an
# empty key.
_LEADING_ARTICLE_RE = re.compile(
    r"^(?:the|a|an|le|la|les|un|une)\s+(?=\S)|^l['’](?=\S)"
)

# A run of one or more ASCII digits, used to zero-pad numbers so that
# lexicographic (byte-wise) comparison agrees with numeric comparison.
_DIGITS_RE = re.compile(r"\d+")

# Wide enough that no realistic season/episode/year number in a title
# overflows it; padding is purely a sort-order trick and never displayed.
_DIGIT_PAD_WIDTH = 10


def library_sort_key(name: str) -> str:
    """Build a total-order sort key for a series or movie display name.

    Steps:
    1. Unicode-normalize (NFKD) and drop combining marks, so accented
       letters sort next to their unaccented form (e.g. "Année" ~ "Annee").
    2. Casefold and collapse internal whitespace.
    3. Strip one leading English/French article, if the name isn't only
       that article.
    4. Zero-pad every digit run to a fixed width, so plain string
       comparison gives natural numeric ordering.

    The result is for ordering only - never for display.
    """
    normalized = unicodedata.normalize("NFKD", name)
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    folded = " ".join(without_marks.casefold().split())

    stripped = _LEADING_ARTICLE_RE.sub("", folded, count=1)

    return _DIGITS_RE.sub(lambda m: m.group().zfill(_DIGIT_PAD_WIDTH), stripped)
