"""日志配置与脱敏过滤器。

脱敏 Filter 必须挂在 `logging.getLogger("app")` 这一个 logger 上：
- Python 的 `Logger.filter` 只检查当前 logger 自身的过滤器，子 logger 不继承；
- 挂 logger 而不是 handler，pytest `caplog`（挂在 root handler）才能看到脱敏后的文本。

脱敏对象：敏感值、邮箱、经纬度键值与裸坐标、`Authorization: Bearer`。
"""

import logging
import re
from collections.abc import Mapping

from app.core.config import Settings

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_LAT_RE = re.compile(r"\b(?:latitude|lat)\s*[:=]\s*[-\d.]+", re.IGNORECASE)
_LNG_RE = re.compile(r"\b(?:longitude|lon|lng)\s*[:=]\s*[-\d.]+", re.IGNORECASE)
# 6 位以上小数的裸坐标；阈值防止误伤版本号如 0.1.0
_COORD_RE = re.compile(r"\b-?\d{1,3}\.\d{5,}\b")
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+")

_STD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }
)


def _redact_email(match: re.Match[str]) -> str:
    email = match.group(0)
    local, _, domain = email.partition("@")
    if not local or not domain:
        return email
    return f"{local[0]}***@{domain}"


class RedactFilter(logging.Filter):
    """把日志中的敏感信息替换为占位符。"""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        super().__init__()
        # 只收集非空且足够长的值，避免把空串/占位符误伤
        self._secrets = [value for value in secrets.values() if value and len(value) >= 4]

    def _redact(self, text: str) -> str:
        result = text
        for secret in self._secrets:
            result = result.replace(secret, "[REDACTED]")
        result = _EMAIL_RE.sub(_redact_email, result)
        result = _LAT_RE.sub("[REDACTED]", result)
        result = _LNG_RE.sub("[REDACTED]", result)
        result = _COORD_RE.sub("[REDACTED]", result)
        result = _BEARER_RE.sub(r"\1[REDACTED]", result)
        return result

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args and isinstance(record.args, tuple):
            record.args = tuple(
                self._redact(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        for key in list(record.__dict__):
            if key in _STD_ATTRS or key.startswith("_"):
                continue
            value = getattr(record, key)
            if isinstance(value, str):
                setattr(record, key, self._redact(value))
        return True


class StructuredFormatter(logging.Formatter):
    """`时间 | LEVEL | logger | 消息`，异常时追加 traceback。"""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        line = f"{timestamp} | {record.levelname:<7} | {record.name} | {record.getMessage()}"
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line = f"{line}\n{record.exc_text}"
        return line


def configure_logging(settings: Settings) -> None:
    """配置项目统一 logger `app`。幂等：重复调用不会重复添加过滤器/处理器。"""
    logger = logging.getLogger("app")
    logger.setLevel(logging.DEBUG if settings.app_env == "development" else logging.INFO)
    if not any(isinstance(item, RedactFilter) for item in logger.filters):
        logger.addFilter(RedactFilter(settings.secret_values))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
    # 关闭 uvicorn 默认访问日志，避免与我们的访问日志重复
    logging.getLogger("uvicorn.access").disabled = True
