from __future__ import annotations

import contextvars

from ogx.api.tracing import (
    AlertEvaluator,
    AlertRule,
    NoOpExporter,
    Sampler,
    Span,
    TraceContext,
    Tracer,
    current_trace,
    extract_headers,
    inject_headers,
    redact,
    reset_trace,
    set_current_trace,
)


def test_traceparent_roundtrip():
    ctx = TraceContext()
    tp = ctx.to_traceparent()
    parsed = TraceContext.from_traceparent(tp)
    assert parsed is not None
    assert parsed.trace_id == ctx.trace_id
    assert parsed.span_id == ctx.span_id
    assert parsed.sampled is True


def test_traceparent_invalid():
    assert TraceContext.from_traceparent("") is None
    assert TraceContext.from_traceparent("garbage") is None
    assert TraceContext.from_traceparent("00-abc-def-01") is None


def test_child_context():
    parent = TraceContext()
    child = parent.child()
    assert child.trace_id == parent.trace_id
    assert child.parent_span_id == parent.span_id
    assert child.span_id != parent.span_id


def test_propagation_headers():
    ctx = TraceContext(sampled=False)
    token = set_current_trace(ctx)
    try:
        headers = inject_headers({"x-custom": "v"})
        assert "traceparent" in headers
        assert headers["x-custom"] == "v"
        extracted = extract_headers(headers)
        assert extracted is not None
        assert extracted.trace_id == ctx.trace_id
        assert extracted.sampled is False
    finally:
        reset_trace(token)


def test_current_trace_contextvar():
    assert current_trace() is None
    ctx = TraceContext()
    token = set_current_trace(ctx)
    try:
        assert current_trace() is ctx
    finally:
        reset_trace(token)
    assert current_trace() is None


def test_sampler_modes():
    assert Sampler("always").decide() is True
    assert Sampler("never").decide() is True is False or Sampler("never").decide() is False
    s = Sampler("ratio", ratio=0.0)
    assert s.decide() is False
    s2 = Sampler("ratio", ratio=1.0)
    assert s2.decide() is True


def test_redact_sensitive():
    data = {
        "authorization": "Bearer secret",
        "nested": {"password": "p", "safe": "ok", "token": "t"},
        "items": [{"secret": "x", "value": 5}],
        "body": "request body here",
    }
    redacted = redact(data)
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "ok"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["items"][0]["secret"] == "[REDACTED]"
    assert redacted["items"][0]["value"] == 5
    assert redacted["body"] == "[REDACTED]"


def test_redact_truncates_long_strings():
    long = "a" * 200
    out = redact(long, max_string=10)
    assert out.endswith("...[truncated]")
    assert len(out) <= 30


def test_tracer_span_lifecycle():
    tracer = Tracer(service_name="test", exporter=NoOpExporter())
    span = tracer.start_span("op", attributes={"k": "v"})
    assert span.name == "op"
    assert current_trace() is span.context
    tracer.end_span(span, status="ok")
    assert span.ended_at is not None
    assert span.status == "ok"
    recent = tracer.recent_spans(5)
    assert len(recent) == 1
    assert recent[0]["name"] == "op"


def test_tracer_sampled_export_does_not_raise():
    class BoomExporter:
        def export_span(self, span):
            raise RuntimeError("boom")
        def export_metrics(self, payload):
            return True
        def shutdown(self):
            pass

    tracer = Tracer(exporter=BoomExporter(), sampler=Sampler("always"))
    span = tracer.start_span("x")
    tracer.end_span(span)  # must not raise


def test_alert_rule_evaluate():
    rule = AlertRule(name="high_errors", metric="requests_total", operator=">", threshold=5)
    assert rule.evaluate(10) is True
    assert rule.evaluate(3) is False
    bad = AlertRule(name="x", metric="m", operator=">=", threshold=5)
    assert bad.evaluate(5) is True


def test_alert_evaluator():
    rules = [AlertRule(name="r1", metric="error_codes.invalid_token", operator=">", threshold=0)]
    ev = AlertEvaluator(rules)
    fired = ev.evaluate({"error_codes": {"invalid_token": 3}})
    assert len(fired) == 1
    assert fired[0]["rule"] == "r1"
    assert ev.rules()[0].name == "r1"


def test_alert_evaluator_no_fire():
    ev = AlertEvaluator([AlertRule(name="r", metric="x", operator=">", threshold=10)])
    assert ev.evaluate({"x": 5}) == []


def test_span_redacts_attributes():
    span = Span(name="s", context=TraceContext())
    span.attributes = {"password": "secret", "count": 3}
    d = span.as_dict()
    assert d["attributes"]["password"] == "[REDACTED]"
    assert d["attributes"]["count"] == 3
