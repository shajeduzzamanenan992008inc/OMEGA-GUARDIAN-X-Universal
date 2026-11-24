"""Regression: the real transport must not read an unbounded response body.

``HttpTransport`` previously read the entire body before comparing its length
with ``max_response_bytes``, so a hostile endpoint could exhaust memory before
the bound was applied. The real urllib path now reads ``limit + 1`` bytes.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from ogx.platform.errors import ResponseTooLargeError
from ogx.platform.transport import HttpTransport


class _FakeResponse:
    status = 200
    headers = {}

    def __init__(self, payload: bytes):
        self.payload = payload
        self.read_amount: int | None = None

    def read(self, amount: int = -1) -> bytes:
        self.read_amount = amount
        return self.payload if amount is None or amount < 0 else self.payload[:amount]

    def geturl(self) -> str:
        return "http://127.0.0.1:9/data"

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeOpener:
    def __init__(self, response):
        self.response = response

    def open(self, request, timeout=None):
        return self.response


class TransportBoundTests(unittest.TestCase):
    def test_real_transport_reads_at_most_limit_plus_one(self):
        response = _FakeResponse(b"x" * 10_000)
        transport = HttpTransport(
            allow_loopback_http=True,
            max_response_bytes=1024,
            max_retries=0,
        )
        with patch(
            "ogx.platform.transport.urllib.request.build_opener",
            return_value=_FakeOpener(response),
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
        transport = HttpTransport(
            allow_loopback_http=True,
            max_response_bytes=1024,
            max_retries=0,
        )
        with patch(
            "ogx.platform.transport.urllib.request.build_opener",
            return_value=_FakeOpener(response),
        ):
            result = transport.send(
                "GET",
                "http://127.0.0.1:9/data",
                expected_host="127.0.0.1",
            )

        self.assertEqual(result.body, b"ok")
        self.assertEqual(response.read_amount, 1025)


if __name__ == "__main__":
    unittest.main()
