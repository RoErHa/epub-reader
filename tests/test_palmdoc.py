"""A PalmDOC book, handed to the reader as a MOBI.

Six PRC files here are the older PalmDB container — `TEXtREAd`, a bare
PalmDOC header and no MOBI header — holding Mobipocket's HTML all the
same. foliate opens only `BOOKMOBI` and said "File type not supported".
So the container is relabelled and given the MOBI header foliate reads,
on the way out, as a RAR comic is packed as a zip (§8.15). The text
records are not touched, and neither is the file.
"""
from __future__ import annotations

import struct

import pytest

from epub_palmdoc import PalmDoc
from tests.mobi_fixtures import MobiBuilder, PalmDocBuilder


def records(data: bytes) -> list[bytes]:
    count = struct.unpack(">H", data[76:78])[0]
    offsets = [struct.unpack(">I", data[78 + 8 * i:82 + 8 * i])[0]
               for i in range(count)] + [len(data)]
    return [data[offsets[i]:offsets[i + 1]] for i in range(count)]


LONG = "<html><body>" + "<p>A sentence of the book.</p>" * 400 + "</body></html>"


@pytest.fixture
def palm(tmp_path):
    return PalmDocBuilder(title="Wintertide", text=LONG).build(
        tmp_path / "wintertide.prc")


class TestRecognising:
    def test_a_palmdoc_is_one(self, palm):
        assert PalmDoc.is_palmdoc(palm)

    def test_a_mobi_is_not(self, tmp_path):
        assert not PalmDoc.is_palmdoc(MobiBuilder().build(tmp_path / "m.mobi"))

    def test_a_directory_or_a_stranger_is_not(self, tmp_path):
        (tmp_path / "x.prc").write_bytes(b"short")
        assert not PalmDoc.is_palmdoc(tmp_path / "x.prc")
        assert not PalmDoc.is_palmdoc(tmp_path)


class TestAsMobi:
    def test_it_announces_itself_as_a_mobi(self, palm):
        out = PalmDoc(palm).as_mobi()
        assert out[60:68] == b"BOOKMOBI"

    def test_record_zero_carries_the_mobi_header_foliate_reads(self, palm):
        zero = records(PalmDoc(palm).as_mobi())[0]
        assert zero[16:20] == b"MOBI"
        length, = struct.unpack(">I", zero[20:24])
        version, = struct.unpack(">I", zero[36:40])
        encoding, = struct.unpack(">I", zero[28:32])
        assert (length, version, encoding) == (232, 6, 1252)
        title_at, title_len = struct.unpack(">II", zero[84:92])
        assert zero[title_at:title_at + title_len] == b"Wintertide"
        # No EXTH, no index, no huffman tables, no trailing entries.
        assert struct.unpack(">I", zero[128:132])[0] & 0x40 == 0
        assert struct.unpack(">I", zero[244:248])[0] == 0xFFFFFFFF
        assert struct.unpack(">I", zero[240:244])[0] == 0

    def test_the_palmdoc_header_is_kept(self, palm):
        before = records(palm.read_bytes())[0][:12]
        after = records(PalmDoc(palm).as_mobi())[0][:12]
        assert before == after

    def test_pictures_are_looked_for_after_the_text(self, palm):
        zero = records(PalmDoc(palm).as_mobi())[0]
        texts, = struct.unpack(">H", zero[8:10])
        assert struct.unpack(">I", zero[108:112])[0] == texts + 1

    def test_the_text_records_are_untouched(self, palm):
        before, after = records(palm.read_bytes()), records(
            PalmDoc(palm).as_mobi())
        assert len(before) == len(after) > 2
        assert before[1:] == after[1:]

    def test_the_file_is_untouched(self, palm):
        original = palm.read_bytes()
        PalmDoc(palm).as_mobi()
        assert palm.read_bytes() == original


class TestServing:
    @pytest.fixture
    def book(self, library, gateway, client):
        PalmDocBuilder(title="Old Palm").build(library / "old-palm.prc")
        gateway.reindex()
        return next(b for b in client.get("/api/books?limit=500").json()["books"]
                    if b["path"] == "old-palm.prc")

    def test_it_is_served_as_a_mobi(self, client, book):
        response = client.get(book["file"])
        assert response.status_code == 200
        assert response.content[60:68] == b"BOOKMOBI"
        assert response.headers["etag"].startswith('W/"mobi1-')

    def test_a_second_ask_costs_a_header(self, client, book):
        etag = client.get(book["file"]).headers["etag"]
        again = client.get(book["file"], headers={"If-None-Match": etag})
        assert again.status_code == 304

    def test_a_real_mobi_is_sent_as_it_is(self, library, gateway, client):
        MobiBuilder(title="Modern").build(library / "modern.mobi")
        gateway.reindex()
        book = next(b for b in client.get("/api/books?limit=500").json()["books"]
                    if b["path"] == "modern.mobi")
        response = client.get(book["file"])
        assert response.content == (library / "modern.mobi").read_bytes()
