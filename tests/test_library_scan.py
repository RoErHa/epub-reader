"""Startup scan: what appeared, vanished or changed in the library folder
since the last run.

Detection is stat-based (size + mtime) rather than content-hashed — this
runs on every start, and hashing a large library each time would be
wasteful. M2's index owns content hashing and permanent identity.
"""
from __future__ import annotations

import json

from epub_library import LibraryScanner, ScanManifest, ScanReport
from epub_server import BookLibrary
from tests.epub_fixtures import EpubBuilder


def make_library(tmp_path, *names):
    root = tmp_path / "books"
    root.mkdir(exist_ok=True)
    for name in names:
        EpubBuilder(title=name).build(root / name)
    return root


def scanner_for(tmp_path, root):
    return LibraryScanner(
        BookLibrary(root), ScanManifest(tmp_path / "manifest.json")
    )


class TestFirstRun:
    def test_every_book_is_new_when_no_manifest_exists(self, tmp_path):
        root = make_library(tmp_path, "a.epub", "b.epub")
        report = scanner_for(tmp_path, root).scan()
        assert {b.name for b in report.added} == {"a.epub", "b.epub"}
        assert report.removed == []
        assert report.changed == []

    def test_first_run_is_flagged_so_callers_can_word_it_differently(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        assert scanner_for(tmp_path, root).scan().first_run is True

    def test_empty_library_reports_nothing(self, tmp_path):
        root = make_library(tmp_path)
        report = scanner_for(tmp_path, root).scan()
        assert report.added == []
        assert not report.has_changes

    def test_missing_library_folder_does_not_raise(self, tmp_path):
        report = scanner_for(tmp_path, tmp_path / "absent").scan()
        assert report.added == []


class TestSubsequentRuns:
    def test_nothing_changes_between_identical_scans(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        scanner = scanner_for(tmp_path, root)
        scanner.scan_and_record()
        report = scanner.scan()
        assert not report.has_changes
        assert report.first_run is False
        assert report.unchanged == 1

    def test_a_new_book_is_detected(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        scanner = scanner_for(tmp_path, root)
        scanner.scan_and_record()
        EpubBuilder(title="New").build(root / "new.epub")
        report = scanner.scan()
        assert [b.name for b in report.added] == ["new.epub"]
        assert report.has_changes

    def test_a_deleted_book_is_detected(self, tmp_path):
        root = make_library(tmp_path, "a.epub", "b.epub")
        scanner = scanner_for(tmp_path, root)
        scanner.scan_and_record()
        (root / "b.epub").unlink()
        report = scanner.scan()
        assert report.removed == ["b.epub"]

    def test_a_replaced_book_is_detected_as_changed(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        scanner = scanner_for(tmp_path, root)
        scanner.scan_and_record()
        # Same name, different bytes and mtime.
        EpubBuilder(title="Much longer title " * 40).build(root / "a.epub")
        import os, time  # noqa: E401
        os.utime(root / "a.epub", (time.time() + 10, time.time() + 10))
        report = scanner.scan()
        assert [b.name for b in report.changed] == ["a.epub"]

    def test_recording_clears_the_next_report(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        scanner = scanner_for(tmp_path, root)
        scanner.scan_and_record()
        EpubBuilder(title="New").build(root / "new.epub")
        scanner.scan_and_record()
        assert not scanner.scan().has_changes


class TestFiltering:
    def test_non_book_files_are_ignored(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        (root / "cover.jpg").write_bytes(b"nope")
        (root / "notes.txt").write_text("nope")
        report = scanner_for(tmp_path, root).scan()
        assert [b.name for b in report.added] == ["a.epub"]

    def test_other_book_formats_are_included(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        (root / "b.azw3").write_bytes(b"x")
        (root / "c.pdf").write_bytes(b"x")
        names = {b.name for b in scanner_for(tmp_path, root).scan().added}
        assert names == {"a.epub", "b.azw3", "c.pdf"}


class TestManifest:
    def test_manifest_is_written_as_readable_json(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        scanner_for(tmp_path, root).scan_and_record()
        data = json.loads((tmp_path / "manifest.json").read_text())
        assert "a.epub" in data["books"]
        assert "scanned_at" in data

    def test_a_corrupt_manifest_is_treated_as_a_first_run(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        (tmp_path / "manifest.json").write_text("{not json")
        report = scanner_for(tmp_path, root).scan()
        assert report.first_run is True
        assert len(report.added) == 1

    def test_manifest_parent_directory_is_created(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        manifest = ScanManifest(tmp_path / "nested" / "deep" / "m.json")
        LibraryScanner(BookLibrary(root), manifest).scan_and_record()
        assert (tmp_path / "nested" / "deep" / "m.json").is_file()


class TestSummary:
    def test_summary_names_the_counts(self, tmp_path):
        root = make_library(tmp_path, "a.epub", "b.epub")
        summary = scanner_for(tmp_path, root).scan().summary()
        assert "2 books" in summary

    def test_a_single_book_is_not_reported_as_plural(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        summary = scanner_for(tmp_path, root).scan().summary()
        assert "1 book found" in summary
        assert "1 books" not in summary

    def test_one_unchanged_book_is_singular_too(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        scanner = scanner_for(tmp_path, root)
        scanner.scan_and_record()
        assert "1 book." in scanner.scan().summary()

    def test_quiet_summary_when_nothing_changed(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        scanner = scanner_for(tmp_path, root)
        scanner.scan_and_record()
        assert "no changes" in scanner.scan().summary().lower()

    def test_report_is_serialisable_for_logging(self, tmp_path):
        root = make_library(tmp_path, "a.epub")
        payload = scanner_for(tmp_path, root).scan().as_dict()
        assert json.dumps(payload)
        assert payload["added"] == ["a.epub"]


class TestReportValueObject:
    def test_has_changes_is_false_only_when_all_three_lists_are_empty(self):
        assert not ScanReport([], [], [], 3, False).has_changes
        assert ScanReport([], ["gone.epub"], [], 0, False).has_changes
