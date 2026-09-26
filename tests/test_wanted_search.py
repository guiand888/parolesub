"""Tests for the Wanted list's search-query parser."""

import pytest

from audio_to_subs.api.services.wanted_search import WantedSearch, parse_wanted_search


class TestParseWantedSearch:
    """Unit tests for parse_wanted_search."""

    def test_season_and_episode_code_alone(self):
        assert parse_wanted_search("S04E01") == WantedSearch(
            text=None, season=4, episode=1
        )

    def test_season_and_episode_code_lowercase(self):
        assert parse_wanted_search("s4e1") == WantedSearch(
            text=None, season=4, episode=1
        )

    def test_nxn_code_alone(self):
        assert parse_wanted_search("4x01") == WantedSearch(
            text=None, season=4, episode=1
        )

    def test_nxn_code_uppercase_x(self):
        assert parse_wanted_search("4X01") == WantedSearch(
            text=None, season=4, episode=1
        )

    def test_season_only_code_with_trailing_text(self):
        assert parse_wanted_search("agency s04") == WantedSearch(
            text="agency", season=4, episode=None
        )

    def test_season_and_episode_code_with_leading_text(self):
        assert parse_wanted_search("agency S04E01") == WantedSearch(
            text="agency", season=4, episode=1
        )

    def test_plain_text_query_with_no_code(self):
        assert parse_wanted_search("The Parisian Agency") == WantedSearch(
            text="The Parisian Agency", season=None, episode=None
        )

    def test_empty_string(self):
        assert parse_wanted_search("") == WantedSearch(
            text=None, season=None, episode=None
        )

    def test_whitespace_only_string(self):
        assert parse_wanted_search("   ") == WantedSearch(
            text=None, season=None, episode=None
        )

    def test_season_only_code_alone(self):
        """A bare "S04" (no episode) sets season only."""
        assert parse_wanted_search("S04") == WantedSearch(
            text=None, season=4, episode=None
        )

    def test_case_variation_mixed(self):
        assert parse_wanted_search("aGeNcY S04e01") == WantedSearch(
            text="aGeNcY", season=4, episode=1
        )

    def test_whitespace_collapsing_around_code(self):
        assert parse_wanted_search("agency   s04   extra") == WantedSearch(
            text="agency extra", season=4, episode=None
        )

    def test_whitespace_collapsing_no_code(self):
        assert parse_wanted_search("  the   parisian   agency  ") == WantedSearch(
            text="the parisian agency", season=None, episode=None
        )

    def test_code_embedded_in_larger_word_is_not_matched(self):
        """A code glued onto surrounding letters isn't word-bounded, so it's
        left as plain text rather than being parsed as a season/episode."""
        result = parse_wanted_search("Season04Special")
        assert result.season is None
        assert result.episode is None
        assert result.text == "Season04Special"

    def test_trailing_episode_code(self):
        assert parse_wanted_search("agency S01E02") == WantedSearch(
            text="agency", season=1, episode=2
        )

    def test_four_digit_season_and_episode(self):
        assert parse_wanted_search("S1000E9999") == WantedSearch(
            text=None, season=1000, episode=9999
        )

    def test_nxn_code_with_surrounding_text(self):
        assert parse_wanted_search("the office 4x01 pilot") == WantedSearch(
            text="the office pilot", season=4, episode=1
        )

    @pytest.mark.parametrize("query", ["S04E01", "s04e01", "S4E1", "s4E1"])
    def test_case_insensitivity_matrix(self, query):
        result = parse_wanted_search(query)
        assert result.season == 4
        assert result.episode == 1
        assert result.text is None

    def test_never_raises_on_odd_input(self):
        # No exceptions for malformed/odd input - worst case the whole
        # string becomes text. Assert the actual parsed values too (not
        # just that a WantedSearch came back), since a "no season" or
        # "wrong season" result would silently misroute the search filter
        # despite still passing an isinstance-only check.
        result = parse_wanted_search("!!!s0000e0000???")
        assert result == WantedSearch(text="!!! ???", season=0, episode=0)

    def test_nxn_single_digit_episode_is_not_a_code(self):
        """ "4x4" is a real movie title (two films of that exact name), not
        an episode code. The NxN form requires >= 2 digits on the episode
        side (scene-style zero-padding, e.g. "4x01") specifically so a
        search for it isn't misparsed as season=4/episode=4 and then
        excluded from its own search results (a parsed season forces
        kind == "episode" in list_wanted)."""
        assert parse_wanted_search("4x4") == WantedSearch(
            text="4x4", season=None, episode=None
        )

    def test_nxn_single_digit_episode_within_a_title_is_not_a_code(self):
        assert parse_wanted_search("4x4 movie") == WantedSearch(
            text="4x4 movie", season=None, episode=None
        )
