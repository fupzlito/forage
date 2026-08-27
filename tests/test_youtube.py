# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for native YouTube Data API, handle resolution, SearXNG fallback, and MCP tools."""

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
        self.assertEqual(kwargs["params"]["key"], "TEST_API_KEY")

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

    @patch("app.youtube.search_searxng")
    def test_search_youtube_searxng_fallback(self, mock_search_searxng):
        # Configure without direct api_key to verify SearXNG fallback
        mock_search_searxng.return_value = {
            "success": True,
            "results": [
                {
                    "title": "About Oliver | Doki Doki Literature Club! #1",
                    "url": "https://www.youtube.com/watch?v=wwRVLZ7LntA",
                    "channel": "About Oliver (UCC-0KKfcSG4BGpMeyUXhu0Q)",
                    "snippet": "Blind playthrough of DDLC.",
                    "citation": "[About Oliver | Doki Doki Literature Club! #1](https://www.youtube.com/watch?v=wwRVLZ7LntA)",
                }
            ],
        }

        res = search_youtube(
            config=self.config,
            channel="UCC-0KKfcSG4BGpMeyUXhu0Q",
            query="minecraft",
            sort_by="date",
            limit=15,
        )

        self.assertTrue(res["success"])
        mock_search_searxng.assert_called_once()
        _, kwargs = mock_search_searxng.call_args
        self.assertEqual(kwargs["engines"], ["youtube-api"])
        self.assertIn("UCC-0KKfcSG4BGpMeyUXhu0Q", kwargs["query"])
        self.assertIn("minecraft", kwargs["query"])
        self.assertIn("sort:date", kwargs["query"])

    async def test_mcp_youtube_search_tool_registered_and_executed(self):
        # 1. Check tool definition
        defs = get_tool_definitions(self.config)
        yt_tool = next((t for t in defs if t["name"] == "youtube_search"), None)
        self.assertIsNotNone(yt_tool)
        self.assertIn("channel", yt_tool["inputSchema"]["properties"])
        self.assertIn("sort_by", yt_tool["inputSchema"]["properties"])

        # 2. Check execution via mock
        with patch("app.youtube.search_searxng") as mock_searxng:
            mock_searxng.return_value = {
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
                config=self.config,
                browser_pool=None,
            )

            self.assertIn("results", res)
            self.assertEqual(len(res["results"]), 1)
            self.assertIn("formatted_text", res)
            self.assertIn("YOUTUBE SEARCH RESULTS", res["formatted_text"])
            self.assertIn("CHANNEL: About Oliver (UCC-0KKfcSG4BGpMeyUXhu0Q)", res["formatted_text"])

    def test_youtube_search_rest_endpoint(self):
        client = TestClient(app)
        with patch("app.youtube.search_searxng") as mock_searxng:
            mock_searxng.return_value = {
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
