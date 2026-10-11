#!/usr/bin/env python3
"""Entry point for the epub-reader service.

    python epub_gateway.py [--env .env]

Every setting comes from the .env file — this module accepts no
configuration flags of its own beyond which file to read.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from hypercorn.asyncio import serve
from hypercorn.config import Config as HypercornConfig

from epub_config import Config, ConfigError
from epub_refresh import BATCH
from epub_server import SERVICE, VERSION, EpubGateway

LOG = logging.getLogger(SERVICE)


class LoggingSetup:
    """Console plus rotating-style file logging, both at the configured level."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def apply(self) -> None:
        level = getattr(logging, self._config.log_level, logging.INFO)
        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        root = logging.getLogger()
        root.setLevel(level)
        root.handlers.clear()

        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        root.addHandler(console)

        file_handler = logging.FileHandler(self._config.log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)


class ScanCommand:
    """`--scan`: index the library, report what changed, then exit.

    The same index runs at startup, so `run.sh start` and `run.sh restart`
    both pick up books added since the last run.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    def run(self) -> int:
        if not self._config.library_root.is_dir():
            print(
                f"Library folder does not exist: {self._config.library_root}\n"
                "Set LIBRARY_ROOT in your .env file.",
                file=sys.stderr,
            )
            return 1
        gateway = EpubGateway(self._config)
        try:
            print(gateway.reindex().summary())
            # Free and local, so it runs unless turned off.
            if self._config.tidy_on_scan:
                print(gateway.tidy_summary(gateway.tidy()))
            if self._config.repair_on_scan:
                print(gateway.repair(apply=True))
        finally:
            gateway.close()
        return 0


class RepairCommand:
    """`--repair`: check authors and titles, report, then exit.

    Looking is the default. `--apply` is what changes anything, and the
    same passes run after a startup scan when REPAIR_ON_SCAN is set.
    """

    def __init__(self, config: Config, apply: bool = False) -> None:
        self._config = config
        self._apply = apply

    def run(self) -> int:
        gateway = EpubGateway(self._config)
        try:
            print(gateway.repair(apply=self._apply))
        finally:
            gateway.close()
        return 0


class RefreshCommand:
    """`--refresh`: bring the oldest author data up to date, then exit.

    This is what the weekly launchd job runs. It asks about ten authors
    a week, oldest first, so a library of this size comes round in about
    forty weeks without ever looking like a bulk sweep.
    """

    def __init__(self, config: Config, limit: int) -> None:
        self._config = config
        self._limit = limit

    def run(self) -> int:
        gateway = EpubGateway(self._config)
        try:
            print(gateway.refresh_authors(limit=self._limit))
        finally:
            gateway.close()
        return 0


class ForgetCommand:
    """`--forget`: remove books whose files are gone.

    A scan only marks them, so that an unplugged drive is a nuisance
    rather than a disaster. This is the deliberate part, and it refuses
    when most of the library is missing.
    """

    def __init__(self, config: Config, apply: bool = False,
                 force: bool = False) -> None:
        self._config = config
        self._apply = apply
        self._force = force

    def run(self) -> int:
        from epub_forget import UnpluggedError

        gateway = EpubGateway(self._config)
        try:
            print(gateway.forget_missing(apply=self._apply, force=self._force))
        except UnpluggedError as exc:
            print(f"✗  {exc}", file=sys.stderr)
            return 1
        finally:
            gateway.close()
        return 0


class SeriesCommand:
    """`--series`: file books under the series their filenames name.

    Needs no provider: 597 filenames carry "Series NN - Title", and a
    name is only believed when two books by one author agree on it.
    """

    def __init__(self, config: Config, apply: bool = False) -> None:
        self._config = config
        self._apply = apply

    def run(self) -> int:
        gateway = EpubGateway(self._config)
        try:
            print(gateway.name_series(apply=self._apply))
        finally:
            gateway.close()
        return 0


class NamesCommand:
    """`--names`: author, series and title, read from one filename.

    "Anthony, Piers - Xanth 08 - Crewel Lye" is three facts written as
    one. §8.4 made the filename lead for PDFs but only for the title,
    so these books have no author — and with no author, the series rule
    has nobody to agree with and never fires.
    """

    def __init__(self, config: Config, apply: bool = False,
                 author: str | None = None,
                 formats: list[str] | None = None) -> None:
        self._config = config
        self._apply = apply
        self._author = author
        self._formats = formats

    def run(self) -> int:
        gateway = EpubGateway(self._config)
        try:
            print(gateway.read_names(apply=self._apply, author=self._author,
                                     formats=self._formats))
        finally:
            gateway.close()
        return 0


class CoAuthorsCommand:
    """`--coauthors`: a second author written into the title.

    "Dead Morn (with Roberto Fuentes)" is a book by two people whose
    filename had room for one. A rule, not a lookup — no provider, no
    key, no network.
    """

    def __init__(self, config: Config, apply: bool = False) -> None:
        self._config = config
        self._apply = apply

    def run(self) -> int:
        gateway = EpubGateway(self._config)
        try:
            print(gateway.read_coauthors(apply=self._apply))
        finally:
            gateway.close()
        return 0


class FragmentsCommand:
    """`--fragments`: finish the titles somebody's filesystem cut off.

    "The Caterpillars Questi" matches nothing anywhere, for ever. Trim
    to the last complete word, ask again, and take the answer only if it
    *completes* what we hold.
    """

    def __init__(self, config: Config, apply: bool = False,
                 author: str | None = None,
                 formats: list[str] | None = None) -> None:
        self._config = config
        self._apply = apply
        self._author = author
        self._formats = formats

    def run(self) -> int:
        gateway = EpubGateway(self._config)
        try:
            print(gateway.mend_fragments(apply=self._apply, author=self._author,
                                         formats=self._formats))
        finally:
            gateway.close()
        return 0


class HypothesesCommand:
    """`--hypotheses`: the names that are clear to a person, not to the code.

    `Colleen McCullough - Masters of Rome 1 - First Man in Rome` has no
    comma, so §8.13a sees no author and everything asked of an author is
    lost with it. Cut the name every way, ask Open Library which reading
    is real, and keep the one whose author's own works hold the book.
    """

    def __init__(self, config: Config, apply: bool = False,
                 author: str | None = None,
                 formats: list[str] | None = None) -> None:
        self._config = config
        self._apply = apply
        self._author = author
        self._formats = formats

    def run(self) -> int:
        gateway = EpubGateway(self._config)
        try:
            print(gateway.read_hypotheses(apply=self._apply,
                                          author=self._author,
                                          formats=self._formats))
        finally:
            gateway.close()
        return 0


class TagsCommand:
    """`--tags`: what the scanner wrote into the title.

    "(v2.1)", "(not ocr)", "(uc)" — a closed vocabulary, never any
    bracket, because the same titles carry "(The Murderbot Diaries)" and
    "(Osprey, Men at Arms #121)".
    """

    def __init__(self, config: Config, apply: bool = False) -> None:
        self._config = config
        self._apply = apply

    def run(self) -> int:
        gateway = EpubGateway(self._config)
        try:
            print(gateway.clean_tags(apply=self._apply))
        finally:
            gateway.close()
        return 0


class CoversCommand:
    """`--covers`: a cover for books that show a lettered tile.

    Page one of a PDF, but only when page one is the cover — a raster
    image covering the page with no text over it. A title page is a
    worse tile than the letter it would replace.
    """

    def __init__(self, config: Config, apply: bool = False,
                 limit: int | None = None, online: bool = False) -> None:
        self._config = config
        self._apply = apply
        self._limit = limit
        self._online = online

    def run(self) -> int:
        gateway = EpubGateway(self._config)
        try:
            print(gateway.find_covers(apply=self._apply, limit=self._limit,
                                      online=self._online))
        finally:
            gateway.close()
        return 0


class DedupeCommand:
    """`--dedupe`: one row per work, and say what that took.

    No file is deleted — the spare copies keep their rows and their
    paths, and the restore endpoint brings them back.
    """

    def __init__(self, config: Config, apply: bool = False) -> None:
        self._config = config
        self._apply = apply

    def run(self) -> int:
        gateway = EpubGateway(self._config)
        try:
            print(gateway.dedupe_books(apply=self._apply))
        finally:
            gateway.close()
        return 0


class GatewayRunner:
    """Owns the server lifecycle."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._gateway = EpubGateway(config)

    def hypercorn_config(self) -> HypercornConfig:
        cfg = HypercornConfig()
        port = self._config.port
        secure = [f"{a}:{port}" for a in self._config.bind_addresses]
        plain = [f"{a}:{port}" for a in self._config.plain_bind_addresses]
        cfg.accesslog = "-"
        cfg.errorlog = "-"
        # Raises ConfigError with an explanation if TLS is asked for but
        # the certificate cannot be found — the service used to die with
        # a bare FileNotFoundError from inside hypercorn.
        tls = self._config.require_tls_files()
        if tls:
            cert, key = tls
            cfg.certfile = str(cert)
            cfg.keyfile = str(key)
            # hypercorn reads insecure_bind only while TLS is on, which
            # is the only time there is a difference to make.
            cfg.bind, cfg.insecure_bind = secure, plain
        else:
            cfg.bind = secure + plain
        return cfg

    def close(self) -> None:
        self._gateway.close()

    def scan_library(self) -> None:
        """Pick up books added since the last run, before serving.

        And read their names, so a launchd start behaves exactly as
        `./run.sh restart` does — a book dropped in is a book filed.
        """
        report = self._gateway.reindex()
        for line in report.summary().splitlines():
            LOG.info("%s", line)
        if self._config.tidy_on_scan:
            LOG.info("%s", self._gateway.tidy_summary(self._gateway.tidy()))

    def run(self) -> None:
        cfg = self.hypercorn_config()
        self.scan_library()
        scheme = "https" if cfg.certfile else "http"
        urls = [f"{scheme}://{b}" for b in cfg.bind]
        if cfg.certfile:
            urls += [f"http://{b}" for b in cfg.insecure_bind]
        LOG.info(
            "%s %s starting on %s — library %s",
            SERVICE,
            VERSION,
            ", ".join(urls),
            self._config.library_root,
        )
        asyncio.run(serve(self._gateway.app, cfg))


class GatewayCLI:
    @staticmethod
    def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(prog="epub_gateway", description=__doc__)
        parser.add_argument(
            "--env",
            default=".env",
            help="Path to the configuration file (default: .env)",
        )
        parser.add_argument(
            "--scan",
            action="store_true",
            help="Report what changed in the library, then exit without serving.",
        )
        parser.add_argument(
            "--repair",
            action="store_true",
            help="Check authors and titles, report, then exit without serving.",
        )
        parser.add_argument(
            "--forget",
            action="store_true",
            help="Remove books whose files are gone, then exit.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="With --forget: proceed even when most of the library is"
                 " missing, which usually means an unmounted disk.",
        )
        parser.add_argument(
            "--series",
            action="store_true",
            help="File books under the series their names give, then exit.",
        )
        parser.add_argument(
            "--names",
            action="store_true",
            help="Read author, series and title from filenames, then exit.",
        )
        parser.add_argument(
            "--author",
            help="With --names, --fragments or --hypotheses: only this author.",
        )
        parser.add_argument(
            "--format",
            action="append",
            dest="formats",
            help=("With --names, --fragments or --hypotheses: only this"
                  " format (repeatable)."),
        )
        parser.add_argument(
            "--fragments",
            action="store_true",
            help="Finish titles that were cut off, then exit.",
        )
        parser.add_argument(
            "--hypotheses",
            action="store_true",
            help="Ask a catalogue to read the names nothing here can, then exit.",
        )
        parser.add_argument(
            "--tags",
            action="store_true",
            help="Take the scanner's notes out of the titles, then exit.",
        )
        parser.add_argument(
            "--coauthors",
            action="store_true",
            help="Free the co-authors written into titles, then exit.",
        )
        parser.add_argument(
            "--dedupe",
            action="store_true",
            help="Report works held more than once, then exit without serving.",
        )
        parser.add_argument(
            "--covers",
            action="store_true",
            help="Give a cover to books that show a lettered tile, then exit.",
        )
        parser.add_argument(
            "--online",
            action="store_true",
            help="With --covers: ask Open Library about the books whose files"
                 " hold no image, one request a minute.",
        )
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="Re-ask about the oldest author data, then exit without serving.",
        )
        parser.add_argument(
            "--max",
            type=int,
            default=BATCH,
            help=f"With --refresh: how many authors to ask about (default {BATCH}).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="With --repair: make the changes rather than only listing them.",
        )
        return parser.parse_args(argv)

    @classmethod
    def main(cls, argv: list[str] | None = None) -> int:
        args = cls.parse_args(argv)
        try:
            config = Config.from_file(args.env)
        except ConfigError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 2
        LoggingSetup(config).apply()
        if args.forget:
            return ForgetCommand(config, apply=args.apply,
                                 force=args.force).run()
        if args.series:
            return SeriesCommand(config, apply=args.apply).run()
        if args.names:
            return NamesCommand(config, apply=args.apply, author=args.author,
                                formats=args.formats).run()
        if args.fragments:
            return FragmentsCommand(config, apply=args.apply,
                                    author=args.author,
                                    formats=args.formats).run()
        if args.hypotheses:
            return HypothesesCommand(config, apply=args.apply,
                                     author=args.author,
                                     formats=args.formats).run()
        if args.tags:
            return TagsCommand(config, apply=args.apply).run()
        if args.coauthors:
            return CoAuthorsCommand(config, apply=args.apply).run()
        if args.dedupe:
            return DedupeCommand(config, apply=args.apply).run()
        if args.covers:
            return CoversCommand(config, apply=args.apply,
                                 limit=args.max if args.max != BATCH else None,
                                 online=args.online).run()
        if args.refresh:
            return RefreshCommand(config, limit=args.max).run()
        if args.repair:
            return RepairCommand(config, apply=args.apply).run()
        if args.scan:
            return ScanCommand(config).run()
        try:
            GatewayRunner(config).run()
        except ConfigError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            LOG.info("Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(GatewayCLI.main())
