"""Unit test for native Reddit JSON extraction."""

import json
import unittest

from app.reddit import parse_reddit_json


class TestRedditJsonEdgeCases(unittest.TestCase):
    """Boundary cases for parse_reddit_json: the < 2 element list, the
    comments-must-be-a-dict contract, and the empty-children listing."""

    def test_single_element_list_omits_comments(self):
        # A list with only the post (no comments block) -> post-only output,
        # no crash, no Link line (url is a www.reddit.com link).
        raw = [{
            "kind": "t3",
            "data": {
                "children": [{
                    "kind": "t3",
                    "data": {
                        "title": "Solo Post",
                        "subreddit_name_prefixed": "r/solo",
                        "author": "alice",
                        "score": 42,
                        "created_utc": 1723048291.0,
                        "selftext": "body",
                        "url": "https://www.reddit.com/r/solo/comments/abc/solo/",
                    },
                }],
            },
        }]
        content, title = parse_reddit_json(raw)
        self.assertEqual(title, "Solo Post")
        self.assertIn("# Solo Post", content)
        self.assertIn("body", content)
        self.assertNotIn("## Comments", content)
        self.assertNotIn("**Link**:", content)

    def test_empty_list_raises(self):
        with self.assertRaises(ValueError):
            parse_reddit_json([])

    def test_non_dict_second_element_drops_comments(self):
        # raw_json[1] must be a dict to parse comments. If it is anything else
        # (e.g. a list), comments are dropped silently, not crash.
        raw = [{
            "kind": "t3",
            "data": {
                "children": [{
                    "kind": "t3",
                    "data": {
                        "title": "Thread",
                        "subreddit_name_prefixed": "r/thread",
                        "author": "bob",
                        "score": 1,
                        "created_utc": 1723048291.0,
                        "url": "https://www.reddit.com/r/thread/comments/def/thread/",
                    },
                }],
            },
        }, [1, 2, 3]]  # comments block is a list, not a dict
        content, title = parse_reddit_json(raw)
        self.assertEqual(title, "Thread")
        self.assertIn("# Thread", content)
        self.assertNotIn("## Comments", content)

    def test_dict_second_without_data(self):
        # comments dict with no data.children -> no comments, no crash.
        raw = [{
            "kind": "t3",
            "data": {
                "children": [{
                    "kind": "t3",
                    "data": {
                        "title": "No Comments Thread",
                        "subreddit_name_prefixed": "r/x",
                        "author": "carol",
                        "score": 1,
                        "created_utc": 1723048291.0,
                        "url": "https://www.reddit.com/r/x/comments/ghi/thread/",
                    },
                }],
            },
        }, {"data": {"children": []}}]
        content, title = parse_reddit_json(raw)
        self.assertEqual(title, "No Comments Thread")
        self.assertNotIn("## Comments", content)

    def test_empty_children_listing_no_post_cards(self):
        # An empty listing (no children) cannot determine a subreddit, so the
        # title falls back to the generic "Reddit Posts" and the body says
        # "No posts found." (graceful, no crash).
        listing = {
            "kind": "Listing",
            "data": {"children": []},
        }
        content, title = parse_reddit_json(listing)
        self.assertEqual(title, "Reddit Posts")
        self.assertIn("# Reddit Posts", content)
        self.assertIn("No posts found.", content)
        self.assertNotIn("### [", content)  # no post cards


class TestRedditJSON(unittest.TestCase):
    def test_parse_reddit_json(self):
        sample_data = [
            {
                "kind": "Listing",
                "data": {
                    "children": [
                        {
                            "kind": "t3",
                            "data": {
                                "title": "Test Reddit Post",
                                "subreddit_name_prefixed": "r/testsub",
                                "author": "test_author",
                                "score": 99,
                                "num_comments": 2,
                                "created_utc": 1723048291.0,
                                "selftext": "This is a test post body content.",
                                "url": "https://www.reddit.com/r/testsub/comments/123/test/",
                            },
                        }
                    ]
                },
            },
            {
                "kind": "Listing",
                "data": {
                    "children": [
                        {
                            "kind": "t1",
                            "data": {
                                "author": "commenter_one",
                                "body": "Great post!",
                                "score": 15,
                                "created_utc": 1723049500.0,
                                "replies": {
                                    "kind": "Listing",
                                    "data": {
                                        "children": [
                                            {
                                                "kind": "t1",
                                                "data": {
                                                    "author": "reply_user",
                                                    "body": "I agree completely.",
                                                    "score": 5,
                                                    "created_utc": 1723050000.0,
                                                },
                                            }
                                        ]
                                    },
                                },
                            },
                        }
                    ]
                },
            },
        ]

        markdown, title = parse_reddit_json(sample_data)
        self.assertEqual(title, "Test Reddit Post")
        self.assertIn("# Test Reddit Post", markdown)
        self.assertIn("r/testsub", markdown)
        self.assertIn("u/test_author", markdown)
        self.assertIn("**Posted**: 2024-08-07", markdown)
        self.assertIn("This is a test post body content.", markdown)
        self.assertIn("u/commenter_one", markdown)
        self.assertIn("Score: 15", markdown)
        self.assertIn("2024-08-07", markdown)
        self.assertIn("Great post!", markdown)
        self.assertIn("u/reply_user", markdown)

    def test_parse_subreddit_listing_json(self):
        sample_listing = {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "title": "SpaceX Starship Launch Update",
                            "subreddit_name_prefixed": "r/SpaceX",
                            "author": "elon_fan",
                            "score": 1500,
                            "num_comments": 350,
                            "created_utc": 1723048291.0,
                            "selftext": "Flight 5 summary and booster catch discussion.",
                            "permalink": "/r/SpaceX/comments/abc/starship/",
                        },
                    },
                    {
                        "kind": "t3",
                        "data": {
                            "title": "Falcon 9 Record Turnaround",
                            "subreddit_name_prefixed": "r/SpaceX",
                            "author": "rocket_nerd",
                            "score": 820,
                            "num_comments": 45,
                            "created_utc": 1723050000.0,
                            "selftext": "New turnaround record achieved.",
                            "permalink": "/r/SpaceX/comments/def/falcon9/",
                        },
                    },
                ]
            },
        }

        markdown, title = parse_reddit_json(sample_listing)
        self.assertEqual(title, "r/SpaceX - Reddit Posts")
        self.assertIn("# r/SpaceX - Reddit Posts", markdown)
        self.assertIn("### [SpaceX Starship Launch Update](https://www.reddit.com/r/SpaceX/comments/abc/starship/)", markdown)
        self.assertIn("u/elon_fan", markdown)
        self.assertIn("**Posted**: 2024-08-07", markdown)
        self.assertIn("**Score**: 1500", markdown)
        self.assertIn("### [Falcon 9 Record Turnaround](https://www.reddit.com/r/SpaceX/comments/def/falcon9/)", markdown)
        self.assertIn("u/rocket_nerd", markdown)

    def test_parse_reddit_search_multi_subreddit_listing(self):
        multi_sub_listing = {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "title": "Post in Stocks",
                            "subreddit_name_prefixed": "r/stocks",
                            "author": "trader1",
                            "score": 500,
                            "num_comments": 40,
                            "created_utc": 1723048291.0,
                            "permalink": "/r/stocks/comments/111/stocks_post/",
                        },
                    },
                    {
                        "kind": "t3",
                        "data": {
                            "title": "Post in WSB",
                            "subreddit_name_prefixed": "r/wallstreetbets",
                            "author": "trader2",
                            "score": 1200,
                            "num_comments": 150,
                            "created_utc": 1723050000.0,
                            "permalink": "/r/wallstreetbets/comments/222/wsb_post/",
                        },
                    },
                ]
            },
        }

        markdown, title = parse_reddit_json(multi_sub_listing)
        self.assertEqual(title, "Reddit Search / Listing Results")
        self.assertIn("# Reddit Search / Listing Results", markdown)
        self.assertIn("### [Post in Stocks](https://www.reddit.com/r/stocks/comments/111/stocks_post/)", markdown)
        self.assertIn("**Subreddit**: r/stocks", markdown)
        self.assertIn("### [Post in WSB](https://www.reddit.com/r/wallstreetbets/comments/222/wsb_post/)", markdown)
        self.assertIn("**Subreddit**: r/wallstreetbets", markdown)

    def test_clean_reddit_markdown(self):
        from app.extract import _clean_reddit_markdown
        raw = (
            "# r/Baking\n\n"
            "Skip to main content LocalLlama Open menu Advertise on Reddit Open chat Create post "
            "Open sort options\n\n"
            "Change post view\n\n"
            "Card Compact Community highlights 15 votes • 27 comments\n\n"
            "[Cake](https://reddit.com/r/Baking/comments/123/cake/)\n\n"
            "[![](https://styles.redditmedia.com/t5_2qx1h/styles/communityIcon_123.png)\n"
            "r/Baking](https://www.reddit.com/r/Baking/)\n\n"
            "[Cake](https://reddit.com/r/Baking/comments/123/cake/)\n"
        )
        cleaned = _clean_reddit_markdown(raw)
        self.assertNotIn("Skip to main content", cleaned)
        self.assertNotIn("Open menu", cleaned)
        self.assertNotIn("Advertise on Reddit", cleaned)
        self.assertNotIn("Open sort options", cleaned)
        self.assertNotIn("Change post view", cleaned)
        self.assertNotIn("Community highlights", cleaned)
        self.assertNotIn("communityIcon_", cleaned)
        self.assertIn("[Cake](https://reddit.com/r/Baking/comments/123/cake/)", cleaned)

    def test_reddit_cooldown_and_throttle(self):
        import asyncio
        import app.extract
        from app.extract import _throttle_reddit_request, _is_reddit_json_on_cooldown, _set_reddit_json_cooldown

        async def _run():
            app.extract._reddit_json_cooldown_until = 0.0
            self.assertFalse(_is_reddit_json_on_cooldown())
            await _throttle_reddit_request(min_interval=0.01)
            _set_reddit_json_cooldown(5.0)
            self.assertTrue(_is_reddit_json_on_cooldown())
            app.extract._reddit_json_cooldown_until = 0.0

        asyncio.run(_run())

    def test_reddit_cookies_env_overrides(self):
        import os
        from unittest.mock import patch
        from app.config import load_config

        with patch.dict(os.environ, {
            "FORAGE_REDDIT_SESSION": '"session_abc_123"',
            "FORAGE_REDDIT_TOKEN_V2": "'token_xyz_456'",
        }):
            cfg = load_config()
            overrides = cfg.extract.domain_overrides
            reddit_ov = next((o for o in overrides if "reddit" in o.pattern), None)
            self.assertIsNotNone(reddit_ov)
            self.assertEqual(reddit_ov.cookies.get("reddit_session"), "session_abc_123")
            self.assertEqual(reddit_ov.cookies.get("token_v2"), "token_xyz_456")

        # Test combined raw cookies string with quotes
        with patch.dict(os.environ, {
            "FORAGE_REDDIT_COOKIES": '"reddit_session=sess1; token_v2=tok2; custom_cookie=val3"',
        }):
            cfg_raw = load_config()
            overrides_raw = cfg_raw.extract.domain_overrides
            reddit_ov_raw = next((o for o in overrides_raw if "reddit" in o.pattern), None)
            self.assertIsNotNone(reddit_ov_raw)
            self.assertEqual(reddit_ov_raw.cookies.get("reddit_session"), "sess1")
            self.assertEqual(reddit_ov_raw.cookies.get("token_v2"), "tok2")
            self.assertEqual(reddit_ov_raw.cookies.get("custom_cookie"), "val3")

    def test_reddit_private_and_not_found_handling(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.extract import _try_reddit_extract
        from app.config import load_config

        cfg = load_config()

        async def _test():
            # 1. Private subreddit JSON response
            mock_client = AsyncMock()
            mock_resp_priv = MagicMock()
            mock_resp_priv.status_code = 403
            mock_resp_priv.text = '{"reason": "private", "message": "Forbidden", "error": 403}'
            mock_resp_priv.json.return_value = {"reason": "private", "message": "Forbidden", "error": 403}

            with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp_priv)):
                res = await _try_reddit_extract(cfg, "https://www.reddit.com/r/locallama/top/?t=week", timeout=10)
                self.assertIsNotNone(res)
                self.assertEqual(res["method"], "reddit+forbidden")
                self.assertIn("Private Community", res["title"])
                self.assertIn("is a private community", res["content"])

            # 3. Deleted/Removed post handling
            deleted_post_json = [
                {
                    "kind": "Listing",
                    "data": {
                        "children": [
                            {
                                "kind": "t3",
                                "data": {
                                    "title": "Deleted Test Post",
                                    "subreddit_name_prefixed": "r/test",
                                    "author": "[deleted]",
                                    "selftext": "[deleted]",
                                    "score": 5,
                                    "num_comments": 1,
                                    "created_utc": 1723048291.0,
                                },
                            }
                        ]
                    },
                },
                {"kind": "Listing", "data": {"children": []}},
            ]
            content_del, title_del = parse_reddit_json(deleted_post_json)
            self.assertIn("This post was deleted by the author", content_del)

            removed_post_json = [
                {
                    "kind": "Listing",
                    "data": {
                        "children": [
                            {
                                "kind": "t3",
                                "data": {
                                    "title": "Removed Test Post",
                                    "subreddit_name_prefixed": "r/test",
                                    "author": "spammer",
                                    "selftext": "[removed]",
                                    "removed_by_category": "moderator",
                                    "score": 0,
                                    "num_comments": 0,
                                    "created_utc": 1723048291.0,
                                },
                            }
                        ]
                    },
                },
                {"kind": "Listing", "data": {"children": []}},
            ]
            content_rem, title_rem = parse_reddit_json(removed_post_json)
            self.assertIn("This post was removed by Reddit moderators", content_rem)
            self.assertIn("moderator", content_rem)

            # 4. Non-existent post thread (empty children array in post listing)
            empty_post_json = [
                {"kind": "Listing", "data": {"children": []}},
                {"kind": "Listing", "data": {"children": []}},
            ]
            content_empty_p, title_empty_p = parse_reddit_json(empty_post_json)
            self.assertIn("Post Not Found", title_empty_p)
            self.assertIn("deleted or does not exist", content_empty_p)

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
