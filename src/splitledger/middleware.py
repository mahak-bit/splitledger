"""Cross-cutting HTTP middleware: request correlation ids and server timing."""

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
RESPONSE_TIME_HEADER = "X-Response-Time-ms"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id (honouring an inbound one) and measure handler latency."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[RESPONSE_TIME_HEADER] = f"{elapsed_ms:.2f}"
        return response
