"""Regression: the real transport must not read an unbounded response body.

``HttpTransport`` previously read the entire body before comparing its length
with ``max_response_bytes``, so a hostile endpoint could exhaust memory before
the bound was applied. The real request path now reads ``limit + 1`` bytes and
dials a pre-validated, pinned address.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from ogx.platform.errors import ResponseTooLargeError
from ogx.platform.transport import HttpTransport


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.read_amount: int | None = None
        self.status = 200

    def read(self, amount: int = -1) -> bytes:
        self.read_amount = amount
        return self.payload if amount is None or amount < 0 else self.payload[:amount]

    def getheaders(self):
        return [("Content-Type", "application/octet-stream")]


class _FakeConnection:
    last_kwargs: dict | None = None

    def __init__(self, host, port, **kwargs):
        type(self).last_kwargs = {"host": host, "port": port, **kwargs}
        self.host = host
        self.port = port
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        self.requested = (method, path, headers)

    def getresponse(self):
        return type(self).response

    def close(self):
        self.closed = True


class TransportBoundTests(unittest.TestCase):
    def _transport(self):
        return HttpTransport(
            allow_loopback_http=True,
            max_response_bytes=1024,
            max_retries=0,
        )

    def test_real_transport_reads_at_most_limit_plus_one(self):
        response = _FakeResponse(b"x" * 10_000)
        _FakeConnection.response = response
        transport = self._transport()

        with patch(
            "ogx.platform.transport._PinnedHTTPConnection",
            _FakeConnection,
        ):
            with self.assertRaises(ResponseTooLargeError):
                transport.send(
                    "GET",
                    "http://127.0.0.1:9/data",
                    expected_host="127.0.0.1",
                )

        self.assertEqual(response.read_amount, 1025)

    def test_response_within_limit_is_returned(self):
        response = _FakeResponse(b"ok")
        _FakeConnection.response = response
        transport = self._transport()

        with patch(
            "ogx.platform.transport._PinnedHTTPConnection",
            _FakeConnection,
        ):
            result = transport.send(
                "GET",
                "http://127.0.0.1:9/data",
                expected_host="127.0.0.1",
            )

        self.assertEqual(result.body, b"ok")
        self.assertEqual(response.read_amount, 1025)
        # The socket was dialed to a validated literal address, not a name.
        assert _FakeConnection.last_kwargs is not None
        self.assertEqual(_FakeConnection.last_kwargs["pinned_address"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
