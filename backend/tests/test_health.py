def test_health_live(client) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_not_configured_database(client) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "config": "ok", "database": "not_configured"}


def test_health_ready_does_not_expose_secrets(client) -> None:
    response = client.get("/health/ready")
    text = response.text
    assert "DATABASE_URL" not in text
    assert "AI_API_KEY" not in text
