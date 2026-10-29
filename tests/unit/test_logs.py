import json
import logging

from esign.logs import JsonFormatter, request_id_var


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        "esign.test", logging.INFO, __file__, 1, "hello %s", ("world",), None
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formats_as_json_with_extras() -> None:
    line = JsonFormatter().format(make_record(envelope_id="env-1"))
    entry = json.loads(line)
    assert entry["message"] == "hello world"
    assert entry["level"] == "INFO"
    assert entry["envelope_id"] == "env-1"
    assert "request_id" not in entry


def test_includes_request_id_from_context() -> None:
    token = request_id_var.set("req-123")
    try:
        entry = json.loads(JsonFormatter().format(make_record()))
    finally:
        request_id_var.reset(token)
    assert entry["request_id"] == "req-123"


def test_includes_exception_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    entry = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in entry["exception"]
