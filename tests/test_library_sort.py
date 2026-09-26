"""Tests for `library_sort_key` (article/case/accent-insensitive sort keys)."""

from audio_to_subs.core.library_sort import library_sort_key


class TestLeadingArticles:
    """Leading English/French articles are stripped, unless nothing remains."""

    def test_strips_leading_english_the(self):
        """ "The Example Show" sorts as if it were "Example Show"."""
        assert library_sort_key("The Example Show") == library_sort_key("Example Show")

    def test_strips_leading_english_a(self):
        """A leading "A " is stripped."""
        assert library_sort_key("A Show") == library_sort_key("Show")

    def test_strips_leading_english_an(self):
        """A leading "An " is stripped."""
        assert library_sort_key("An Adventure") == library_sort_key("Adventure")

    def test_strips_leading_french_le(self):
        """A leading "Le " is stripped."""
        assert library_sort_key("Le Bureau") == library_sort_key("Bureau")

    def test_strips_leading_french_la(self):
        """A leading "La " is stripped."""
        assert library_sort_key("La Casa de Papel") == library_sort_key("Casa de Papel")

    def test_strips_leading_french_les(self):
        """A leading "Les " is stripped."""
        assert library_sort_key("Les Revenants") == library_sort_key("Revenants")

    def test_strips_leading_french_un(self):
        """A leading "Un " is stripped."""
        assert library_sort_key("Un Village Francais") == library_sort_key(
            "Village Francais"
        )

    def test_strips_leading_french_une(self):
        """A leading "Une " is stripped."""
        assert library_sort_key("Une Femme") == library_sort_key("Femme")

    def test_strips_elided_apostrophe_straight_quote(self):
        """Elided "L'Agence" (straight apostrophe) strips to "Agence"."""
        assert library_sort_key("L'Agence") == library_sort_key("Agence")

    def test_strips_elided_apostrophe_curly_quote(self):
        """Elided "L’Agence" (curly U+2019 apostrophe) strips to "Agence"."""
        assert library_sort_key("L’Agence") == library_sort_key("Agence")

    def test_straight_and_curly_apostrophe_are_equivalent(self):
        """Both apostrophe forms of the same elided name produce the same key."""
        assert library_sort_key("L'Agence") == library_sort_key("L’Agence")

    def test_article_only_name_is_kept_whole_the(self):
        """A name that is *only* the article "The" is not reduced to empty."""
        assert library_sort_key("The") == "the"

    def test_article_only_name_is_kept_whole_a(self):
        """A name that is *only* the article "A" is not reduced to empty."""
        assert library_sort_key("A") == "a"

    def test_article_only_name_is_kept_whole_les(self):
        """A name that is *only* the article "Les" is not reduced to empty."""
        assert library_sort_key("Les") == "les"

    def test_non_article_word_starting_like_article_is_not_stripped(self):
        """A word that merely starts with an article's letters (no boundary
        whitespace) is left untouched, e.g. "Theater" keeps its "the"."""
        assert library_sort_key("Theater") == "theater"

    def test_only_one_leading_article_is_stripped(self):
        """Only a single leading article is removed, not repeated ones: the
        second "The" is left in place rather than also being stripped."""
        assert library_sort_key("The The Band") == "the band"


class TestCaseInsensitivity:
    """Sort keys are case-insensitive (casefolded)."""

    def test_upper_and_lower_case_produce_same_key(self):
        assert library_sort_key("SHOW") == library_sort_key("show")

    def test_mixed_case_produces_same_key(self):
        assert library_sort_key("ShOwCaSe") == library_sort_key("showcase")


class TestAccentInsensitivity:
    """Sort keys strip combining marks so accented text sorts naturally."""

    def test_accented_and_plain_produce_same_key(self):
        assert library_sort_key("Année") == library_sort_key("Annee")

    def test_multiple_accents_are_stripped(self):
        assert library_sort_key("Élément") == library_sort_key("Element")


class TestNaturalNumberOrdering:
    """Digit runs are zero-padded so plain string comparison is numeric."""

    def test_two_digit_number_sorts_after_single_digit(self):
        assert library_sort_key("Show 2") < library_sort_key("Show 10")

    def test_single_digit_numbers_sort_in_numeric_order(self):
        assert library_sort_key("Show 1") < library_sort_key("Show 2")

    def test_large_gap_still_sorts_numerically(self):
        assert library_sort_key("Show 9") < library_sort_key("Show 100")

    def test_pre_existing_leading_zeros_do_not_break_ordering(self):
        """A name already written with leading zeros (e.g. "Episode 007")
        produces the same key as its unpadded equivalent ("Episode 7")."""
        assert library_sort_key("Episode 007") == library_sort_key("Episode 7")

    def test_multiple_digit_runs_are_each_padded(self):
        assert library_sort_key("S2 E10") < library_sort_key("S2 E100")
        assert library_sort_key("S1 E1") < library_sort_key("S2 E1")


class TestEndToEndSorting:
    """Realistic sorted() usage matches human expectations."""

    def test_article_insensitive_alphabetical_order_preserved(self):
        names = ["Avatar", "The Example Show", "Zeta"]
        assert sorted(names, key=library_sort_key) == names

    def test_shuffled_articles_sort_alphabetically_by_remainder(self):
        names = ["Zeta", "Avatar", "The Example Show"]
        assert sorted(names, key=library_sort_key) == [
            "Avatar",
            "The Example Show",
            "Zeta",
        ]

    def test_numeric_episode_titles_sort_naturally(self):
        names = ["Show 2", "Show 10", "Show 1"]
        assert sorted(names, key=library_sort_key) == ["Show 1", "Show 2", "Show 10"]

    def test_mixed_articles_case_and_numbers(self):
        names = ["le Bureau 2", "The Bureau 10", "Bureau 1"]
        # All three reduce to "bureau <n>" once the leading article is
        # stripped and case is folded, so they sort purely on the number.
        assert sorted(names, key=library_sort_key) == [
            "Bureau 1",
            "le Bureau 2",
            "The Bureau 10",
        ]
