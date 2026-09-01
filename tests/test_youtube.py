# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for native YouTube Data API, handle resolution, and MCP tools."""

import dataclasses
import unittest
from unittest.mock import MagicMock, patch

import httpx
from starlette.testclient import TestClient

from app.config import ForageConfig, YouTubeConfig, load_config
from app.main import app
from app.mcp import execute_tool_call, get_tool_definitions
from app.youtube import (
    extract_channel_id_or_handle,
    resolve_handle_to_channel_id,
    search_youtube,
    search_youtube_direct,
)


class TestYouTube(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = load_config()

    def test_extract_channel_id_or_handle(self):
        # Raw Channel ID
        cid, handle = extract_channel_id_or_handle("UCC-0KKfcSG4BGpMeyUXhu0Q")
        self.assertEqual(cid, "UCC-0KKfcSG4BGpMeyUXhu0Q")
        self.assertIsNone(handle)

        # Channel URL
        cid, handle = extract_channel_id_or_handle("https://www.youtube.com/channel/UCC-0KKfcSG4BGpMeyUXhu0Q")
        self.assertEqual(cid, "UCC-0KKfcSG4BGpMeyUXhu0Q")
        self.assertIsNone(handle)

        # Raw Handle
        cid, handle = extract_channel_id_or_handle("@aboutoliver")
        self.assertIsNone(cid)
        self.assertEqual(handle, "aboutoliver")

        # Handle URL
        cid, handle = extract_channel_id_or_handle("https://www.youtube.com/@aboutoliver")
        self.assertIsNone(cid)
        self.assertEqual(handle, "aboutoliver")

        # Empty
        cid, handle = extract_channel_id_or_handle("")
        self.assertIsNone(cid)
        self.assertIsNone(handle)

        # Channel URL with trailing /videos
        cid, handle = extract_channel_id_or_handle("https://youtube.com/channel/UCC-0KKfcSG4BGpMeyUXhu0Q/videos")
        self.assertEqual(cid, "UCC-0KKfcSG4BGpMeyUXhu0Q")
        self.assertIsNone(handle)

        # Channel URL with trailing slash
        cid, handle = extract_channel_id_or_handle("https://youtube.com/channel/UCC-0KKfcSG4BGpMeyUXhu0Q/")
        self.assertEqual(cid, "UCC-0KKfcSG4BGpMeyUXhu0Q")
        self.assertIsNone(handle)

        # Channel URL with query params
        cid, handle = extract_channel_id_or_handle("https://youtube.com/channel/UCC-0KKfcSG4BGpMeyUXhu0Q?sub_confirmation=1")
        self.assertEqual(cid, "UCC-0KKfcSG4BGpMeyUXhu0Q")
        self.assertIsNone(handle)

    def test_resolve_handle_to_channel_id(self):
        mock_client = MagicMock(spec=httpx.Client)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [{"id": "UCC-0KKfcSG4BGpMeyUXhu0Q"}]
        }
        mock_client.get.return_value = mock_resp

        cid = resolve_handle_to_channel_id("aboutoliver", api_key="TEST_API_KEY", client=mock_client)
        self.assertEqual(cid, "UCC-0KKfcSG4BGpMeyUXhu0Q")
        mock_client.get.assert_called_once()
        args, kwargs = mock_client.get.call_args
        self.assertEqual(kwargs["params"]["forHandle"], "aboutoliver")
        # Bug 1 regression: the API key must travel in a header, never in the
        # query params (where it would leak into httpx exception strings).
        self.assertNotIn("key", kwargs["params"])
        self.assertEqual(kwargs["headers"]["X-Goog-Api-Key"], "TEST_API_KEY")

    def test_search_youtube_direct_success(self):
        mock_client = MagicMock(spec=httpx.Client)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "items": [
                {
                    "id": {"videoId": "wwRVLZ7LntA"},
                    "snippet": {
                        "title": "Doki Doki Literature Club! #1",
                        "description": "Blind playthrough of DDLC.",
                        "channelTitle": "About Oliver",
                        "channelId": "UCC-0KKfcSG4BGpMeyUXhu0Q",
                        "publishedAt": "2026-08-19T18:12:21Z",
                        "liveBroadcastContent": "live",
                        "thumbnails": {
                            "high": {"url": "https://i.ytimg.com/vi/wwRVLZ7LntA/hqdefault.jpg"}
                        },
                    },
                }
            ]
        }
        mock_client.get.return_value = mock_resp

        res = search_youtube_direct(
            api_key="TEST_KEY",
            channel="UCC-0KKfcSG4BGpMeyUXhu0Q",
            sort_by="date",
            limit=10,
            client=mock_client,
        )

        self.assertTrue(res["success"])
        self.assertEqual(len(res["results"]), 1)
        item = res["results"][0]
        self.assertEqual(item["title"], "About Oliver | Doki Doki Literature Club! #1")
        self.assertEqual(item["channel"], "About Oliver (UCC-0KKfcSG4BGpMeyUXhu0Q)")
        self.assertEqual(item["live_status"], "[🔴 LIVE]")
        self.assertEqual(item["published_date"], "2026-08-19 18:12:21 UTC")
        self.assertEqual(item["url"], "https://www.youtube.com/watch?v=wwRVLZ7LntA")
        self.assertEqual(item["iframe_src"], "https://www.youtube-nocookie.com/embed/wwRVLZ7LntA")
        self.assertEqual(item["thumbnail"], "https://i.ytimg.com/vi/wwRVLZ7LntA/hqdefault.jpg")
        self.assertEqual(item["citation"], "[About Oliver | Doki Doki Literature Club! #1](https://www.youtube.com/watch?v=wwRVLZ7LntA)")

    @patch("app.youtube.resolve_handle_to_channel_id", return_value=None)
    def test_search_youtube_direct_handle_resolution_failure(self, mock_resolve):
        # Bug 3 regression: if a @handle cannot be resolved to a channel ID,
        # the function must return a clear error rather than silently dropping
        # the channel constraint (which would fall back to a global search).
        mock_client = MagicMock(spec=httpx.Client)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"items": []}
        mock_client.get.return_value = mock_resp

        res = search_youtube_direct(
            api_key="TEST_KEY",
            channel="@aboutoliver",
            query="minecraft",
            client=mock_client,
        )

        self.assertFalse(res["success"])
        self.assertIn("Could not resolve channel", res["error"])
        self.assertIn("@aboutoliver", res["error"])
        self.assertEqual(res["results"], [])
        # The search endpoint must NOT be hit with a dropped channel constraint.
        mock_client.get.assert_not_called()

    def test_search_youtube_requires_api_key(self):
        # Without an API key, search_youtube returns a clear "key required" error
        # rather than falling back to SearXNG.
        res = search_youtube(
            config=self.config,
            channel="UCC-0KKfcSG4BGpMeyUXhu0Q",
            query="minecraft",
            sort_by="date",
            limit=15,
        )
        self.assertFalse(res["success"])
        self.assertIn("FORAGE_YOUTUBE_API_KEY", res["error"])
        self.assertEqual(res["results"], [])

    async def test_mcp_youtube_search_tool_registered_and_executed(self):
        # The tool is registered only when an API key is configured.
        config = dataclasses.replace(
            self.config,
            youtube=dataclasses.replace(self.config.youtube, api_key="TEST_KEY"),
        )

        # 1. Check tool definition
        defs = get_tool_definitions(config)
        yt_tool = next((t for t in defs if t["name"] == "youtube_search"), None)
        self.assertIsNotNone(yt_tool)
        self.assertIn("channel", yt_tool["inputSchema"]["properties"])
        self.assertIn("sort_by", yt_tool["inputSchema"]["properties"])

        # 2. Check execution via mock (direct YouTube Data API path)
        with patch("app.youtube.search_youtube_direct") as mock_direct:
            mock_direct.return_value = {
                "success": True,
                "searched_at": "2026-08-25 21:00:00 UTC",
                "results": [
                    {
                        "position": 1,
                        "domain": "youtube.com",
                        "url": "https://www.youtube.com/watch?v=wwRVLZ7LntA",
                        "title": "About Oliver | Doki Doki Literature Club! #1",
                        "channel": "About Oliver (UCC-0KKfcSG4BGpMeyUXhu0Q)",
                        "snippet": "Blind playthrough of DDLC.",
                        "citation": "[About Oliver | Doki Doki Literature Club! #1](https://www.youtube.com/watch?v=wwRVLZ7LntA)",
                    }
                ],
            }

            res = await execute_tool_call(
                name="youtube_search",
                arguments={"channel": "UCC-0KKfcSG4BGpMeyUXhu0Q", "sort_by": "date"},
                config=config,
                browser_pool=None,
            )

            self.assertIn("results", res)
            self.assertEqual(len(res["results"]), 1)
            self.assertIn("formatted_text", res)
            self.assertIn("YOUTUBE SEARCH RESULTS", res["formatted_text"])
            self.assertIn("CHANNEL: About Oliver (UCC-0KKfcSG4BGpMeyUXhu0Q)", res["formatted_text"])

    def test_youtube_search_tool_not_registered_without_key(self):
        # Without an API key, the youtube_search tool is not registered.
        defs = get_tool_definitions(self.config)
        yt_tool = next((t for t in defs if t["name"] == "youtube_search"), None)
        self.assertIsNone(yt_tool)

    def test_youtube_search_rest_endpoint(self):
        client = TestClient(app)
        config_with_key = dataclasses.replace(
            self.config,
            youtube=dataclasses.replace(self.config.youtube, api_key="TEST_KEY"),
        )
        with patch("app.main.config", config_with_key), \
             patch("app.youtube.search_youtube_direct") as mock_direct:
            mock_direct.return_value = {
                "success": True,
                "results": [
                    {
                        "position": 1,
                        "domain": "youtube.com",
                        "url": "https://www.youtube.com/watch?v=wwRVLZ7LntA",
                        "title": "About Oliver | Doki Doki Literature Club! #1",
                    }
                ],
            }

            resp = client.post(
                "/v1/youtube/search",
                json={"channel": "UCC-0KKfcSG4BGpMeyUXhu0Q", "limit": 10},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertEqual(len(data["results"]), 1)


if __name__ == "__main__":
    unittest.main()
