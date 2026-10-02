"""One error shape for the whole product API:

    {"error": {"code": "rate_limited", "message": "...", "request_id": "..."}}
"""
from typing import Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    422: "validation_error", 429: "rate_limited", 502: "upstream_error", 503: "unavailable",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: Optional[str] = None, headers: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code or CODES.get(status, "error")
        self.headers = headers or {}


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def error_response(request: Request, status: int, code: str, message: str, headers: Optional[dict] = None):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id(request)}},
        headers=headers,
    )


def is_product_api(request: Request) -> bool:
    return request.url.path.startswith("/v1")


def install_handlers(app) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(request, exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if not is_product_api(request):   # the bundled UI reads {"detail": ...}
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return error_response(request, exc.status_code, CODES.get(exc.status_code, "error"), message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        if not is_product_api(request):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p != "query")
        return error_response(request, 422, "validation_error", f"{where}: {first.get('msg', 'invalid value')}".strip(": "))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        import logging
        logging.getLogger("market_terminal").exception("Unhandled error on %s", request.url.path)
        if not is_product_api(request):
            return JSONResponse({"detail": "Internal server error"}, status_code=500)
        return error_response(request, 500, "internal_error", "Internal server error")
