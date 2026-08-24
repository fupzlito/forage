"""Unit tests for modular prompt configuration and dynamic template rendering."""

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
        config = load_config()
        tools = get_tool_definitions(config)
        self.assertEqual(len(tools), 2)
        search_tool = next(t for t in tools if t["name"] == "web_search")
        extract_tool = next(t for t in tools if t["name"] == "web_extract")

        self.assertIn("Search the web via SearXNG", search_tool["description"])
        self.assertIn("year 2026", search_tool["description"])
        self.assertIn("CITATION RULES", search_tool["description"])
        self.assertIn("Fetch and extract clean markdown", extract_tool["description"])

    def test_custom_prompt_override(self):
        raw_dict = {
            "prompts": {
                "citation_guidelines": "Custom rules for testing.",
                "search_tool_description": "Search custom: {citation_guidelines}",
            }
        }
        config = ForageConfig.from_dict(raw_dict, source_path="test")
        tools = get_tool_definitions(config)
        search_tool = next(t for t in tools if t["name"] == "web_search")
        self.assertEqual(search_tool["description"], "Search custom: Custom rules for testing.")


if __name__ == "__main__":
    unittest.main()
