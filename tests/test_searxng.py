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

    @patch("httpx.get")
    def test_search_searxng_published_date(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "results": [
                {
                    "title": "Article with Date",
                    "url": "https://news.example.com/article1",
                    "content": "News story content",
                    "publishedDate": "2026-08-07T14:30:00Z",
                    "score": 2.0,
                },
                {
                    "title": "Article without Date",
                    "url": "https://example.com/page",
                    "content": "Static page content",
                    "score": 1.0,
                },
            ],
            "unresponsive_engines": [],
        }
        mock_get.return_value = mock_resp

        res = search_searxng(self.config, query="test query", limit=5)
        self.assertTrue(res["success"])
        self.assertEqual(len(res["results"]), 2)
        # First result has published_date
        self.assertEqual(res["results"][0]["published_date"], "2026-08-07 14:30:00 UTC")
        # Second result omits published_date
        self.assertNotIn("published_date", res["results"][1])

    @patch("httpx.get")
    def test_search_searxng_snippet_date_fallback(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "results": [
                {
                    "title": "Google Style Result",
                    "url": "https://example.com/google",
                    "content": "Jul 1, 2026 ... Analysis of the SpaceX stock price stability.",
                    "score": 3.0,
                },
                {
                    "title": "Dateline Style Result",
                    "url": "https://example.com/dateline",
                    "content": "Jerusalem, Israel (February 10, 2026) Israel is moving ahead with plans.",
                    "score": 2.0,
                },
                {
                    "title": "Relative Time Result",
                    "url": "https://example.com/relative",
                    "content": "2 hours ago · SpaceX confirms booster recovery status.",
                    "score": 1.0,
                },
            ],
            "unresponsive_engines": [],
        }
        mock_get.return_value = mock_resp

        res = search_searxng(self.config, query="test query", limit=5)
        self.assertTrue(res["success"])
        self.assertEqual(len(res["results"]), 3)
        self.assertEqual(res["results"][0]["published_date"], "Jul 1, 2026")
        self.assertEqual(res["results"][0]["snippet"], "Analysis of the SpaceX stock price stability.")
        self.assertEqual(res["results"][1]["published_date"], "February 10, 2026")
        self.assertEqual(res["results"][1]["snippet"], "Jerusalem, Israel - Israel is moving ahead with plans.")
        self.assertEqual(res["results"][2]["published_date"], "2 hours ago")
        self.assertEqual(res["results"][2]["snippet"], "SpaceX confirms booster recovery status.")

    def test_unknown_config_keys_ignored(self):
        raw_dict = {
            "search": {
                "ban_detector": {"default": {"suspend_time": 300}},
                "engines": ["google", "bing"],
            }
        }
        config = ForageConfig.from_dict(raw_dict, source_path="test")
        self.assertEqual(config.search.engines, ("google", "bing"))

    def test_default_engines_and_legacy_engines_backwards_compatibility(self):
        """Verify that legacy 'engines:' and new 'default_engines:' both work identically."""
        # 1. Test legacy 'engines:' key in YAML dict
        cfg_legacy = ForageConfig.from_dict({"search": {"engines": ["google", "bing"]}}, source_path="test")
        self.assertEqual(cfg_legacy.search.engines, ("google", "bing"))
        self.assertEqual(cfg_legacy.search.default_engines, ("google", "bing"))

        # 2. Test new 'default_engines:' key in YAML dict
        cfg_new = ForageConfig.from_dict({"search": {"default_engines": ["duckduckgo", "brave"]}}, source_path="test")
        self.assertEqual(cfg_new.search.engines, ("duckduckgo", "brave"))
        self.assertEqual(cfg_new.search.default_engines, ("duckduckgo", "brave"))

        # 3. Test legacy FORAGE_SEARCH_ENGINES env var
        import os
        with patch.dict(os.environ, {"FORAGE_SEARCH_ENGINES": "qwant,startpage"}, clear=False):
            cfg_env = load_config()
            self.assertEqual(cfg_env.search.engines, ("qwant", "startpage"))
            self.assertEqual(cfg_env.search.default_engines, ("qwant", "startpage"))

    def test_env_variable_overrides(self):
        """Verify environment variables override default and YAML configuration."""
        import os
        env_patches = {
            "FORAGE_PORT": "4000",
            "FORAGE_LOG_LEVEL": "debug",
            "FORAGE_SEARXNG_URL": "http://custom-searxng:8080",
            "FORAGE_DEFAULT_ENGINES": "bing,brave",
            "FORAGE_AVAILABLE_ENGINES": "bing,brave,wikipedia,github",
            "FORAGE_BROWSER_ENGINE": "scrapling",
            "FORAGE_EXTRACT_ENGINE": "readability",
            "FORAGE_REQUIRE_MAX_CHARS": "true",
            "FORAGE_AUTH_ENABLED": "true",
        }
        with patch.dict(os.environ, env_patches, clear=False):
            cfg = load_config()
            self.assertEqual(cfg.server.port, 4000)
            self.assertEqual(cfg.server.log_level, "debug")
            self.assertEqual(cfg.search.searxng_url, "http://custom-searxng:8080")
            self.assertEqual(cfg.search.engines, ("bing", "brave"))
            self.assertEqual(cfg.search.default_engines, ("bing", "brave"))
            self.assertEqual(cfg.search.available_engines, ("bing", "brave", "wikipedia", "github"))
            self.assertEqual(cfg.browser.engine, "scrapling")
            self.assertEqual(cfg.extract.engine, "readability")
            self.assertTrue(cfg.extract.require_max_chars)
            self.assertTrue(cfg.auth.enabled)

    def test_auto_seed_empty_mounted_directory(self):
        """Verify empty mounted directory gets auto-seeded with config.yaml and prompts.yaml, and regenerates on deletion."""
        import os
        import stat
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. First run on empty directory: both files created
            cfg = load_config(tmpdir)
            self.assertIsNotNone(cfg)
            config_file = os.path.join(tmpdir, "config.yaml")
            prompts_file = os.path.join(tmpdir, "prompts.yaml")
            self.assertTrue(os.path.exists(config_file))
            self.assertTrue(os.path.exists(prompts_file))

            # 2. Delete prompts.yaml: regenerated on next load
            os.remove(prompts_file)
            self.assertFalse(os.path.exists(prompts_file))
            cfg2 = load_config(tmpdir)
            self.assertIsNotNone(cfg2)
            self.assertTrue(os.path.exists(prompts_file))

            # 3. Read-only directory: logs warning and uses defaults without crashing
            os.chmod(tmpdir, stat.S_IREAD | stat.S_IEXEC)
            try:
                cfg_ro = load_config(tmpdir)
                self.assertIsNotNone(cfg_ro)
            finally:
                os.chmod(tmpdir, stat.S_IRWXU)

    @patch("httpx.get")
    def test_fetch_searxng_engines_live(self, mock_get):
        from app.searxng import fetch_searxng_engines_sync
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "engines": [
                {"name": "360search", "categories": ["general"], "enabled": False},
                {"name": "google", "categories": ["general"], "enabled": True},
                {"name": "bing", "categories": ["general"], "enabled": True},
                {"name": "bing images", "categories": ["images"], "enabled": True},
                {"name": "adobe stock audio", "categories": ["music", "audio"], "enabled": True},
                {"name": "btdigg", "categories": ["files"], "enabled": True},
                {"name": "wikipedia", "categories": ["general"], "enabled": False},
                {"name": "github", "categories": ["it"], "enabled": True},
                {"name": "duckduckgo", "categories": ["general"], "enabled": True},
                {"name": "youtube", "categories": ["videos", "general"], "enabled": True},
            ]
        }
        mock_get.return_value = mock_resp

        avail, gen = fetch_searxng_engines_sync("http://mock-searxng:8080")
        self.assertEqual(avail, ("google", "bing", "duckduckgo", "youtube"))
        self.assertEqual(gen, ("google", "bing", "duckduckgo", "youtube"))

    @patch("httpx.get")
    def test_get_live_available_engines_fallback(self, mock_get):
        from app.searxng import DEFAULT_AVAILABLE_ENGINES, get_live_available_engines
        mock_get.side_effect = Exception("SearXNG unreachable")
        avail = get_live_available_engines(self.config)
        self.assertEqual(avail, DEFAULT_AVAILABLE_ENGINES)

        # Test explicit config override
        cfg_custom = ForageConfig.from_dict({"search": {"available_engines": ["google", "duckduckgo"]}}, source_path="test")
        avail_custom = get_live_available_engines(cfg_custom)
        self.assertEqual(avail_custom, ("google", "duckduckgo"))


if __name__ == "__main__":
    unittest.main()
