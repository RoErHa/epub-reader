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
from pathlib import Path

from hypercorn.asyncio import serve
from hypercorn.config import Config as HypercornConfig

from epub_config import Config, ConfigError
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
        cfg.bind = [f"{self._config.bind}:{self._config.port}"]
        cfg.accesslog = "-"
        cfg.errorlog = "-"
        cert_host = self._config.cert_host
        if cert_host:
            cert_dir = Path.home() / ".config" / "tailscale-certs"
            cfg.certfile = str(cert_dir / f"{cert_host}.crt")
            cfg.keyfile = str(cert_dir / f"{cert_host}.key")
        return cfg

    def scan_library(self) -> None:
        """Pick up books added since the last run, before serving."""
        report = self._gateway.reindex()
        for line in report.summary().splitlines():
            LOG.info("%s", line)

    def run(self) -> None:
        cfg = self.hypercorn_config()
        self.scan_library()
        scheme = "https" if cfg.certfile else "http"
        LOG.info(
            "%s %s starting on %s://%s — library %s",
            SERVICE,
            VERSION,
            scheme,
            cfg.bind[0],
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
        if args.scan:
            return ScanCommand(config).run()
        try:
            GatewayRunner(config).run()
        except KeyboardInterrupt:
            LOG.info("Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(GatewayCLI.main())
