import os

# 必须在 import app 之前设置测试环境，否则 Settings 在 import 时会读到本机环境
os.environ["APP_ENV"] = "test"
os.environ["APP_MODE"] = "mock"

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def make_settings():
    def _make(**overrides: object) -> Settings:
        base: dict[str, object] = {"app_env": "test", "app_mode": "mock"}
        base.update(overrides)
        return Settings(_env_file=None, **base)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def make_client(make_settings):
    def _make(**overrides: object):
        return TestClient(create_app(make_settings(**overrides)))

    return _make
