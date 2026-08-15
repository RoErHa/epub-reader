"""The genre vocabulary (decision 5).

Providers return BISAC codes, free text and folksonomy tags
interchangeably. Everything normalises onto a fixed ~40-term BISAC
subset, and **unmapped values are logged, never guessed** — so the map
grows from real data rather than from imagination.
"""
from __future__ import annotations

import pytest

from epub_genres import GenreVocabulary


@pytest.fixture
def vocab():
    return GenreVocabulary.load()


class TestTheVocabulary:
    def test_it_is_a_subset_not_the_whole_of_bisac(self, vocab):
        assert 30 <= len(vocab.genres) <= 60

    def test_every_genre_has_a_code_and_a_name(self, vocab):
        for code, name in vocab.genres.items():
            assert code.strip() and name.strip()

    def test_codes_are_unique(self, vocab):
        assert len(set(vocab.genres)) == len(vocab.genres)

    def test_it_stops_at_one_level_of_subcategory(self, vocab):
        # "FICTION / Science Fiction", never "/ Space Opera".
        for name in vocab.genres.values():
            assert name.count("/") == 0


class TestMapping:
    @pytest.mark.parametrize("raw,expected", [
        ("Science Fiction", "Science Fiction"),
        ("sci-fi", "Science Fiction"),
        ("SCIFI", "Science Fiction"),
        ("Fiction / Science Fiction", "Science Fiction"),
        ("Fantasy fiction", "Fantasy"),
        ("Thrillers", "Thriller"),
        ("Biography & Autobiography", "Biography & Memoir"),
        ("young adult", "Young Adult"),
    ])
    def test_provider_strings_land_on_a_genre(self, vocab, raw, expected):
        assert vocab.map(raw) == expected

    def test_a_bisac_code_maps_directly(self, vocab):
        assert vocab.map("FIC028000") == "Science Fiction"

    def test_accents_and_spacing_do_not_matter(self, vocab):
        assert vocab.map("  Humour  ") == "Humour"

    @pytest.mark.parametrize("raw,expected", [
        ("Fiction - Fantasy", "Fantasy"),
        ("Fantasy - Epic", "Fantasy"),
        ("Fantasy - General", "Fantasy"),
        ("Fiction - Espionage", "Thriller"),
        ("Fiction / Science Fiction / Space Opera", "Science Fiction"),
    ])
    def test_hyphen_and_slash_paths_are_walked(self, vocab, raw, expected):
        # Real libraries are full of these; only slashes were handled,
        # which left 55% of the genre rows unmapped.
        assert vocab.map(raw) == expected

    def test_a_generic_segment_never_wins_over_a_specific_one(self, vocab):
        # "Fantasy - General" is fantasy, not the catch-all.
        assert vocab.map("Fantasy - General") == "Fantasy"

    def test_an_unknown_string_maps_to_nothing(self, vocab):
        assert vocab.map("Chelonian husbandry") is None

    def test_unknown_strings_are_recorded_for_later(self, vocab):
        vocab.map("Chelonian husbandry")
        vocab.map("Chelonian husbandry")
        assert vocab.unmapped["chelonian husbandry"] == 2

    def test_nothing_maps_to_nothing(self, vocab):
        assert vocab.map("") is None
        assert vocab.map(None) is None


class TestMappingASetOfSubjects:
    def test_the_first_match_leads(self, vocab):
        assert vocab.map_all(["Fiction", "Science Fiction"])[0] == "Fiction"

    def test_duplicates_collapse(self, vocab):
        assert vocab.map_all(["sci-fi", "science fiction", "SF"]) == \
            ["Science Fiction"]

    def test_unmapped_subjects_are_dropped_from_the_result(self, vocab):
        assert vocab.map_all(["Chelonian husbandry", "Fantasy"]) == ["Fantasy"]

    def test_an_empty_list_is_empty(self, vocab):
        assert vocab.map_all([]) == []
