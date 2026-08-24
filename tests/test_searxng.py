"""Unit tests for SearXNG client normalization, engine alias resolution, and error guidance."""

import unittest
from unittest.mock import MagicMock, patch

from app.config import ForageConfig, load_config
from app.searxng import normalize_and_validate_engines, search_searxng


class TestSearXNG(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_normalize_and_validate_engines_alias(self):
        default_engines = ("google", "bing")
        available_engines = ("google", "bing", "duckduckgo", "brave", "wikipedia")

        # Test valid alias
        engines, warning = normalize_and_validate_engines(
            ["ddg", "google_search"],
            default_engines,
            available_engines,
        )
        self.assertEqual(engines, ["duckduckgo", "google"])
        self.assertIsNone(warning)

    def test_normalize_and_validate_engines_invalid(self):
        default_engines = ("google", "bing")
        available_engines = ("google", "bing", "duckduckgo")

        # Test completely invalid engines
        engines, warning = normalize_and_validate_engines(
            ["invalid_engine_xyz"],
            default_engines,
            available_engines,
        )
        self.assertEqual(engines, ["google", "bing"])
        self.assertIsNotNone(warning)
        self.assertIn("None of the requested engines", warning)

    @patch("httpx.get")
    def test_search_searxng_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "results": [
                {
                    "title": "Example Title",
                    "url": "https://example.com",
                    "content": "Example snippet content",
                    "score": 1.5,
                }
            ],
            "unresponsive_engines": [],
        }
        mock_get.return_value = mock_resp

        res = search_searxng(self.config, query="test query", limit=5)
        self.assertTrue(res["success"])
        self.assertEqual(len(res["results"]), 1)
        self.assertEqual(res["results"][0]["title"], "Example Title")
        self.assertIn("Search retrieved 1 result(s)", res["content"])

    @patch("httpx.get")
    def test_search_searxng_unresponsive_engines(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "results": [],
            "unresponsive_engines": [["google", "Timeout"], ["bing", "HTTP 429"]],
        }
        mock_get.return_value = mock_resp

        res = search_searxng(self.config, query="test query", limit=5)
        self.assertTrue(res["success"])
        self.assertEqual(len(res["results"]), 0)
        self.assertIsNotNone(res["warning"])
        self.assertIn("google (Timeout)", res["warning"])
        self.assertIn("Do NOT hammer", res["warning"])


if __name__ == "__main__":
    unittest.main()
