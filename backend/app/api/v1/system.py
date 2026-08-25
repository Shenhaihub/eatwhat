"""观测仪表盘最小接口（P7-09 + P7-02）。

GET /api/v1/system/ai-stats
    Query: limit=100（默认 500，最大 2000）, stage=follow_up|final（可选过滤）
    Response: AiStatsResponse（compute_stats 输出，详见 ai_stats.compute_stats）

GET /api/v1/system/metrics
    Response: 系统级请求指标（uptime / 总请求数 / 错误率 / 延迟分位数）

    权限：不需要登录（仅匿名读取"调用次数/字符数/偏好覆盖率"这种整体聚合统计；
    但 sample_records 里的 user_id/session_id 会自动截断为 hash 展示，避免泄露 PII）。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from app.core.ai_stats import AiCallMetaStore, compute_stats
from app.core.middleware import get_request_metrics

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/ai-stats")
def get_ai_stats(
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    stage: Annotated[Literal["follow_up", "final"] | None, Query()] = None,
) -> dict[str, Any]:
    """最近 N 条 ai_call_meta 的整体观测。

    - 时间窗口：取 buffer 中最近 `limit` 条。
    - 可选按 `ai_stage` 过滤（follow_up = AI 动态追问、final = 出 Top5 推荐）。
    - 返回结果里 `user_id`/`session_id` 统一做短 hash，不作为 PII 返回。
    """
    store = AiCallMetaStore.instance()
    recs = store.snapshot(limit=limit)
    if stage is not None:
        recs = [r for r in recs if r.ai_stage == stage]

    def _obfuscate_id(s: str | None) -> str | None:
        if not s:
            return None
        import hashlib

        return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]

    # compute_stats 里 sample_records 会把 user_id/session_id 原样带出；
    # 这里再做一次脱敏
    stats = compute_stats(recs)
    for s in stats.get("sample_records", []):
        if "user_id" in s:
            s["user_id_sha1_10"] = _obfuscate_id(s.pop("user_id", None))
        if "session_id" in s:
            s["session_id_sha1_10"] = _obfuscate_id(s.pop("session_id", None))
    return stats


@router.get("/metrics")
def get_system_metrics() -> dict[str, Any]:
    """系统级请求指标（P7-02）。

    返回：
    - uptime_seconds：进程运行时长
    - total_requests：总请求数
    - error_requests：错误请求数（HTTP ≥ 400）
    - error_rate：错误率
    - latency_ms：最近 1000 次请求的延迟分位数（avg/p50/p95/p99）
    """
    return get_request_metrics().snapshot()
