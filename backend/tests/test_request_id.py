import logging


def test_request_id_echoed_when_provided(client) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


def test_request_id_generated_when_missing(client) -> None:
    response = client.get("/health/live")
    request_id = response.headers.get("X-Request-ID")
    assert request_id and len(request_id) > 0


def test_error_body_request_id_matches_header(client) -> None:
    response = client.get("/definitely-missing")
    assert response.status_code == 404
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_access_log_has_request_fields(client, caplog) -> None:
    caplog.set_level(logging.INFO, logger="app")
    client.get("/health/live")
    assert "request_id=" in caplog.text
    assert "method=GET" in caplog.text
    assert "path=/health/live" in caplog.text
    assert "status=200" in caplog.text
