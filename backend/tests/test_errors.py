from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.exceptions import AppError, register_exception_handlers
from app.core.middleware import RequestContextMiddleware


def _build_mini_app() -> FastAPI:
    """仅用于异常契约测试的最小应用，避免污染生产路由。"""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    @app.post("/need-body")
    def need_body(payload: dict[str, int]) -> dict[str, int]:
        return payload

    @app.get("/rate-limited")
    def rate_limited() -> None:
        raise AppError("RATE_LIMITED", "请求过于频繁", status_code=429)

    @app.get("/legacy")
    def legacy() -> None:
        raise HTTPException(status_code=404, detail={"code": "CUSTOM_NOT_FOUND", "message": "自定义"})

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom-internal")

    return app


def test_404_uses_unified_error_contract(client) -> None:
    response = client.get("/definitely-missing")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["details"] is None
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def _mini_client(app: FastAPI) -> TestClient:
    # raise_server_exceptions=False：ServerErrorMiddleware 发送 500 后仍会重抛异常，
    # 关闭后测试才能拿到 500 响应本身
    return TestClient(app, raise_server_exceptions=False)


def test_validation_error_maps_to_422() -> None:
    app = _build_mini_app()
    with _mini_client(app) as test_client:
        response = test_client.post("/need-body", json={"payload": "not-int"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["error"]["details"], list)
    # 校验详情不得回显输入值（可能含敏感信息）
    assert '"input"' not in response.text


def test_app_error_uses_status_and_code() -> None:
    app = _build_mini_app()
    with _mini_client(app) as test_client:
        response = test_client.get("/rate-limited")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"


def test_http_exception_detail_passthrough() -> None:
    app = _build_mini_app()
    with _mini_client(app) as test_client:
        response = test_client.get("/legacy")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CUSTOM_NOT_FOUND"
    assert response.json()["error"]["message"] == "自定义"


def test_unhandled_error_returns_500_without_traceback_in_body(caplog) -> None:
    app = _build_mini_app()
    with _mini_client(app) as test_client:
        response = test_client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "boom-internal" not in response.text
    # traceback 只进日志，不进响应
    assert "Traceback" in caplog.text
