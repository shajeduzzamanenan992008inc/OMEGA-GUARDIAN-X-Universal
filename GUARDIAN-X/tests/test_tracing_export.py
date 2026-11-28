"""Live loopback tests for OTLP export and webhook alert delivery (ENT-09).

The OTLP collector and the alert receiver are real HTTP servers started by the
test on loopback. Nothing is mocked: the exporter and alerter open sockets,
serialize OTLP/JSON or signed alert JSON, and the receiver inspects the bytes.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from ogx.api.server import ApiContext, create_server
from ogx.api.tracing import (
    AlertEvaluator,
    AlertDispatcher,
    AlertRule,
    DeliveryResult,
    HttpOTLPExporter,
    Tracer,
    WebhookAlerter,
    sign_alert_payload,
    verify_alert_signature,
)
from ogx.enterprise.store import EnterpriseStore
from ogx.platform.errors import InvalidEndpointError


class _Receiver(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        return None

    def do_POST(self):  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length)
        try:
            payload = json.loads(body) if body else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        record = {
            "path": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": body,
            "json": payload,
        }
        with self.server.lock:  # type: ignore[attr-defined]
            self.server.requests.append(record)  # type: ignore[attr-defined]
            status = (self.server.responses.pop(0)  # type: ignore[attr-defined]
                      if self.server.responses else 200)  # type: ignore[attr-defined]
        response = b'{"ok":true}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)
        self.close_connection = True


@pytest.fixture()
def receiver():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    server.requests = []  # type: ignore[attr-defined]
    server.responses = []  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield {"base": base, "server": server}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _requests(receiver):
    with receiver["server"].lock:
        return list(receiver["server"].requests)


def _api_server(tmp_path, *, tracer=None):
    store = EnterpriseStore(tmp_path / "enterprise.sqlite3")
    store.initialize()
    context = ApiContext(store=store, rate_limit=1000, tracer=tracer)
    server = create_server(context, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return context, server, thread, base


def _get(base, path):
    parsed = urlsplit(base)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def test_http_otlp_exporter_posts_real_trace_json(receiver):
    exporter = HttpOTLPExporter(receiver["base"], service_name="ogx-test",
                                allow_loopback_http=True, timeout=5)
    tracer = Tracer(service_name="ogx-test", exporter=exporter)
    try:
        span = tracer.start_span("unit.work", attributes={
            "authorization": "Bearer super-secret",
            "attempt": 2,
            "ok": True,
        })
        tracer.end_span(span, attributes={"result.count": 3})
        assert exporter.flush(timeout=5)
        requests = _requests(receiver)
        assert len(requests) == 1
        request = requests[0]
        assert request["path"] == "/v1/traces"
        assert request["headers"]["content-type"] == "application/json"
        payload = request["json"]
        resource = payload["resourceSpans"][0]
        resource_attrs = {item["key"]: item["value"]
                          for item in resource["resource"]["attributes"]}
        assert resource_attrs["service.name"] == {"stringValue": "ogx-test"}
        span_payload = resource["scopeSpans"][0]["spans"][0]
        assert span_payload["name"] == "unit.work"
        assert span_payload["traceId"] == span.context.trace_id
        assert span_payload["spanId"] == span.context.span_id
        assert span_payload["status"]["code"] == 1
        attributes = {item["key"]: item["value"]
                      for item in span_payload["attributes"]}
        assert attributes["authorization"] == {"stringValue": "[REDACTED]"}
        assert attributes["attempt"] == {"intValue": "2"}
        assert attributes["ok"] == {"boolValue": True}
        assert b"super-secret" not in request["body"]
        assert exporter.exported == 1 and exporter.failed == 0
    finally:
        exporter.shutdown()


def test_http_otlp_exporter_posts_metrics(receiver):
    exporter = HttpOTLPExporter(receiver["base"], service_name="ogx-test",
                                allow_loopback_http=True, timeout=5)
    try:
        assert exporter.export_metrics({
            "requests_total": 7,
            "routes": {"/api/v1/meta": {"count": 2}},
        })
        assert exporter.flush(timeout=5)
        request = _requests(receiver)[0]
        assert request["path"] == "/v1/metrics"
        metrics = request["json"]["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
        names = {item["name"]: item["gauge"]["dataPoints"][0]["asDouble"]
                 for item in metrics}
        assert names["requests_total"] == 7.0
        assert names["routes.api.v1.meta.count"] == 2.0
    finally:
        exporter.shutdown()


def test_http_otlp_exporter_rejects_unsafe_endpoint():
    with pytest.raises(InvalidEndpointError):
        HttpOTLPExporter("http://collector.example.com:4318")
    with pytest.raises(InvalidEndpointError):
        HttpOTLPExporter("http://169.254.169.254/latest/meta-data",
                         allow_loopback_http=True)


def test_http_otlp_exporter_does_not_follow_redirects(receiver):
    receiver["server"].responses.append(302)
    exporter = HttpOTLPExporter(receiver["base"], allow_loopback_http=True,
                                timeout=5, max_attempts=1)
    try:
        assert exporter.export_metrics({"requests_total": 1})
        assert exporter.flush(timeout=5)
        assert len(_requests(receiver)) == 1
        assert exporter.failed == 1
        assert "302" in (exporter.last_error or "")
    finally:
        exporter.shutdown()


def test_webhook_alert_signature_and_delivery(receiver):
    secret = "unit-test-secret-value-0123456789"
    alerter = WebhookAlerter(receiver["base"], secret, allow_loopback_http=True,
                             timeout=5)
    result = alerter.send([{
        "rule": "error-rate", "severity": "critical", "value": 9,
        "token": "must-not-leak",
    }], now=1_700_000_000)
    assert result.delivered is True
    assert result.status == 200
    assert result.attempts == 1
    request = _requests(receiver)[0]
    headers = request["headers"]
    assert headers["x-ogx-delivery"] == result.delivery_id
    assert verify_alert_signature(
        secret, headers["x-ogx-timestamp"], request["body"],
        headers["x-ogx-signature"], now=1_700_000_000,
    )
    assert not verify_alert_signature(
        secret, headers["x-ogx-timestamp"], request["body"],
        headers["x-ogx-signature"], now=1_700_000_400,
    )
    payload = request["json"]
    assert payload["schema"] == "ogx.alerts.v1"
    assert payload["alerts"][0]["rule"] == "error-rate"
    assert payload["alerts"][0]["token"] == "[REDACTED]"
    assert b"must-not-leak" not in request["body"]


def test_webhook_retries_server_errors_with_stable_delivery_id(receiver):
    receiver["server"].responses.extend([500, 200])
    alerter = WebhookAlerter(receiver["base"], "unit-test-secret-value-0123456789",
                             allow_loopback_http=True, timeout=5,
                             max_attempts=3, backoff=0)
    result = alerter.send([{"rule": "disk", "severity": "warning", "value": 91}])
    assert result.delivered is True
    assert result.attempts == 2
    requests = _requests(receiver)
    assert len(requests) == 2
    assert (requests[0]["headers"]["x-ogx-delivery"]
            == requests[1]["headers"]["x-ogx-delivery"])


def test_webhook_does_not_retry_client_errors(receiver):
    receiver["server"].responses.extend([400, 200])
    alerter = WebhookAlerter(receiver["base"], "unit-test-secret-value-0123456789",
                             allow_loopback_http=True, timeout=5,
                             max_attempts=3, backoff=0)
    result = alerter.send([{"rule": "disk", "severity": "warning", "value": 91}])
    assert result.delivered is False
    assert result.attempts == 1
    assert result.status == 400
    assert len(_requests(receiver)) == 1


def test_webhook_rejects_short_secret_and_unsafe_url():
    with pytest.raises(ValueError):
        WebhookAlerter("https://example.com/hook", "short")
    with pytest.raises(InvalidEndpointError):
        WebhookAlerter("http://example.com/hook", "unit-test-secret-value-0123456789")


def test_alert_dispatcher_delivers_fired_rules(receiver):
    alerter = WebhookAlerter(receiver["base"], "unit-test-secret-value-0123456789",
                             allow_loopback_http=True, timeout=5)
    evaluator = AlertEvaluator([
        AlertRule("errors", "error_codes.internal_error", ">", 0, "critical"),
        AlertRule("quiet", "error_codes.none", ">", 0),
    ])
    fired, result = AlertDispatcher(evaluator, alerter).dispatch({
        "error_codes": {"internal_error": 2, "none": 0},
    })
    assert [item["rule"] for item in fired] == ["errors"]
    assert isinstance(result, DeliveryResult)
    assert result.delivered is True
    body = _requests(receiver)[0]["json"]
    assert body["alerts"][0]["rule"] == "errors"


def test_api_requests_emit_spans(tmp_path):
    tracer = Tracer(service_name="ogx-api")
    context, server, thread, base = _api_server(tmp_path, tracer=tracer)
    try:
        assert _get(base, "/api/v1/meta") == 200
        spans = context.tracer.recent_spans()
        assert spans
        span = spans[-1]
        assert span["name"] == "http.request"
        assert span["status"] == "ok"
        assert span["attributes"]["http.route"] == "/api/v1/meta"
        assert span["attributes"]["http.method"] == "GET"
        assert span["attributes"]["http.status_code"] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        tracer.shutdown()


def test_api_exports_spans_to_loopback_collector(tmp_path, receiver, monkeypatch):
    monkeypatch.setenv("OGX_OTLP_ENDPOINT", receiver["base"])
    monkeypatch.setenv("OGX_OTLP_ALLOW_LOOPBACK_HTTP", "1")
    context, server, thread, base = _api_server(tmp_path)
    try:
        assert _get(base, "/api/v1/meta") == 200
        exporter = context.tracer.exporter
        assert isinstance(exporter, HttpOTLPExporter)
        assert exporter.flush(timeout=5)
        requests = _requests(receiver)
        assert requests and requests[-1]["path"] == "/v1/traces"
        payload = requests[-1]["json"]
        resource = payload["resourceSpans"][0]
        attrs = {item["key"]: item["value"]
                 for item in resource["resource"]["attributes"]}
        assert attrs["service.name"] == {"stringValue": "ogx-api"}
        span = resource["scopeSpans"][0]["spans"][0]
        assert span["name"] == "http.request"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        context.tracer.shutdown()
