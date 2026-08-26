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

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


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


class SameOriginPolicy:
    """Whether a request is allowed to change anything.

    There is no authentication here and nothing uses cookies, so a
    cross-site request cannot be *authenticated* — but it does not need
    to be. The service acts for whoever can reach the port, and
    `POST /api/books/forget-missing?apply=true&force=true` drops reading
    positions and highlights. A bodiless POST is a CORS "simple request":
    it is sent without a preflight and only the *reply* is withheld from
    the caller, so CORS never prevents the act. Checking `Origin` does.
    """

    unsafe_methods = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def allows(self, method: str, origin: str | None, host: str | None) -> bool:
        if method.upper() not in self.unsafe_methods:
            return True          # reading changes nothing
        if not origin:
            # curl, `run.sh` and the test suite send no Origin at all.
            # Only a browser can be made to forge one, and a browser
            # always sends it on these methods — so absence is a
            # non-browser caller, not an attack to defend against.
            return True
        # `Origin: null` (a sandboxed frame) splits to an empty netloc and
        # is refused with everything else that is not us.
        return urlsplit(origin).netloc.lower() == (host or "").lower()


class SameOriginMiddleware(BaseHTTPMiddleware):
    """Refuses a state-changing request that came from somebody else's
    page."""

    def __init__(self, app, policy: SameOriginPolicy | None = None) -> None:
        super().__init__(app)
        self._policy = policy or SameOriginPolicy()

    async def dispatch(self, request: Request, call_next):
        if not self._policy.allows(request.method,
                                   request.headers.get("origin"),
                                   request.headers.get("host")):
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-site requests are refused."},
            )
        return await call_next(request)
