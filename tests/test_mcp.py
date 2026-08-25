"""Unit tests for MCP protocol and OpenAI API tools endpoints."""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.config import ForageConfig
from app.main import app, config


class TestMCPAndOpenAIEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_mcp_initialize(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        }
        response = self.client.post("/mcp", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], 1)
        self.assertIn("serverInfo", data["result"])
        self.assertEqual(data["result"]["serverInfo"]["name"], "forage")

    def test_mcp_tools_list(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        response = self.client.post("/mcp", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        tools = data["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("web_search", tool_names)
        self.assertIn("web_extract", tool_names)

    @patch("app.mcp.search_searxng")
    def test_mcp_tools_call_search(self, mock_search):
        mock_search.return_value = {
            "success": True,
            "results": [{"position": 1, "domain": "example.com", "title": "Search Title", "url": "https://example.com", "snippet": "desc", "citation": "[Search Title](https://example.com)"}],
            "warning": None,
        }
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "web_search",
                "arguments": {"query": "python fastapi"},
            },
        }
        response = self.client.post("/mcp", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["result"]["isError"])
        text = data["result"]["content"][0]["text"]
        self.assertIn("Search Title", text)
        mock_search.assert_called_once()
        _, kwargs = mock_search.call_args
        self.assertEqual(kwargs.get("limit"), config.search.default_limit)

    def test_v1_tools_list(self):
        response = self.client.get("/v1/tools")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("tools", data)
        names = [t["function"]["name"] for t in data["tools"]]
        self.assertIn("web_search", names)
        self.assertIn("web_extract", names)

    def test_openapi_schema(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        search_op = data["paths"]["/search"]["post"]
        extract_op = data["paths"]["/extract"]["post"]
        self.assertEqual(search_op["operationId"], "web_search")
        self.assertEqual(extract_op["operationId"], "web_extract")
        self.assertIn("Search the web via SearXNG", search_op["description"])
        self.assertIn("CITATION RULES", search_op["description"])
        self.assertIn("Fetch and extract clean markdown", extract_op["description"])
        self.assertIn("CITATION RULES", extract_op["description"])

    @patch("app.mcp.extract_url", new_callable=AsyncMock)
    def test_mcp_tools_call_extract_require_max_chars(self, mock_extract):
        mock_extract.return_value = {
            "position": 1,
            "domain": "example.com",
            "url": "https://example.com",
            "title": "Example",
            "content": "A" * 5000,
            "citation": "[Example](https://example.com)",
            "method": "static",
            "extracted_at": "2026-08-25 03:00:00 UTC",
        }

        # 1. require_max_chars is True, but max_chars omitted -> error
        cfg_req = ForageConfig.from_dict({"extract": {"require_max_chars": True}}, source_path="test")
        with patch("app.mcp._get_config_and_pool", return_value=(cfg_req, None)):
            payload = {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "web_extract", "arguments": {"urls": ["https://example.com"]}},
            }
            resp = self.client.post("/mcp", json=payload)
            data = resp.json()
            self.assertTrue(data["result"]["isError"])
            self.assertIn("Missing required parameter 'max_chars'", data["result"]["content"][0]["text"])

        # 2. require_max_chars is True, max_chars supplied -> succeeds and truncates
        with patch("app.mcp._get_config_and_pool", return_value=(cfg_req, None)):
            payload = {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "web_extract", "arguments": {"urls": ["https://example.com"], "max_chars": 1000}},
            }
            resp = self.client.post("/mcp", json=payload)
            data = resp.json()
            self.assertFalse(data["result"]["isError"])
            text = data["result"]["content"][0]["text"]
            self.assertIn("[TRUNCATED at 1,000 of 5,000 chars]", text)

    @patch("app.mcp.extract_url", new_callable=AsyncMock)
    def test_v1_chat_completions_streaming(self, mock_extract):
        mock_extract.return_value = {
            "position": 1,
            "domain": "example.com",
            "url": "https://example.com",
            "title": "Example",
            "content": "Example page content here.",
            "citation": "[Example](https://example.com)",
            "method": "static",
        }

        # 1. Non-streaming request
        payload = {
            "model": "web_extract",
            "messages": [{"role": "user", "content": "https://example.com"}],
            "stream": False,
        }
        resp = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["object"], "chat.completion")
        self.assertIn("Example page content here.", data["choices"][0]["message"]["content"])

        # 2. Streaming request
        payload["stream"] = True
        resp = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers["content-type"])
        body = resp.text
        self.assertIn("chat.completion.chunk", body)
        self.assertIn("Example page content here.", body)
        self.assertIn("data: [DONE]", body)


if __name__ == "__main__":
    unittest.main()
