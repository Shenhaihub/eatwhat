"""ai_call_meta 结构化观测缓冲（P7-09 仪表盘最小接口）。

本模块只做一件事：拦截 `app.services.recommendation_session` logger 发出的
`ai_call stage=...` 结构化日志（由 `RecommendationSession._log_ai_call_meta` 产生），
把 `record.__dict__` 里的 extra 字段抽取后推进 **in-memory deque 环形缓冲**，
并可选追加写 `ai_call_meta.jsonl` 供进程重启后恢复最近一部分数据。

注意：
- 本缓冲不是高可用日志系统（不做 DB / Kafka / 多进程共享），
  只是满足"最近 N 条观测"的最小仪表盘。生产部署若启用 Gunicorn 多 worker，
  每个 worker 各自持有一份缓冲 + JSONL 追加（不同 worker 不会互相覆盖，因为用 a 模式）。
- 仪表盘接口 GET /api/v1/system/ai-stats 只会读取这一份缓冲（不提供全局聚合承诺）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.core.config import Settings


@dataclass(slots=True)
class AiCallMetaRecord:
    """规范化的一条 ai_call 观测记录。"""

    # 时间戳（秒级浮点数，UTC），也有 ISO 字符串供展示
    ts: float
    ts_iso: str
    ai_stage: str  # "follow_up" | "final"
    session_id: str
    user_id: str | None
    ai_round_1based: int | None
    preference_context_used: bool
    preference_context_snapshot_count: int
    preference_context_chars: int
    preference_context_lines: int
    system_prompt_chars: int
    user_prompt_chars: int
    total_prompt_chars: int
    ai_outcome: str  # "ok" | "fallback_rules_engine" | "fail"
    ai_fail_code: str | None
    final_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "ts_iso": self.ts_iso,
            "ai_stage": self.ai_stage,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "ai_round_1based": self.ai_round_1based,
            "preference_context_used": self.preference_context_used,
            "preference_context_snapshot_count": self.preference_context_snapshot_count,
            "preference_context_chars": self.preference_context_chars,
            "preference_context_lines": self.preference_context_lines,
            "system_prompt_chars": self.system_prompt_chars,
            "user_prompt_chars": self.user_prompt_chars,
            "total_prompt_chars": self.total_prompt_chars,
            "ai_outcome": self.ai_outcome,
            "ai_fail_code": self.ai_fail_code,
            "final_reason": self.final_reason,
        }

    @classmethod
    def from_record(cls, record: logging.LogRecord) -> "AiCallMetaRecord | None":
        """从 logging.LogRecord 中抽取 ai_call 字段（通过 record.__dict__['extra']）。

        返回 None 表示这条 record 不是 ai_call_meta。
        """
        if not isinstance(getattr(record, "msg", None), str):
            return None
        msg: str = record.msg
        # 只处理 _log_ai_call_meta 固定格式的 info 前缀
        if not msg.startswith("ai_call "):
            return None
        from datetime import datetime, timezone

        ts = record.created
        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        def _pick(keys: Iterable[str], default: Any) -> Any:
            d = record.__dict__
            for k in keys:
                if k in d:
                    return d[k]
            return default

        ai_stage = str(_pick(("ai_call_stage",), "unknown"))
        session_id = str(_pick(("session_id",), ""))
        user_id_raw = _pick(("user_id",), None)
        user_id = None if user_id_raw is None else str(user_id_raw)
        ai_round = _pick(("ai_round_1based",), None)
        ai_round_1based = int(ai_round) if isinstance(ai_round, int) else None
        pref_used = bool(_pick(("preference_context_used",), False))
        pref_snaps = int(_pick(("preference_context_snapshot_count",), 0) or 0)
        pref_chars = int(_pick(("preference_context_chars",), 0) or 0)
        pref_lines = int(_pick(("preference_context_lines",), 0) or 0)
        sys_chars = int(_pick(("system_prompt_chars",), 0) or 0)
        user_chars = int(_pick(("user_prompt_chars",), 0) or 0)
        total_chars = int(_pick(("total_prompt_chars",), 0) or 0)
        if total_chars == 0 and (sys_chars or user_chars):
            total_chars = sys_chars + user_chars
        outcome = str(_pick(("ai_outcome",), "unknown"))
        fail = _pick(("ai_fail_code",), None)
        ai_fail_code = None if fail is None else str(fail)
        reason = _pick(("final_reason",), None)
        final_reason = None if reason is None else str(reason)
        return cls(
            ts=ts,
            ts_iso=ts_iso,
            ai_stage=ai_stage,
            session_id=session_id,
            user_id=user_id,
            ai_round_1based=ai_round_1based,
            preference_context_used=pref_used,
            preference_context_snapshot_count=pref_snaps,
            preference_context_chars=pref_chars,
            preference_context_lines=pref_lines,
            system_prompt_chars=sys_chars,
            user_prompt_chars=user_chars,
            total_prompt_chars=total_chars,
            ai_outcome=outcome,
            ai_fail_code=ai_fail_code,
            final_reason=final_reason,
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AiCallMetaRecord":
        return cls(
            ts=float(d.get("ts") or 0.0),
            ts_iso=str(d.get("ts_iso") or ""),
            ai_stage=str(d.get("ai_stage") or "unknown"),
            session_id=str(d.get("session_id") or ""),
            user_id=str(d["user_id"]) if d.get("user_id") is not None else None,
            ai_round_1based=int(d["ai_round_1based"]) if d.get("ai_round_1based") is not None else None,
            preference_context_used=bool(d.get("preference_context_used")),
            preference_context_snapshot_count=int(d.get("preference_context_snapshot_count") or 0),
            preference_context_chars=int(d.get("preference_context_chars") or 0),
            preference_context_lines=int(d.get("preference_context_lines") or 0),
            system_prompt_chars=int(d.get("system_prompt_chars") or 0),
            user_prompt_chars=int(d.get("user_prompt_chars") or 0),
            total_prompt_chars=int(d.get("total_prompt_chars") or 0),
            ai_outcome=str(d.get("ai_outcome") or "unknown"),
            ai_fail_code=str(d["ai_fail_code"]) if d.get("ai_fail_code") is not None else None,
            final_reason=str(d["final_reason"]) if d.get("final_reason") is not None else None,
        )


class AiCallMetaStore:
    """进程内单例缓冲（deque maxlen）+ 可选 JSONL 追加落盘。"""

    _instance: "AiCallMetaStore | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, maxlen: int, persist_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._buf: deque[AiCallMetaRecord] = deque(maxlen=maxlen)
        self._persist_path = persist_path
        self._persist_fh = None
        if persist_path is not None:
            try:
                persist_path.parent.mkdir(parents=True, exist_ok=True)
                self._persist_fh = persist_path.open("a", encoding="utf-8", buffering=1)
            except Exception:  # noqa: BLE001
                # 落盘失败不影响缓冲 & 不影响主流程
                self._persist_fh = None

    @classmethod
    def instance(cls) -> "AiCallMetaStore":
        """未初始化前返回空 store（仅用于 test/冷启动场景）。"""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = AiCallMetaStore(maxlen=2000, persist_path=None)
            return cls._instance

    @classmethod
    def configure(cls, settings: Settings) -> "AiCallMetaStore":
        """首次（应用启动）配置单例，并尝试从 JSONL 回填最近部分记录。"""
        log_dir = Path(settings.log_dir)
        if not log_dir.is_absolute():
            log_dir = Path.cwd() / log_dir
        persist_path: Path | None = None
        if settings.ai_call_meta_file.strip():
            persist_path = log_dir / settings.ai_call_meta_file.strip()
        store = AiCallMetaStore(maxlen=settings.ai_stats_buffer_size, persist_path=persist_path)
        # 回填：读 jsonl 最后 (maxlen * 2) 行里能解析的
        if persist_path is not None and persist_path.exists():
            try:
                raw = persist_path.read_text(encoding="utf-8").splitlines()
                take = raw[-settings.ai_stats_buffer_size:] if len(raw) > settings.ai_stats_buffer_size else raw
                with store._lock:  # noqa: SLF001
                    for line in take:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                            store._buf.append(AiCallMetaRecord.from_dict(d))  # noqa: SLF001
                        except Exception:  # noqa: BLE001
                            continue
            except Exception:  # noqa: BLE001
                # 回填失败不影响
                pass
        with cls._instance_lock:
            cls._instance = store
        return store

    # --------------------- 写入口 ---------------------
    def push_log_record(self, record: logging.LogRecord) -> None:
        rec = AiCallMetaRecord.from_record(record)
        if rec is None:
            return
        self.push(rec)

    def push(self, rec: AiCallMetaRecord) -> None:
        with self._lock:
            self._buf.append(rec)
            if self._persist_fh is not None:
                try:
                    self._persist_fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
                except Exception:  # noqa: BLE001
                    # 写文件失败不影响内存缓冲
                    pass

    # --------------------- 读入口 ---------------------
    def snapshot(self, limit: int | None = None) -> list[AiCallMetaRecord]:
        """返回最近 N 条（按时间升序）。"""
        with self._lock:
            items = list(self._buf)
        if limit is not None and limit >= 0 and limit < len(items):
            return items[-limit:]
        return items


class AiCallMetaLogHandler(logging.Handler):
    """挂到 `app.services.recommendation_session` logger 的 handler。"""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            AiCallMetaStore.instance().push_log_record(record)
        except Exception:  # noqa: BLE001
            # 观测链路失败绝不影响主业务
            pass


def configure_ai_call_logging(settings: Settings) -> None:
    """启动期一次性初始化：配置单例 store + 挂 handler 到观测源 logger。"""
    AiCallMetaStore.configure(settings)
    logger_names = (
        # 主要来源：RecommendationSession._log_ai_call_meta
        "app.services.recommendation_session",
    )
    handler = AiCallMetaLogHandler(level=logging.INFO)
    for name in logger_names:
        lg = logging.getLogger(name)
        lg.addHandler(handler)
        # 保证 ai_call 的 INFO 不会被父 logger 级别截断
        if lg.level == logging.NOTSET or lg.level > logging.INFO:
            lg.setLevel(logging.INFO)


def compute_stats(records: list[AiCallMetaRecord]) -> dict[str, Any]:
    """给定最近 N 条记录，计算仪表盘汇总。"""
    from collections import Counter

    total = len(records)
    if total == 0:
        return {
            "queried_records": 0,
            "window": {"oldest_ts": None, "newest_ts": None},
            "pref_context_used_rate": 0.0,
            "avg_snapshot_count_used": 0.0,
            "avg_total_prompt_chars": 0,
            "breakdown_by_stage": {},
            "outcome_breakdown": {},
            "sample_records": [],
        }
    sorted_recs = sorted(records, key=lambda r: r.ts)
    oldest, newest = sorted_recs[0], sorted_recs[-1]
    n_pref_used = sum(1 for r in sorted_recs if r.preference_context_used)
    # 只在 pref_used 为真时计算平均快照数
    snaps = [r.preference_context_snapshot_count for r in sorted_recs if r.preference_context_used]
    total_prompts = [r.total_prompt_chars for r in sorted_recs]
    by_stage: dict[str, dict[str, Any]] = {}
    for r in sorted_recs:
        entry = by_stage.setdefault(
            r.ai_stage,
            {"calls": 0, "pref_used_count": 0, "sum_pref_snaps": 0, "sum_total_prompt_chars": 0},
        )
        entry["calls"] += 1
        if r.preference_context_used:
            entry["pref_used_count"] += 1
            entry["sum_pref_snaps"] += r.preference_context_snapshot_count
        entry["sum_total_prompt_chars"] += r.total_prompt_chars
    stage_summary: dict[str, dict[str, Any]] = {}
    for stage, e in by_stage.items():
        calls = e["calls"]
        pref_used_count = e["pref_used_count"]
        stage_summary[stage] = {
            "calls": calls,
            "pref_used_rate": round(pref_used_count / calls, 4) if calls else 0.0,
            "avg_pref_snaps": round(e["sum_pref_snaps"] / pref_used_count, 3) if pref_used_count else 0.0,
            "avg_total_prompt_chars": round(e["sum_total_prompt_chars"] / calls) if calls else 0,
        }
    outcome_counter = Counter(r.ai_outcome for r in sorted_recs)
    outcome_breakdown = dict(outcome_counter)
    # sample_records：最近 5 条（时间倒序）
    sample = [r.to_dict() for r in sorted_recs[-5:][::-1]]
    return {
        "queried_records": total,
        "window": {
            "oldest_ts": oldest.ts_iso,
            "newest_ts": newest.ts_iso,
            "oldest_epoch": oldest.ts,
            "newest_epoch": newest.ts,
        },
        "pref_context_used_rate": round(n_pref_used / total, 4),
        "avg_snapshot_count_used": round(sum(snaps) / len(snaps), 3) if snaps else 0.0,
        "avg_total_prompt_chars": round(sum(total_prompts) / total) if total else 0,
        "breakdown_by_stage": stage_summary,
        "outcome_breakdown": outcome_breakdown,
        "sample_records": sample,
    }
