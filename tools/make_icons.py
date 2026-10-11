#!/usr/bin/env python3
"""Paints the app's icons: an open book in amber on dlna-gateway's navy.

    .venv/bin/python tools/make_icons.py

The PNGs are committed, so this runs only when the drawing changes. It is
painted large and scaled down, which is the cheapest anti-aliasing there
is, and a maskable variant keeps the book inside the circle a launcher
may crop it to.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
NAVY = "#0A1526"
AMBER = "#FFC24A"
EDGE = "#C8902A"


class IconPainter:
    """One drawing, at any size, with or without a safe margin."""

    CANVAS = 1024

    def __init__(self, scale: float = 1.0) -> None:
        # The fraction of the canvas the book may use: 1.0 for an icon
        # shown as it is, less for one a launcher crops to a circle.
        self._scale = scale

    def _at(self, x: float, y: float) -> tuple[float, float]:
        c = self.CANVAS / 2
        return (c + (x - c) * self._scale, c + (y - c) * self._scale)

    def _poly(self, draw: ImageDraw.ImageDraw, points, fill) -> None:
        draw.polygon([self._at(x, y) for x, y in points], fill=fill)

    def paint(self, size: int) -> Image.Image:
        image = Image.new("RGB", (self.CANVAS, self.CANVAS), NAVY)
        draw = ImageDraw.Draw(image)
        cx = self.CANVAS / 2
        # The cover's edge, showing beneath the pages.
        self._poly(draw, [(cx, 800), (cx - 330, 740), (cx - 330, 700),
                          (cx, 760), (cx + 330, 700), (cx + 330, 740)], EDGE)
        for side in (-1, 1):
            inner, outer = cx + side * 14, cx + side * 310
            self._poly(draw, [(inner, 330), (outer, 270),
                              (outer, 690), (inner, 750)], AMBER)
            # Lines of text, following the page's slope.
            for row in range(5):
                y = 380 + row * 64
                a, b = cx + side * 70, cx + side * 260
                self._poly(draw, [(a, y), (b, y - 42),
                                  (b, y - 26), (a, y + 16)], NAVY)
        return image.resize((size, size), Image.LANCZOS)


class IconSet:
    """Every file the manifest and the shell name."""

    FILES = (
        ("icon-192.png", 192, 1.0),
        ("icon-512.png", 512, 1.0),
        ("icon-maskable-512.png", 512, 0.72),
        ("apple-touch-icon.png", 180, 1.0),
    )

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def write(self) -> list[Path]:
        self._folder.mkdir(parents=True, exist_ok=True)
        written = []
        for name, size, scale in self.FILES:
            path = self._folder / name
            IconPainter(scale).paint(size).save(path, optimize=True)
            written.append(path)
        return written


if __name__ == "__main__":
    for path in IconSet(ROOT / "static" / "icons").write():
        print(path.relative_to(ROOT))
