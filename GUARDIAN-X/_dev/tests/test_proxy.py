import unittest

from ogx.runtime.proxy import _validated_origin_target


class ProxyTargetValidationTests(unittest.TestCase):
    def test_accepts_origin_form_path_and_query(self):
        self.assertEqual(_validated_origin_target("/search?q=shoes"), "/search?q=shoes")
        self.assertEqual(_validated_origin_target("/api/v1/items?x=%2F"), "/api/v1/items?x=%2F")
        self.assertEqual(_validated_origin_target("/api/v1/items?x=%252F"), "/api/v1/items?x=%252F")
        self.assertEqual(_validated_origin_target("/health"), "/health")
        self.assertEqual(_validated_origin_target("/search?q=hello%20world&a=1"), "/search?q=hello%20world&a=1")
        self.assertEqual(_validated_origin_target("/path?"), "/path?")
        self.assertEqual(_validated_origin_target("/path"), "/path")
        # Traversal remains visible to Shield.inspect, which owns attack blocking.
        self.assertEqual(_validated_origin_target("/x/../../etc/passwd"), "/x/../../etc/passwd")

    def test_rejects_absolute_and_network_path_references(self):
        for target in ("http://example.invalid/path", "https://example.invalid/", "//example.invalid/path", "///example.invalid/path"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                _validated_origin_target(target)

    def test_rejects_encoded_authority_paths(self):
        for target in ("/%2f%2fevil.invalid/path", "/%252f%252fevil.invalid/path"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                _validated_origin_target(target)

    def test_rejects_malformed_percent_encoding_in_query(self):
        for target in ("/path?x=%2G", "/path?x=%2x"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                _validated_origin_target(target)

    def test_rejects_ambiguous_or_control_characters(self):
        for target in ("/\\\\evil.invalid/path", "/path\\nInjected: yes", "/path#fragment", "/has space", "/bad\\x7fpath", "/bad%0apath", "/bad%20path"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                _validated_origin_target(target)


if __name__ == "__main__":
    unittest.main()
