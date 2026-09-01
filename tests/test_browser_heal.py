"""Tests for the dead-browser heal path (crash-variant hardening).

Covers the classifier (_is_dead_browser must recognize "Target crashed"-style
messages) and the scrapling retry loop (restart the session once, then retry).
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.browser import BrowserPool, _is_dead_browser


class _Cfg:
    """Minimal browser config namespace (real dataclass shape not needed)."""

    engine = "scrapling"
    cdp_url = ""
    min_idle = 1
    max_instances = 1
    idle_timeout = 60.0
    launch_timeout = 30.0
    network_idle_timeout = 5.0
    scroll_steps = 0
    challenge_timeout = 5.0
    solve_cloudflare = False
    fallback_solver = False
    headless = True
    stealth = False


class TestIsDeadBrowser(unittest.TestCase):
    def test_recognizes_crash_variants(self):
        for msg in [
            "Page.set_extra_http_headers: Target crashed",
            "Target page, context or browser has been closed",
            "Browser has been closed",
            "browser process crashed unexpectedly",
        ]:
            self.assertTrue(_is_dead_browser(RuntimeError(msg)), msg)

    def test_ignores_harmless_messages(self):
        self.assertFalse(_is_dead_browser(RuntimeError("some unrelated error")))
        self.assertFalse(_is_dead_browser(RuntimeError("GONE: timeout")) )


class TestScraplingHeal(unittest.TestCase):
    def test_full_reset_after_crash(self):
        """A 'Target crashed' error triggers a session restart, then the
        same URL is fetched from the fresh session successfully."""
        session = MagicMock()
        crash = RuntimeError("Page.set_extra_http_headers: Target crashed")
        session.fetch = AsyncMock(
            side_effect=[crash, MagicMock(body=b"<html>ok</html>")]
        )
        restart = AsyncMock()

        async def _run():
            # Build the pool and semaphore inside a running event loop so the
            # test works on Python 3.9: asyncio.Semaphore (and Lock) call
            # get_event_loop() at construction time on 3.9, which raises
            # RuntimeError outside a running loop.
            pool = BrowserPool(_Cfg())
            pool._scrapling_session = session
            pool._semaphore = asyncio.Semaphore(1)
            pool._started = True
            pool._restart_scrapling_session = restart  # shadow the real restart
            return await pool.render(
                "https://example.com",
                wait_for=None,
                timeout=10,
                network_idle_timeout=5,
                challenge_timeout=5,
                readability=False,
                extra_headers=None,
                cookies=None,
            )

        html = asyncio.run(_run())

        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(restart.await_count, 1)

    def test_non_dead_error_raises_immediately(self):
        """A real (non browser-death) error must NOT be swallowed: it is
        re-raised without a restart."""
        session = MagicMock()
        session.fetch = AsyncMock(
            side_effect=RuntimeError("unhandled bug that should propagate")
        )
        restart = AsyncMock()

        async def _run():
            # Build the pool and semaphore inside a running event loop so the
            # test works on Python 3.9 (see the sibling test for the rationale).
            pool = BrowserPool(_Cfg())
            pool._scrapling_session = session
            pool._semaphore = asyncio.Semaphore(1)
            pool._started = True
            pool._restart_scrapling_session = restart
            return await pool.render(
                "https://example.com",
                wait_for=None,
                timeout=10,
                network_idle_timeout=5,
                challenge_timeout=5,
                readability=False,
                extra_headers=None,
                cookies=None,
            )

        with self.assertRaises(RuntimeError):
            asyncio.run(_run())
        self.assertEqual(restart.await_count, 0)


if __name__ == "__main__":
    unittest.main()