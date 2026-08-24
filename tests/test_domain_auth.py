"""Unit test for domain overrides with custom headers and cookies."""

import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from app.config import ForageConfig
from app.extract import extract_url


class TestDomainAuth(unittest.TestCase):
    @patch("app.extract.fetch_static", new_callable=AsyncMock)
    async def _async_test(self, mock_fetch):
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

    def test_domain_override_headers_cookies(self):
        import asyncio
        asyncio.run(self._async_test())


if __name__ == "__main__":
    unittest.main()
