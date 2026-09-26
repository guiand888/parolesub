"""Parsing for the Wanted list's free-text search box.

The Wanted list's ``search`` query parameter doubles as a title search and
an episode-code shorthand (e.g. "S04E01", "4x01") so a user can jump
straight to a specific season/episode without scrolling. This module is
pure and has no FastAPI or SQLAlchemy dependency - see
``audio_to_subs/api/routes/wanted.py`` for how the result is turned into
query filters.
"""

import re
from dataclasses import dataclass

# Recognizes exactly one episode-code token, case-insensitively, in either
# of two forms:
#   - "S04", "S04E01"  -> S(\d{1,4}) with an optional E(\d{1,4})
#   - "4x01"           -> (\d{1,4})X(\d{2,4})
# Both alternatives are word-bounded on both ends so a code embedded in a
# larger word (e.g. "Season04extra") is left untouched. Groups 1-2 belong to
# the "S..E.." form, groups 3-4 to the "NxN" form; whichever alternative
# matches leaves the other pair of groups as None.
#
# The "NxN" episode half requires >= 2 digits (scene-style zero-padding,
# e.g. "4x01"/"4x10"), not 1 (e.g. "4x1"). A single-digit episode there is
# indistinguishable from a plain number-by-number title: without this,
# searching for the movie "4x4" silently parsed as season=4/episode=4 and
# was then excluded from its own search results, since a parsed season
# forces `kind == "episode"` in list_wanted (api/routes/wanted.py). The
# "S.."-prefixed form doesn't need the same guard: a leading literal "S" is
# a much stronger, less accidental signal of intent.
_EPISODE_CODE_RE = re.compile(
    r"\bS(\d{1,4})(?:E(\d{1,4}))?\b|\b(\d{1,4})X(\d{2,4})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WantedSearch:
    """Parsed form of a Wanted-list search query."""

    text: str | None
    season: int | None
    episode: int | None


def parse_wanted_search(query: str) -> WantedSearch:
    """Split a Wanted-list search query into free text plus an episode code.

    Args:
        query: The raw search string typed by the user.

    Returns:
        A ``WantedSearch`` with the episode code (if any) removed from
        ``text``. Never raises - malformed input just falls back to
        treating the whole (trimmed) query as free text.
    """
    if not query:
        return WantedSearch(text=None, season=None, episode=None)

    match = _EPISODE_CODE_RE.search(query)
    if match is None:
        text = " ".join(query.split())
        return WantedSearch(text=text or None, season=None, episode=None)

    remaining = query[: match.start()] + " " + query[match.end() :]
    text = " ".join(remaining.split())

    season_s, episode_s, season_x, episode_x = match.groups()
    season: int | None
    episode: int | None
    if season_s is not None:
        season = int(season_s)
        episode = int(episode_s) if episode_s is not None else None
    else:
        season = int(season_x) if season_x is not None else None
        episode = int(episode_x) if episode_x is not None else None

    return WantedSearch(text=text or None, season=season, episode=episode)
