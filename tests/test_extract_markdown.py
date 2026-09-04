"""Unit test for the native ``text/markdown`` extraction branch.

A server that answers ``Accept: text/markdown`` returns the body directly as
markdown (no HTML). Previously that branch returned an envelope missing
``title`` and ``citation`` (a runtime KeyError for consumers). This test
installs a fake ``trafilatura`` module in ``sys.modules`` so the extract module
imports cleanly without the dependency installed.
"""

import asyncio
import sys
import unittest
from unittest.mock import Mock, patch
from types import ModuleType

from app.extract import extract_url, _markdown_title


def _install_fake_trafilatura():
    """Register a minimal fake ``trafilatura`` in sys.modules."""
    fake = ModuleType("trafilatura")
    fake.extract = lambda **kwargs: ""  # noqa: E731
    sys.modules["trafilatura"] = fake


def _install_fake_markdownify():
    """Register a minimal fake ``markdownify`` in sys.modules."""
    fake = ModuleType("markdownify")
    fake.markdownify = lambda **kwargs: ""  # noqa: E731
    sys.modules["markdownify"] = fake


def _fake_config():
    """A stub config with only the attributes the native-markdown path reads."""
    cfg = Mock()
    cfg.extract = Mock()
    cfg.extract.domain_overrides = ()
    cfg.extract.max_content_chars = 100000
    cfg.extract.raw_content_markdown = False
    cfg.extract.reddit_mirror = None
    cfg.browser = Mock()
    cfg.browser.fallback_solver = False
    return cfg


async def _no_doc(*_a, **_k):
    """Async fake for _extract_document (returns None -> fall through)."""
    return None


async def _no_reddit(*_a, **_k):
    """Async fake for _try_reddit_extract (returns None -> fall through)."""
    return None


async def _fetch_markdown(config, url, *a, **k):
    """Async fake for fetch_static returning native markdown (the x-url)."""
    return "# My Article\n\nHello world, this is the body text.", 200, url, "text/markdown"


async def _fetch_markdown_2(config, url, *a, **k):
    """Async fake for fetch_static returning native markdown (the slug url)."""
    return "just some prose with no heading", 200, url, "text/markdown"


class TestNativeMarkdown(unittest.TestCase):
    def setUp(self):
        # Fake the heavy HTML-extraction dep for the whole test class.
        _install_fake_trafilatura()
        _install_fake_markdownify()
        with patch("app.searxng.extract_domain", lambda u: "example.com"), \
             patch("app.extract._try_reddit_extract", new=_no_reddit):
            with patch("app.extract._extract_document", new=_no_doc):
                with patch(
                    "app.extract.fetch_static",
                    new=_fetch_markdown,
                ):
                    with patch("app.extract.BrowserPool", object), \
                         patch("app.extract.looks_like_challenge", new=lambda *_a, **_k: False):
                        self.result = asyncio.run(
                            extract_url(_fake_config(), None, "https://example.com/x", position=3)
                        )

    def test_envelope_has_all_keys(self):
        for key in ("position", "domain", "url", "title", "content", "raw_content",
                    "method", "citation", "extracted_at"):
            self.assertIn(key, self.result, f"missing envelope key: {key}")

    def test_title_derived_from_heading(self):
        self.assertEqual(self.result["title"], "My Article")

    def test_citation_present(self):
        self.assertTrue(self.result["citation"].startswith("[Source: My Article]"))

    def test_method_and_domain(self):
        self.assertEqual(self.result["method"], "markdown")
        self.assertEqual(self.result["domain"], "example.com")
        self.assertEqual(self.result["position"], 3)

    def test_result_matches_normal_envelope_shape(self):
        # The native-markdown envelope must be a superset of the normal result
        # keys so consumers can read it identically.
        normal = {
            "position", "domain", "url", "title", "content",
            "citation", "method", "extracted_at",
        }
        self.assertTrue(normal.issubset(self.result.keys()))

    def test_title_falls_back_to_slug(self):
        # No heading in the body -> fall back to the URL slug (title-cased).
        cfg = _fake_config()
        with patch("app.searxng.extract_domain", lambda u: "example.com"), \
             patch("app.extract._try_reddit_extract", new=_no_reddit), \
             patch("app.extract._extract_document", new=_no_doc):
            with patch("app.extract.fetch_static", new=_fetch_markdown_2):
                res = asyncio.run(extract_url(cfg, None, "https://example.com/cool-post", position=1))
        self.assertEqual(res["title"], "Cool Post")


class TestMarkdownTitle(unittest.TestCase):
    def test_first_heading(self):
        self.assertEqual(_markdown_title("# Hello World\n\nbody", "https://x.com/a"), "Hello World")

    def test_slug_fallback(self):
        # Slug fallback uses .title(), which title-cases each word.
        self.assertEqual(_markdown_title("no heading here", "https://x.com/2024/a-cool-post"), "A Cool Post")

    def test_host_fallback(self):
        # Host fallback strips a leading "www." and .capitalize(): first up, rest down.
        self.assertEqual(_markdown_title("nothing", "https://www.example.org/"), "Example.org")

    def test_host_fallback_no_www(self):
        self.assertEqual(_markdown_title("nothing", "https://x.com/"), "X.com")


if __name__ == "__main__":
    unittest.main()
