"""共通のエラー形式 ``{"error": code, "message": 日本語}`` を返すための仕組み。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """API が返す、表示用の日本語メッセージ付きエラー。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def register_error_handlers(app: FastAPI) -> None:
    """``ApiError`` と FastAPI の ``HTTPException`` を共通のエラー形式に変換する。"""

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message},
        )

    @app.exception_handler(FastAPIHTTPException)
    async def _handle_http_exception(request: Request, exc: FastAPIHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail and "message" in detail:
            content = detail
        else:
            content = {"error": "http_error", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content=content)
