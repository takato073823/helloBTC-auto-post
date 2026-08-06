"""Grok X Searchクライアントのオフラインテスト。"""

from __future__ import annotations

import datetime as dt
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import grok_client


class _FakeResponse:
    status = "completed"

    def __init__(
        self,
        payload: dict,
        *,
        citations: list[str] | None = None,
        searched: bool = True,
        output: list[object] | None = None,
    ):
        self.output_text = json.dumps(payload, ensure_ascii=False)
        self.citations = citations or []
        self._searched = searched
        self._output = output

    def model_dump(self):
        output = self._output
        if output is None:
            output = [{"type": "x_search_call"}] if self._searched else [{"type": "message"}]
        return {
            "output": output,
            "citations": self.citations,
            "usage": {"cost_in_usd_ticks": 125_000_000},
        }


class _FakeResponses:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class GrokClientTests(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"signals": {"type": "array", "items": {"type": "object"}}},
            "required": ["signals"],
        }

    def test_only_cited_x_posts_survive(self):
        payload = {
            "signals": [
                {"post_url": "https://x.com/official/status/123", "headline": "cited"},
                {"post_url": "https://x.com/fake/status/999", "headline": "uncited"},
            ]
        }
        response = _FakeResponse(payload, citations=["https://x.com/i/status/123"])
        responses = _FakeResponses(response)
        client = SimpleNamespace(responses=responses)
        with patch.object(grok_client, "_get_client", return_value=client):
            actual, sources = grok_client.generate_x_json(
                "Xを検索",
                schema_name="signals",
                schema=self.schema,
                from_date=dt.date(2026, 8, 4),
                to_date=dt.date(2026, 8, 5),
            )
        self.assertEqual(["cited"], [row["headline"] for row in actual["signals"]])
        self.assertEqual("https://x.com/i/status/123", sources[0]["url"])
        self.assertEqual("x_search", responses.kwargs["tools"][0]["type"])
        self.assertEqual("2026-08-06", responses.kwargs["tools"][0]["to_date"])
        self.assertEqual("required", responses.kwargs["tool_choice"])
        self.assertEqual(["no_inline_citations"], responses.kwargs["include"])
        self.assertTrue(responses.kwargs["text"]["format"]["strict"])

    def test_x_search_result_field_is_verified_without_inline_citations(self):
        payload = {
            "signals": [
                {"post_url": "https://x.com/official/status/123", "headline": "result"},
                {"post_url": "https://x.com/fake/status/999", "headline": "spoofed"},
            ]
        }
        response = _FakeResponse(
            payload,
            output=[
                {
                    "type": "x_search_call",
                    "results": [{"url": "https://x.com/i/status/123"}],
                    "arguments": "https://x.com/fake/status/999",
                    "query": "from:fake https://x.com/fake/status/999",
                }
            ],
        )
        responses = _FakeResponses(response)
        client = SimpleNamespace(responses=responses)
        with patch.object(grok_client, "_get_client", return_value=client):
            actual, sources = grok_client.generate_x_json(
                "Xを検索",
                schema_name="signals",
                schema=self.schema,
                from_date=dt.date(2026, 8, 4),
                to_date=dt.date(2026, 8, 5),
            )
        self.assertEqual(["result"], [row["headline"] for row in actual["signals"]])
        self.assertEqual(["https://x.com/i/status/123"], [row["url"] for row in sources])

    def test_x_search_action_sources_are_verified_and_non_x_urls_are_ignored(self):
        response = _FakeResponse(
            {"signals": [{"post_url": "https://x.com/official/status/456", "headline": "action"}]},
            output=[
                {
                    "type": "x_search_call",
                    "action": {
                        "sources": [
                            {"url": "https://example.com/not-x"},
                            {"link": "https://x.com/i/status/456"},
                        ]
                    },
                }
            ],
        )
        client = SimpleNamespace(responses=_FakeResponses(response))
        with patch.object(grok_client, "_get_client", return_value=client):
            actual, sources = grok_client.generate_x_json(
                "Xを検索",
                schema_name="signals",
                schema=self.schema,
                from_date=dt.date(2026, 8, 4),
                to_date=dt.date(2026, 8, 5),
            )
        self.assertEqual(["action"], [row["headline"] for row in actual["signals"]])
        self.assertEqual(["https://x.com/i/status/456"], [row["url"] for row in sources])

    def test_malformed_or_model_authored_tool_fields_never_verify_posts(self):
        response = _FakeResponse(
            {"signals": [{"post_url": "https://x.com/fake/status/999", "headline": "spoofed"}]},
            output=[
                "unexpected",
                {
                    "type": "x_search_call",
                    "arguments": "https://x.com/fake/status/999",
                    "query": "https://x.com/fake/status/999",
                    "results": [None],
                },
                {"type": "message", "content": "unexpected"},
            ],
        )
        client = SimpleNamespace(responses=_FakeResponses(response))
        with patch.object(grok_client, "_get_client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "投稿引用が返りませんでした"):
                grok_client.generate_x_json(
                    "Xを検索",
                    schema_name="signals",
                    schema=self.schema,
                    from_date=dt.date(2026, 8, 4),
                    to_date=dt.date(2026, 8, 5),
                )

    def test_x_search_execution_is_required(self):
        response = _FakeResponse(
            {"signals": []},
            citations=[],
            searched=False,
        )
        client = SimpleNamespace(responses=_FakeResponses(response))
        with patch.object(grok_client, "_get_client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "X Search"):
                grok_client.generate_x_json(
                    "Xを検索",
                    schema_name="signals",
                    schema=self.schema,
                    from_date=dt.date(2026, 8, 4),
                    to_date=dt.date(2026, 8, 5),
                )

    def test_x_citation_is_accepted_when_tool_trace_is_not_expanded(self):
        response = _FakeResponse(
            {"signals": [{"post_url": "https://x.com/official/status/123"}]},
            citations=["https://x.com/i/status/123"],
            searched=False,
        )
        client = SimpleNamespace(responses=_FakeResponses(response))
        with patch.object(grok_client, "_get_client", return_value=client):
            payload, _ = grok_client.generate_x_json(
                "Xを検索",
                schema_name="signals",
                schema=self.schema,
                from_date=dt.date(2026, 8, 4),
                to_date=dt.date(2026, 8, 5),
            )
        self.assertEqual(1, len(payload["signals"]))

    def test_editorial_generation_never_calls_x_search(self):
        response = _FakeResponse({"candidates": [{"hook": "⚡️ 事実を編集"}]})
        responses = _FakeResponses(response)
        client = SimpleNamespace(responses=responses)
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"candidates": {"type": "array", "items": {"type": "object"}}},
            "required": ["candidates"],
        }
        with patch.object(grok_client, "_get_client", return_value=client):
            payload = grok_client.generate_editorial_json(
                "検証済み事実だけを編集",
                schema_name="editorial",
                schema=schema,
            )
        self.assertEqual("⚡️ 事実を編集", payload["candidates"][0]["hook"])
        self.assertNotIn("tools", responses.kwargs)
        self.assertNotIn("tool_choice", responses.kwargs)
        self.assertTrue(responses.kwargs["text"]["format"]["strict"])


if __name__ == "__main__":
    unittest.main()
