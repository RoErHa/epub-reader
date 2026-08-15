"""Content Security Policy and response hardening.

foliate-js states plainly that it cannot contain scripted EPUB content:
book resources are served same-origin as `blob:` URLs, and a WebKit bug
forces `allow-scripts` on the iframe, so the sandbox attribute is not a
defence. CSP is the containment, which makes this module security
critical rather than boilerplate.

The policy has to do two things at once: refuse every path to script
execution, while still permitting the blob: frames, inline styles and
data: images that rendering a book legitimately needs.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class ContentSecurityPolicy:
    """The single source of truth for what the browser may load."""

    header_name = "Content-Security-Policy"

    def directives(self) -> dict[str, list[str]]:
        return {
            # Deny by default; every relaxation below is deliberate.
            "default-src": ["'self'"],
            # No 'unsafe-inline', no 'unsafe-eval', and crucially no
            # blob: — book resources are blob: URLs, so allowing it here
            # would hand every EPUB in the library code execution.
            "script-src": ["'self'"],
            # Book CSS arrives inline AND as linked blob: stylesheets —
            # foliate serves every book resource as a blob: URL, so
            # omitting blob: here silently strips a book's typography.
            # Stylesheets cannot execute script, which is what keeps this
            # safe while script-src stays locked to 'self'.
            "style-src": ["'self'", "'unsafe-inline'", "blob:"],
            "img-src": ["'self'", "blob:", "data:"],
            "font-src": ["'self'", "blob:", "data:"],
            "media-src": ["'self'", "blob:"],
            # foliate-js renders each section in a blob: iframe.
            "frame-src": ["'self'", "blob:"],
            "connect-src": ["'self'"],
            "object-src": ["'none'"],
            "base-uri": ["'self'"],
            "form-action": ["'none'"],
            # 'self', not 'none': WebKit propagates this policy into
            # blob: documents, and every book section renders in a blob:
            # iframe — 'none' makes books refuse to load in Safari
            # entirely. X-Frame-Options: DENY still refuses framing by
            # other origins, and is not inherited by blob: documents.
            "frame-ancestors": ["'self'"],
        }

    def header_value(self) -> str:
        return "; ".join(
            f"{name} {' '.join(sources)}"
            for name, sources in self.directives().items()
        )


class SecurityHeaders:
    """Every header we put on every response."""

    def __init__(self, policy: ContentSecurityPolicy | None = None) -> None:
        self._policy = policy or ContentSecurityPolicy()

    def values(self) -> dict[str, str]:
        return {
            self._policy.header_name: self._policy.header_value(),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
        }

    def apply(self, response: Response) -> Response:
        for name, value in self.values().items():
            response.headers[name] = value
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Applies the headers to every response, errors included."""

    def __init__(self, app, headers: SecurityHeaders | None = None) -> None:
        super().__init__(app)
        self._headers = headers or SecurityHeaders()

    async def dispatch(self, request: Request, call_next):
        return self._headers.apply(await call_next(request))
