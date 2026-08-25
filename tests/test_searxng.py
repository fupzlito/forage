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
        self.assertNotIn("favicon", res["results"][0])

    @patch("httpx.get")
    def test_search_searxng_default_limit_and_favicon(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        raw_items = [
            {"title": f"Title {i}", "url": f"https://example{i}.com", "content": f"Snippet {i}", "score": 10 - i}
            for i in range(15)
        ]
        mock_resp.json.return_value = {
            "results": raw_items,
            "unresponsive_engines": [],
        }
        mock_get.return_value = mock_resp

        # 1. Test default_limit (default 10) when limit=None
        res = search_searxng(self.config, query="test query", limit=None)
        self.assertEqual(len(res["results"]), 10)
        self.assertNotIn("favicon", res["results"][0])

        # 2. Test when include_favicon is enabled on search config
        cfg_with_fav = ForageConfig.from_dict({"search": {"include_favicon": True, "default_limit": 3}}, source_path="test")
        res_fav = search_searxng(cfg_with_fav, query="test query", limit=None)
        self.assertEqual(len(res_fav["results"]), 3)
        self.assertIn("favicon", res_fav["results"][0])
        self.assertTrue(res_fav["results"][0]["favicon"].startswith("https://www.google.com/s2/favicons"))

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
        self.assertIn("Do NOT hammer", res["warning"])
        self.assertEqual(res["unresponsive_engines"], "google - Timeout; bing - HTTP 429")
        self.assertEqual(res["returned_engines"], "")
        self.assertIn("google", res["requested_engines"])
        self.assertIn("google", res["all_engines"])

    def test_unknown_config_keys_ignored(self):
        raw_dict = {
            "search": {
                "ban_detector": {"default": {"suspend_time": 300}},
                "engines": ["google", "bing"],
            }
        }
        config = ForageConfig.from_dict(raw_dict, source_path="test")
        self.assertEqual(config.search.engines, ("google", "bing"))


if __name__ == "__main__":
    unittest.main()
