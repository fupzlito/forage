"""Unit test for streaming extraction (SSE) on POST /extract."""

import json
import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app


class TestStreamExtract(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.mcp.extract_url", new_callable=AsyncMock)
    def test_stream_extract_sse(self, mock_extract):
        mock_extract.side_effect = [
            {"url": "https://example.com/1", "title": "Page 1", "content": "Content 1", "method": "static"},
            {"url": "https://example.com/2", "title": "Page 2", "content": "Content 2", "method": "static"},
        ]

        resp = self.client.post(
            "/extract",
            json={"urls": ["https://example.com/1", "https://example.com/2"], "stream": True},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers["content-type"])

        lines = [line.strip() for line in resp.text.split("\n") if line.strip().startswith("data:")]
        self.assertEqual(len(lines), 3)  # 2 results + [DONE]
        self.assertEqual(lines[-1], "data: [DONE]")

        item1 = json.loads(lines[0].replace("data:", "").strip())
        self.assertEqual(item1["title"], "Page 1")


if __name__ == "__main__":
    unittest.main()
