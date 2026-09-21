"""共通のエラー形式 ``{"error": code, "message": 日本語}`` を返すための仕組み。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """API が返す、表示用の日本語メッセージ付きエラー。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def register_error_handlers(app: FastAPI) -> None:
    """``ApiError``・入力検査の失敗・FastAPI の ``HTTPException`` を共通のエラー形式に変換する。"""

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 入力の値そのものは含めず、項目名（loc）だけを返す。
        fields = sorted(
            {
                ".".join(str(part) for part in error["loc"] if part != "body")
                for error in exc.errors()
            }
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_request",
                "message": "入力の形が正しくありません。",
                "fields": fields,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail and "message" in detail:
            content = detail
        elif exc.status_code == 404:
            content = {"error": "not_found", "message": "指定されたパスが見つかりません。"}
        elif exc.status_code == 405:
            content = {"error": "method_not_allowed", "message": "許可されていないメソッドです。"}
        else:
            content = {"error": "http_error", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content=content)
