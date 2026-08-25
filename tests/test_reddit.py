"""Unit test for native Reddit JSON extraction."""

import json
import unittest

from app.documents import parse_reddit_json


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


if __name__ == "__main__":
    unittest.main()
