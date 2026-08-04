import logging

from app.core.logging import RedactFilter


def _make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="app",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )


def test_redact_filter_removes_secrets_emails_coords_bearer() -> None:
    redact = RedactFilter({"ai_api_key": "sk-live-12345", "amap_api_key": "amap-secret-999"})
    record = _make_record(
        "key=sk-live-12345 coord=30.123456,114.123456 "
        "mail=abc@example.com Authorization: Bearer tok123"
    )
    assert redact.filter(record)
    text = record.getMessage()
    assert "sk-live-12345" not in text
    assert "30.123456" not in text
    assert "114.123456" not in text
    assert "tok123" not in text
    assert "a***@example.com" in text
    assert "[REDACTED]" in text


def test_redact_filter_ignores_short_values() -> None:
    redact = RedactFilter({"placeholder": "abc"})
    record = _make_record("value=abc")
    assert redact.filter(record)
    assert "abc" in record.getMessage()


def test_redact_filter_handles_args() -> None:
    redact = RedactFilter({"sk-live-12345": "sk-live-12345"})
    record = logging.LogRecord(
        name="app",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="secret=%s",
        args=("sk-live-12345",),
        exc_info=None,
    )
    assert redact.filter(record)
    assert "sk-live-12345" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()
