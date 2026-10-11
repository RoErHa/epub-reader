"""An installable app: a manifest, icons, and a service worker whose
scope is the whole site. Browsers only offer any of it over a secure
origin, which is why the tailnet name is served with TLS (§8.18)."""
from __future__ import annotations

import io
import json

from PIL import Image


class TestManifest:
    def test_it_is_served_as_a_manifest(self, client):
        r = client.get("/manifest.webmanifest")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/manifest+json")

    def test_it_opens_the_library_as_an_app(self, client):
        m = json.loads(client.get("/manifest.webmanifest").content)
        assert m["name"] and m["short_name"]
        assert m["start_url"] == "/"
        assert m["scope"] == "/"
        assert m["display"] == "standalone"
        # The palette is dlna-gateway's, so the two read as a family.
        assert m["background_color"].upper() == "#0A1526"

    def test_every_icon_is_there_at_the_size_it_claims(self, client):
        icons = json.loads(client.get("/manifest.webmanifest").content)["icons"]
        sizes = {icon["sizes"] for icon in icons}
        assert {"192x192", "512x512"} <= sizes   # what installation needs
        assert any("maskable" in icon.get("purpose", "") for icon in icons)
        for icon in icons:
            r = client.get(icon["src"])
            assert r.status_code == 200, icon["src"]
            width, height = Image.open(io.BytesIO(r.content)).size
            assert f"{width}x{height}" == icon["sizes"]


class TestServiceWorker:
    def test_it_is_served_from_the_root(self, client):
        """A worker controls only what sits under its own URL, so one
        served from /static/ could never control the app at /."""
        r = client.get("/sw.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]

    def test_it_is_always_revalidated(self, client):
        """A stale worker is the one cache a restart cannot clear."""
        assert "no-cache" in client.get("/sw.js").headers["cache-control"]

    def test_it_carries_the_security_headers(self, client):
        assert "Content-Security-Policy" in client.get("/sw.js").headers

class TestShell:
    def test_the_shell_names_its_manifest_and_icon(self, client):
        html = client.get("/").text
        assert 'rel="manifest" href="/manifest.webmanifest"' in html
        assert 'rel="apple-touch-icon"' in html

    def test_the_apple_touch_icon_resolves(self, client):
        html = client.get("/").text
        href = html.split('rel="apple-touch-icon" href="')[1].split('"')[0]
        r = client.get(href)
        assert r.status_code == 200
        assert Image.open(io.BytesIO(r.content)).size == (180, 180)
