"""OpenAI共通クライアントのオフラインテスト。"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import llm_client


class _FakeResponses:
    def __init__(self, output_text: str):
        self.output_text = output_text
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(status="completed", output_text=self.output_text)


class _FakeWebResponse:
    status = "completed"

    def __init__(self, output_text: str):
        self.output_text = output_text

    def model_dump(self):
        return {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {"url": "https://example.com/official", "title": "Official"}
                        ]
                    },
                }
            ]
        }


class _FakeWebResponses:
    def __init__(self, output_text: str):
        self.response = _FakeWebResponse(output_text)
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class LlmClientTests(unittest.TestCase):
    def test_text_uses_low_cost_model_and_no_reasoning(self):
        responses = _FakeResponses("  記事本文  ")
        fake_client = SimpleNamespace(responses=responses)

        with patch.object(llm_client, "_get_client", return_value=fake_client):
            actual = llm_client.generate_text("作成して", max_output_tokens=321)

        self.assertEqual(actual, "記事本文")
        self.assertEqual(responses.kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(responses.kwargs["reasoning"], {"effort": "none"})
        self.assertEqual(responses.kwargs["max_output_tokens"], 321)

    def test_json_uses_strict_structured_output(self):
        payload = {"title": "テスト"}
        responses = _FakeResponses(json.dumps(payload, ensure_ascii=False))
        fake_client = SimpleNamespace(responses=responses)
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        }

        with patch.object(llm_client, "_get_client", return_value=fake_client):
            actual = llm_client.generate_json(
                "JSONで作成して", schema_name="article", schema=schema
            )

        self.assertEqual(actual, payload)
        output_format = responses.kwargs["text"]["format"]
        self.assertEqual(output_format["type"], "json_schema")
        self.assertEqual(output_format["name"], "article")
        self.assertTrue(output_format["strict"])
        self.assertEqual(output_format["schema"], schema)

    def test_incomplete_response_is_an_error(self):
        response = SimpleNamespace(
            status="incomplete",
            incomplete_details={"reason": "max_output_tokens"},
            output_text="途中",
        )
        with self.assertRaisesRegex(RuntimeError, "途中で終了"):
            llm_client._extract_text(response)

    def test_web_json_requires_search_and_returns_tool_sources(self):
        responses = _FakeWebResponses('{"title":"速報"}')
        fake_client = SimpleNamespace(responses=responses)
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        }
        with patch.object(llm_client, "_get_client", return_value=fake_client):
            payload, sources = llm_client.generate_web_json(
                "検索して", schema_name="live", schema=schema
            )
        self.assertEqual({"title": "速報"}, payload)
        self.assertEqual("https://example.com/official", sources[0]["url"])
        self.assertEqual("required", responses.kwargs["tool_choice"])
        self.assertEqual("web_search", responses.kwargs["tools"][0]["type"])


if __name__ == "__main__":
    unittest.main()
