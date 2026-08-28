"""A book the reader already has costs a header, not a book.

One rule, one place — the shape `epub_provenance` already takes for
§5's rule. The routes are about routes; whether a reader needs the book
again is a question of its own, and it is asked of three responses that
are otherwise nothing alike: a file read off disk, a comic packed with
RAR, and an EPUB that was never zipped.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import Response


class Revalidation:
    """"Is what you already have still the book?" — and the 304 that
    says yes.

    Every book response already carried a validator and **nothing ever
    acted on one**: Starlette computes an ETag in `FileResponse` and
    answers 304 only from `StaticFiles`, which this app does not use. So
    the header was advice nobody could take, and re-opening a book
    downloaded it again. On an iPad over a tailnet the radio is the
    expensive part of reading, and the largest comic here is 315 MB.

    > **The validator names the source, never the bytes we hand over.**
    > Two of these responses are built rather than read — a comic packed
    > with RAR and an EPUB that was never zipped (§8.5, §8.15) — so
    > hashing what we send would mean doing the packing in order to
    > discover it need not be sent. That is the whole cost being avoided.
    > `book.content_hash` is already stored, already identifies the book
    > by what is in it (§5), and is better than an mtime: a library
    > copied to a new disk keeps its validators, and a book replaced by a
    > better scan loses them.

    > **The recipe is part of what the reader is holding.** A repacked
    > comic is not the archive on disk, so the tag names the packing and
    > carries a version of it. Change how we pack and every cached copy
    > is stale, which is a thing to say rather than to hope about.

    > **A file's own bytes get a strong validator; a body we build gets
    > a weak one.** A zip carries the mtimes of what went into it, so
    > packing the same book twice is the same book and not the same
    > bytes — `W/` is the honest way to say that. It also keeps a weak
    > tag out of `If-Range`, which is the one use a weak validator may
    > not be put to.
    """

    #: Store it, and ask before using it. Without this a browser has no
    #: freshness information at all and falls back to a heuristic, which
    #: is how a validator ends up never being offered back.
    CONTROL = "private, no-cache"

    #: One entity-tag: a quoted opaque string, weak or not, or `*`.
    #: Split on commas and a tag containing one would come apart.
    _TAG = re.compile(r'(?:W/)?"[^"]*"|\*')

    def __init__(self, request) -> None:
        self._offered = self._parse(request.headers.get("if-none-match"))

    @classmethod
    def strong(cls, recipe: str, content_hash: str) -> str:
        """For bytes that are on disk exactly as we send them."""
        return f'"{recipe}-{content_hash}"'

    @classmethod
    def weak(cls, recipe: str, content_hash: str) -> str:
        """For a body we build: the same book, not the same bytes."""
        return "W/" + cls.strong(recipe, content_hash)

    @classmethod
    def of_file(cls, recipe: str, path) -> str:
        """For a file whose *name* does not identify its contents.

        A cover is keyed by the book's hash and rewritten in place when
        a better one turns up (§8.12, §8.13), so the URL says which book
        and not which picture. Size and mtime are what identify it —
        the one case where Starlette's own answer is the honest one.
        Hashing two thousand thumbnails on every browse would cost more
        than the bytes it saves, and this is the reason that URL may not
        be told it is immutable, however much it looks it.
        """
        stat = Path(path).stat()
        return cls.strong(recipe, f"{stat.st_size:x}-{stat.st_mtime_ns:x}")

    @classmethod
    def _parse(cls, header: str | None) -> frozenset[str]:
        if not header:
            return frozenset()
        # Compared weakly, which is what 304 asks for: a cache may hand
        # back what we sent with the `W/` taken off.
        return frozenset(found.group().removeprefix("W/")
                         for found in cls._TAG.finditer(header))

    def holds(self, etag: str) -> bool:
        """Whether the reader is already holding this very book."""
        return "*" in self._offered or etag.removeprefix("W/") in self._offered

    def answer(self, etag: str, build) -> Response:
        """A 304, or whatever `build()` makes.

        `build` is a callable and not a response, because a 304 must not
        call it — see the note above about packing a book to find out
        nobody needed it.
        """
        if self.holds(etag):
            # RFC 9110: a 304 carries the validator, so the cache can
            # tell which of the copies it holds has just been confirmed.
            return Response(status_code=304, headers=self._headers(etag))
        response = build()
        response.headers.update(self._headers(etag))
        return response

    def _headers(self, etag: str) -> dict:
        return {"ETag": etag, "Cache-Control": self.CONTROL}
