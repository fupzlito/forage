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
        self.assertIn("This is a test post body content.", markdown)
        self.assertIn("u/commenter_one", markdown)
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
        self.assertIn("**Score**: 1500", markdown)
        self.assertIn("### [Falcon 9 Record Turnaround](https://www.reddit.com/r/SpaceX/comments/def/falcon9/)", markdown)
        self.assertIn("u/rocket_nerd", markdown)


if __name__ == "__main__":
    unittest.main()
