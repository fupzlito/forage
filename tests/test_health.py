"""Unit test for GET /health with SearXNG liveness probe."""

import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app


class TestHealthCheck(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("httpx.AsyncClient.get", new_callable=AsyncMock)
    def test_health_searxng_connected(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("searxng", data)
        self.assertEqual(data["searxng"]["status"], "connected")
        self.assertIn("latency_ms", data["searxng"])

    @patch("httpx.AsyncClient.get", new_callable=AsyncMock)
    def test_health_searxng_unreachable(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")

        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["searxng"]["status"], "unreachable")


if __name__ == "__main__":
    unittest.main()
