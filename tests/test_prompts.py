"""Unit tests for modular prompt configuration and dynamic template rendering."""

import dataclasses
import unittest
from app.config import ForageConfig, load_config
from app.mcp import get_tool_definitions
from app.prompts import render_prompt


class TestPrompts(unittest.TestCase):
    def test_render_prompt_basic(self):
        template = "Hello {name}, year is {year}!"
        context = {"name": "World", "year": 2026}
        self.assertEqual(render_prompt(template, context), "Hello World, year is 2026!")

    def test_render_prompt_missing_and_extra_braces(self):
        template = 'Keep {missing} and JSON {"key": "value"}'
        context = {"name": "Test"}
        self.assertEqual(render_prompt(template, context), 'Keep {missing} and JSON {"key": "value"}')

    def test_default_tool_definitions_rendered(self):
        base = load_config()
        config = dataclasses.replace(base, youtube=dataclasses.replace(base.youtube, api_key="TEST_KEY"))
        tools = get_tool_definitions(config)
        self.assertEqual(len(tools), 3)
        search_tool = next(t for t in tools if t["name"] == "web_search")
        extract_tool = next(t for t in tools if t["name"] == "web_extract")
        youtube_tool = next(t for t in tools if t["name"] == "youtube_search")

        self.assertIn("Search the web via SearXNG", search_tool["description"])
        self.assertIn("year 2026", search_tool["description"])
        self.assertIn("CITATION RULES", search_tool["description"])
        self.assertIn("Search YouTube videos", youtube_tool["description"])

    def test_empty_prompts_config_renders_full_defaults(self):
        # Empty prompts dictionary should still render rich default descriptions with citation rules
        base = ForageConfig.from_dict({}, source_path="test")
        config = dataclasses.replace(base, youtube=dataclasses.replace(base.youtube, api_key="TEST_KEY"))
        tools = get_tool_definitions(config)
        self.assertEqual(len(tools), 3)
        search_tool = next(t for t in tools if t["name"] == "web_search")
        extract_tool = next(t for t in tools if t["name"] == "web_extract")
        youtube_tool = next(t for t in tools if t["name"] == "youtube_search")

        self.assertIn("Search the web via SearXNG", search_tool["description"])
        self.assertIn("CITATION RULES", search_tool["description"])
        self.assertIn("Fetch and extract clean markdown", extract_tool["description"])
        self.assertIn("CITATION RULES", extract_tool["description"])

    def test_custom_prompt_override(self):
        raw_dict = {
            "prompts": {
                "citation_guidelines": "Custom rules for testing.",
                "search_tool_description": "Search custom: {citation_guidelines}",
                "youtube_tool_description": "YouTube custom: {citation_guidelines}",
                "youtube_params": {
                    "query": "Custom youtube query description",
                },
            },
            "youtube": {"api_key": "TEST_KEY"},
        }
        config = ForageConfig.from_dict(raw_dict, source_path="test")
        tools = get_tool_definitions(config)
        search_tool = next(t for t in tools if t["name"] == "web_search")
        youtube_tool = next(t for t in tools if t["name"] == "youtube_search")
        self.assertEqual(search_tool["description"], "Search custom: Custom rules for testing.")
        self.assertEqual(youtube_tool["description"], "YouTube custom: Custom rules for testing.")
        self.assertEqual(
            youtube_tool["inputSchema"]["properties"]["query"]["description"],
            "Custom youtube query description",
        )

    def test_extract_max_chars_schema_and_require_toggle(self):
        # 1. Default config: max_chars optional, maximum bound to extract.max_content_chars
        config_default = load_config()
        tools_def = get_tool_definitions(config_default)
        extract_def = next(t for t in tools_def if t["name"] == "web_extract")
        schema = extract_def["inputSchema"]

        self.assertEqual(schema["required"], ["urls"])
        self.assertEqual(schema["properties"]["max_chars"]["maximum"], config_default.extract.max_content_chars)

        # 2. Config with require_max_chars=True and custom max_content_chars
        config_req = ForageConfig.from_dict(
            {"extract": {"require_max_chars": True, "max_content_chars": 50000}},
            source_path="test",
        )
        tools_req = get_tool_definitions(config_req)
        extract_req = next(t for t in tools_req if t["name"] == "web_extract")
        schema_req = extract_req["inputSchema"]

        self.assertEqual(schema_req["required"], ["urls", "max_chars"])
        self.assertEqual(schema_req["properties"]["max_chars"]["maximum"], 50000)

    def test_dynamic_tool_names_and_env_overrides(self):
        import os
        from unittest.mock import patch

        # Test custom tool names in config dict
        cfg = ForageConfig.from_dict(
            {"tools": {"search_name": "custom_search", "extract_name": "custom_scrape"}},
            source_path="test",
        )
        tools = get_tool_definitions(cfg)
        search_tool = next(t for t in tools if t["name"] == "custom_search")
        extract_tool = next(t for t in tools if t["name"] == "custom_scrape")

        self.assertIn("use the custom_scrape tool directly", search_tool["description"])
        self.assertEqual(extract_tool["name"], "custom_scrape")

        # Test environment variable overrides for tool names
        with patch.dict(os.environ, {"FORAGE_SEARCH_NAME": "env_search", "FORAGE_EXTRACT_NAME": "env_fetch"}):
            cfg_env = load_config()
            self.assertEqual(cfg_env.tools.search_name, "env_search")
            self.assertEqual(cfg_env.tools.extract_name, "env_fetch")

            tools_env = get_tool_definitions(cfg_env)
            search_env_tool = next(t for t in tools_env if t["name"] == "env_search")
            self.assertIn("use the env_fetch tool directly", search_env_tool["description"])


if __name__ == "__main__":
    unittest.main()
