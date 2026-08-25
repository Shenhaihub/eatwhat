"""P7-09 GET /api/v1/system/ai-stats 仪表盘最小接口验收。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.ai_stats import (
    AiCallMetaRecord,
    AiCallMetaStore,
    compute_stats,
    configure_ai_call_logging,
)
from app.core.config import get_settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    # 不用 pytest 内置 tmp_path（在部分 Windows 环境下写 TEMP 目录会被占用报 5）
    # 直接放到 backend/.local/pytest-tmp，测试函数结束后手动清理不做，大小很小
    safe_dir = Path(__file__).resolve().parent.parent / ".local" / "pytest-tmp" / f"ai-stats-{id(tmp_path_factory)}"
    safe_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings().model_copy(
        update={"ai_call_meta_file": "", "ai_stats_buffer_size": 500, "log_dir": str(safe_dir)},
        deep=True,
    )
    AiCallMetaStore._instance = None
    configure_ai_call_logging(settings)
    app = create_app(settings)
    return TestClient(app)


def _make(ts_offset: int, *, stage: str, pref_used: bool, total_chars: int, outcome: str, snaps: int = 2) -> AiCallMetaRecord:
    now = 1_700_000_000 + ts_offset
    return AiCallMetaRecord(
        ts=now,
        ts_iso=datetime.fromtimestamp(now, tz=UTC).isoformat(),
        ai_stage=stage,
        session_id=f"sess_{ts_offset}",
        user_id=f"user_{ts_offset % 3}",
        ai_round_1based=None if stage == "final" else ((ts_offset % 3) + 1),
        preference_context_used=pref_used,
        preference_context_snapshot_count=snaps if pref_used else 0,
        preference_context_chars=600 * snaps if pref_used else 0,
        preference_context_lines=12 * snaps if pref_used else 0,
        system_prompt_chars=total_chars // 3,
        user_prompt_chars=total_chars - total_chars // 3,
        total_prompt_chars=total_chars,
        ai_outcome=outcome,
        ai_fail_code=None if outcome == "ok" else "mock_timeout",
        final_reason=None if outcome == "ok" else "provider timed out",
    )


def test_empty_buffer_returns_zero_stats(client: TestClient) -> None:
    resp = client.get("/api/v1/system/ai-stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["queried_records"] == 0
    assert body["pref_context_used_rate"] == 0.0
    assert body["avg_total_prompt_chars"] == 0
    assert body["sample_records"] == []


def test_stats_computation_and_pii_obfuscation(client: TestClient) -> None:
    store = AiCallMetaStore.instance()
    # 8 条 follow_up（6 条 pref_used，1 条 ok + 1 条 fail；2 条未用 pref）
    # 4 条 final（3 条 pref_used ok，1 条 ok 未用 pref）
    records: list[AiCallMetaRecord] = [
        # follow_up
        _make(0, stage="follow_up", pref_used=True, total_chars=3000, outcome="ok", snaps=1),
        _make(1, stage="follow_up", pref_used=True, total_chars=3600, outcome="ok", snaps=2),
        _make(2, stage="follow_up", pref_used=True, total_chars=4200, outcome="ok", snaps=3),
        _make(3, stage="follow_up", pref_used=True, total_chars=4500, outcome="ok", snaps=2),
        _make(4, stage="follow_up", pref_used=True, total_chars=4800, outcome="fail", snaps=3),
        _make(5, stage="follow_up", pref_used=True, total_chars=4000, outcome="fallback_rules_engine", snaps=1),
        _make(6, stage="follow_up", pref_used=False, total_chars=2400, outcome="ok"),
        _make(7, stage="follow_up", pref_used=False, total_chars=2200, outcome="ok"),
        # final
        _make(8, stage="final", pref_used=True, total_chars=6000, outcome="ok", snaps=3),
        _make(9, stage="final", pref_used=True, total_chars=6500, outcome="ok", snaps=4),
        _make(10, stage="final", pref_used=True, total_chars=7000, outcome="ok", snaps=5),
        _make(11, stage="final", pref_used=False, total_chars=4800, outcome="ok"),
    ]
    for r in records:
        store.push(r)

    resp = client.get("/api/v1/system/ai-stats")
    assert resp.status_code == 200
    b = resp.json()
    assert b["queried_records"] == 12
    # pref_used = (6 follow_up ok/fail/fallback) + (3 final ok) = 9 of 12 = 0.75
    assert b["pref_context_used_rate"] == pytest.approx(9 / 12, rel=1e-3)
    # avg snaps：9 条里 (1+2+3+2+3+1 + 3+4+5) / 9 = 24 / 9 ≈ 2.6667
    assert b["avg_snapshot_count_used"] == pytest.approx(24 / 9, rel=1e-3)
    # avg total prompt chars：sum / 12
    total_sum = 3000 + 3600 + 4200 + 4500 + 4800 + 4000 + 2400 + 2200 + 6000 + 6500 + 7000 + 4800
    assert b["avg_total_prompt_chars"] == round(total_sum / 12)
    # stage 维度
    assert b["breakdown_by_stage"]["follow_up"]["calls"] == 8
    assert b["breakdown_by_stage"]["follow_up"]["pref_used_rate"] == pytest.approx(6 / 8, rel=1e-3)
    assert b["breakdown_by_stage"]["final"]["calls"] == 4
    # outcome breakdown
    outcome_ok = 0
    for r in records:
        if r.ai_outcome == "ok":
            outcome_ok += 1
    assert b["outcome_breakdown"].get("ok") == outcome_ok
    # sample_records：最近 5 条（倒序，对应 records[-1] .. records[-5]）
    assert len(b["sample_records"]) == 5
    sample = b["sample_records"]
    assert "user_id_sha1_10" in sample[0]
    assert "session_id_sha1_10" in sample[0]
    # PII：user_id / session_id 明文不应返回
    assert "user_id" not in sample[0]
    assert "session_id" not in sample[0]
    # sample_records[0].ts 应是 records[-1].ts（最新）
    assert sample[0]["ts"] == records[-1].ts


def test_limit_and_stage_filter(client: TestClient) -> None:
    store = AiCallMetaStore.instance()
    for i in range(20):
        store.push(_make(i, stage="follow_up" if i < 14 else "final", pref_used=True, total_chars=1000 + i, outcome="ok", snaps=2))
    # limit=5 → 应取最后 5 条
    r = client.get("/api/v1/system/ai-stats", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["queried_records"] == 5
    # stage=final → 只有后 6 条（final），且 limit 默认 500 → 取 6
    r2 = client.get("/api/v1/system/ai-stats", params={"stage": "final"})
    assert r2.status_code == 200
    assert r2.json()["queried_records"] == 6
    assert r2.json()["breakdown_by_stage"]["final"]["calls"] == 6


def test_compute_stats_standalone_empty_ok() -> None:
    body = compute_stats([])
    assert body["queried_records"] == 0
    assert body["window"]["oldest_ts"] is None
    assert body["sample_records"] == []
