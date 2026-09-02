"""Unit test for domain overrides with custom headers and cookies."""

import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from app.config import ForageConfig
from app.extract import extract_url


class TestDomainAuth(unittest.TestCase):
    @patch("app.extract.fetch_static", new_callable=AsyncMock)
    @patch("app.extract._is_ssrf_safe", new_callable=AsyncMock)
    async def _async_test(self, mock_ssrf, mock_fetch):
        mock_ssrf.return_value = True
        raw_dict = {
            "extract": {
                "domain_overrides": {
                    "internal.wiki.local": {
                        "headers": {"Authorization": "Bearer internal-token"},
                        "cookies": {"session": "abc-123"},
                    }
                }
            }
        }
        config = ForageConfig.from_dict(raw_dict, source_path="test")
        mock_fetch.return_value = ("<html><body><h1>Wiki</h1><p>Secret doc</p></body></html>", 200, "https://internal.wiki.local", "text/html")

        res = await extract_url(config, None, "https://internal.wiki.local/page")
        self.assertIn("Secret doc", res["content"])

        # Verify fetch_static received override headers and cookies
        mock_fetch.assert_called_once()
        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs["extra_headers"], {"Authorization": "Bearer internal-token"})
        self.assertEqual(kwargs["cookies"], {"session": "abc-123"})

    @patch("app.extract._try_reddit_extract", new_callable=AsyncMock)
    @patch("app.extract._is_ssrf_safe", new_callable=AsyncMock)
    async def _async_reddit_test(self, mock_ssrf, mock_reddit_extract):
        mock_ssrf.return_value = True
        raw_dict = {
            "extract": {
                "domain_overrides": {
                    "reddit.com": {
                        "headers": {"User-Agent": "CustomRedditBot/1.0"},
                        "cookies": {"reddit_session": "secret_token_123"},
                    }
                }
            }
        }
        config = ForageConfig.from_dict(raw_dict, source_path="test")
        mock_reddit_extract.return_value = {
            "title": "Reddit Post",
            "content": "# Reddit Post\n\nContent here",
            "raw_content": "# Reddit Post\n\nContent here",
            "method": "reddit+json",
        }

        res = await extract_url(config, None, "https://www.reddit.com/r/stocks/comments/123/test/")
        self.assertIn("Content here", res["content"])

        mock_reddit_extract.assert_called_once()
        _, kwargs = mock_reddit_extract.call_args
        self.assertEqual(kwargs["extra_headers"], {"User-Agent": "CustomRedditBot/1.0"})
        self.assertEqual(kwargs["cookies"], {"reddit_session": "secret_token_123"})

    @patch("app.browser.BrowserPool.render", new_callable=AsyncMock)
    @patch("app.extract._is_ssrf_safe", new_callable=AsyncMock)
    async def _async_browser_test(self, mock_ssrf, mock_browser_render):
        mock_ssrf.return_value = True
        raw_dict = {
            "extract": {
                "domain_overrides": {
                    "app.internal.io": {
                        "force_render": True,
                        "headers": {"X-Custom-Auth": "auth_val"},
                        "cookies": {"session_id": "sess_999"},
                    }
                }
            }
        }
        config = ForageConfig.from_dict(raw_dict, source_path="test")
        mock_pool = MagicMock()
        mock_pool.render = AsyncMock(return_value="<html><body><h1>Dashboard</h1></body></html>")

        res = await extract_url(config, mock_pool, "https://app.internal.io/dashboard")
        self.assertIn("Dashboard", res["content"])

        mock_pool.render.assert_called_once()
        _, kwargs = mock_pool.render.call_args
        self.assertEqual(kwargs["extra_headers"], {"X-Custom-Auth": "auth_val"})
        self.assertEqual(kwargs["cookies"], {"session_id": "sess_999"})

    def test_domain_override_headers_cookies(self):
        import asyncio
        asyncio.run(self._async_test())
        asyncio.run(self._async_reddit_test())
        asyncio.run(self._async_browser_test())


if __name__ == "__main__":
    unittest.main()
