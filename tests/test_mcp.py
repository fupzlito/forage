"""Unit tests for MCP protocol and OpenAI API tools endpoints."""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


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
            "results": [{"position": 1, "title": "Search Title", "url": "https://example.com", "snippet": "desc", "citation": "[Search Title](https://example.com)"}],
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
        self.assertEqual(search_op["operationId"], "web_search")


if __name__ == "__main__":
    unittest.main()
