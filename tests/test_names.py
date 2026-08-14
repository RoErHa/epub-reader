"""Author names and title sort keys.

30% of the real library writes authors as "Last, First", and the rest as
"First Last". Browsing by author is unusable unless both collapse to one
sort key, so this is the most load-bearing pure logic in the project.

Sort keys are computed once at ingest and stored, so browsing is a plain
indexed ORDER BY (design §7).
"""
from __future__ import annotations

import pytest

from epub_names import PersonName, title_sort_key


class TestAlreadyInverted:
    @pytest.mark.parametrize("raw,display,sort", [
        ("Ludlum, Robert", "Robert Ludlum", "ludlum, robert"),
        ("Tolkien, J.R.R.", "J.R.R. Tolkien", "tolkien, j.r.r."),
        ("Hobb, Robin", "Robin Hobb", "hobb, robin"),
        ("Green, Simon R", "Simon R Green", "green, simon r"),
    ])
    def test_last_first_is_understood(self, raw, display, sort):
        name = PersonName.parse(raw)
        assert name.display == display
        assert name.sort == sort


class TestNaturalOrder:
    @pytest.mark.parametrize("raw,sort", [
        ("Robert Ludlum", "ludlum, robert"),
        ("Ursula K. Le Guin", "le guin, ursula k."),
        ("J.R.R. Tolkien", "tolkien, j.r.r."),
        ("Frank Herbert", "herbert, frank"),
    ])
    def test_first_last_is_inverted_for_sorting(self, raw, sort):
        assert PersonName.parse(raw).sort == sort

    def test_display_is_left_alone(self):
        assert PersonName.parse("Ursula K. Le Guin").display == "Ursula K. Le Guin"


class TestParticles:
    @pytest.mark.parametrize("raw,sort", [
        ("Ursula K. Le Guin", "le guin, ursula k."),
        ("Simone de Beauvoir", "de beauvoir, simone"),
        ("Jan van der Meer", "van der Meer, jan".lower()),
        ("Ludwig van Beethoven", "van beethoven, ludwig"),
        ("Vincent van Gogh", "van gogh, vincent"),
    ])
    def test_particles_stay_with_the_surname(self, raw, sort):
        assert PersonName.parse(raw).sort == sort


class TestSuffixes:
    @pytest.mark.parametrize("raw,sort", [
        ("Martin Luther King Jr.", "king, martin luther jr."),
        ("John Smith III", "smith, john iii"),
    ])
    def test_suffixes_do_not_take_the_sort_position(self, raw, sort):
        assert PersonName.parse(raw).sort == sort


class TestAwkwardInput:
    def test_a_single_word_name_is_left_as_is(self):
        name = PersonName.parse("Homer")
        assert name.display == "Homer"
        assert name.sort == "homer"

    def test_whitespace_is_collapsed(self):
        assert PersonName.parse("  Robert   Ludlum  ").display == "Robert Ludlum"

    def test_empty_input_is_refused(self):
        for value in ("", "   ", None):
            assert PersonName.parse(value) is None

    def test_unknown_is_treated_as_no_author(self):
        for value in ("Unknown", "unknown", "Anonymous", "N/A"):
            assert PersonName.parse(value) is None

    def test_diacritics_fold_in_the_sort_key_only(self):
        name = PersonName.parse("Émile Zola")
        assert name.sort == "zola, emile"
        assert name.display == "Émile Zola"

    def test_spanish_double_surnames_are_a_known_limitation(self):
        # "Gabriel García Márquez" has two surnames, but "Mary Jane
        # Watson" has a middle name, and nothing in the string
        # distinguishes them — it needs a name database, not a rule.
        # We take the last word, and record that here rather than
        # pretend otherwise. A manual sort-name override is the fix.
        name = PersonName.parse("Gabriel García Márquez")
        assert name.display == "Gabriel García Márquez"
        assert name.sort == "marquez, gabriel garcia"

    def test_multiple_authors_are_split(self):
        names = PersonName.parse_many("Neil Gaiman & Terry Pratchett")
        assert [n.display for n in names] == ["Neil Gaiman", "Terry Pratchett"]

    def test_semicolons_and_and_also_split(self):
        assert len(PersonName.parse_many("A Smith; B Jones")) == 2
        assert len(PersonName.parse_many("A Smith and B Jones")) == 2

    def test_a_comma_name_is_not_split_as_two_authors(self):
        # "Ludlum, Robert" is one person, not two.
        names = PersonName.parse_many("Ludlum, Robert")
        assert [n.display for n in names] == ["Robert Ludlum"]


class TestTitleSort:
    @pytest.mark.parametrize("title,expected", [
        ("The Left Hand of Darkness", "left hand of darkness"),
        ("A Wizard of Earthsea", "wizard of earthsea"),
        ("An Evil Guest", "evil guest"),
        ("De Ontdekking van de Hemel", "ontdekking van de hemel"),
        ("Het Diner", "diner"),
        ("Le Petit Prince", "petit prince"),
        ("La Peste", "peste"),
    ])
    def test_leading_articles_are_dropped(self, title, expected):
        assert title_sort_key(title) == expected

    def test_a_title_that_is_only_an_article_is_kept(self):
        assert title_sort_key("The") == "the"

    def test_diacritics_are_folded(self):
        assert title_sort_key("Ángeles") == "angeles"

    def test_punctuation_is_dropped_but_words_stay_apart(self):
        assert title_sort_key("Hitchhiker's Guide, The") == "hitchhikers guide, the"

    def test_empty_title_is_empty(self):
        assert title_sort_key("") == ""
        assert title_sort_key(None) == ""
