import os

# 必须在 import app 之前设置测试环境，否则 Settings 在 import 时会读到本机环境
os.environ["APP_ENV"] = "test"
os.environ["APP_MODE"] = "mock"
# 强制 POI 走 mock，避免测试机配置了真实 AMAP_API_KEY 时 POI 测试断言失败
os.environ["POI_PROVIDER"] = "mock"
# 清空真实外部密钥：测试禁止任何真实外部网络调用
os.environ.pop("AMAP_API_KEY", None)
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_ANON_KEY", None)
os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def make_settings():
    def _make(**overrides: object) -> Settings:
        base: dict[str, object] = {"app_env": "test", "app_mode": "mock", "poi_provider": "mock"}
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
