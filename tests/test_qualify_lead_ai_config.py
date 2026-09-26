"""Unit tests for qualify_lead AI Config wiring. No live LaunchDarkly or Anthropic calls."""

import unittest
from unittest.mock import MagicMock, patch

from ldai.tracker import LDAIConfigTracker
from ldclient import Context

from serenia.skills.qualify_lead import normalize_qualification, qualify_lead


def _context():
    return Context.builder("customer-dana").kind("user").build()


class _Model:
    def __init__(self, name="claude-opus-4-6", parameters=None):
        self.name = name
        self._parameters = parameters or {}

    def get_parameter(self, key):
        return self._parameters.get(key)


class _Message:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class _Provider:
    def __init__(self, name="Anthropic"):
        self.name = name


class _Config:
    def __init__(self, tracker, **overrides):
        self.enabled = overrides.get("enabled", True)
        self.model = overrides.get("model", _Model())
        self.provider = overrides.get("provider", _Provider())
        self.messages = overrides.get(
            "messages",
            [_Message("system", "Score the lead. Use lead_temperature and follow_up_action.")],
        )
        self._tracker = tracker

    def create_tracker(self):
        return self._tracker


class _Usage:
    input_tokens = 12
    output_tokens = 8


class _Block:
    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text):
        self.content = [_Block(text)]
        self.usage = _Usage()


class QualifyLeadAiConfigTests(unittest.TestCase):
    def test_normalize_v1_and_v2_shapes(self):
        v1 = normalize_qualification(
            {"lead_score": "Hot", "follow_up_action": "book_call"}
        )
        self.assertEqual(v1, {"score": "hot", "action": "book_call", "reason": ""})

        v2 = normalize_qualification(
            {
                "lead_temperature": "Warm",
                "urgency": "weeks",
                "budget_signal": "explicit",
                "decision_authority": "decision_maker",
                "follow_up_action": "send_nurture",
            }
        )
        self.assertEqual(v2["score"], "warm")
        self.assertEqual(v2["action"], "send_nurture")
        self.assertIn("urgency: weeks", v2["reason"])
        self.assertIn("budget signal: explicit", v2["reason"])

    def test_successful_completion_emits_generation_success(self):
        ld_client = MagicMock()
        tracker = LDAIConfigTracker(
            ld_client=ld_client,
            run_id="run-1",
            config_key="qualify-lead-config",
            variation_key="qualify-lead-v2-precise",
            version=3,
            context=_context(),
            model_name="claude-opus-4-6",
            provider_name="Anthropic",
        )
        config = _Config(tracker)
        ai_client = MagicMock()
        ai_client.completion_config.return_value = config

        anthropic_client = MagicMock()
        anthropic_client.messages.create.return_value = _Response(
            '{"lead_temperature": "Hot", "urgency": "immediate", '
            '"follow_up_action": "book_call"}'
        )
        anthropic_ctor = MagicMock(return_value=anthropic_client)

        with (
            patch("serenia.skills.qualify_lead.get_ai_client", return_value=ai_client),
            patch("serenia.skills.qualify_lead.anthropic.Anthropic", anthropic_ctor),
            patch("serenia.skills.qualify_lead.get_table", return_value=None),
        ):
            result = qualify_lead(
                "Dana Rivera",
                "dana@riveraphotography.com",
                "Wedding reception for 120 guests on September 20.",
                context_key="customer-dana",
            )

        ai_client.completion_config.assert_called_once()
        config_key = ai_client.completion_config.call_args.args[0]
        self.assertEqual(config_key, "qualify-lead-config")
        evaluated_context = ai_client.completion_config.call_args.args[1]
        self.assertEqual(evaluated_context.key, "customer-dana")

        create_kwargs = anthropic_client.messages.create.call_args.kwargs
        self.assertEqual(create_kwargs["model"], "claude-opus-4-6")
        self.assertIn("lead_temperature", create_kwargs["system"])
        self.assertIn("Dana Rivera", create_kwargs["messages"][-1]["content"])

        success_calls = [
            call for call in ld_client.track.call_args_list
            if call.args[0] == "$ld:ai:generation:success"
        ]
        self.assertEqual(len(success_calls), 1)
        self.assertEqual(success_calls[0].args[3], 1)
        self.assertEqual(success_calls[0].args[2]["variationKey"], "qualify-lead-v2-precise")
        self.assertEqual(success_calls[0].args[2]["configKey"], "qualify-lead-config")

        self.assertEqual(result["score"], "hot")
        self.assertEqual(result["action"], "book_call")
        self.assertIn("urgency: immediate", result["reason"])

    def test_provider_exception_emits_generation_error(self):
        ld_client = MagicMock()
        tracker = LDAIConfigTracker(
            ld_client=ld_client,
            run_id="run-err",
            config_key="qualify-lead-config",
            variation_key="qualify-lead-v1-stable",
            version=3,
            context=_context(),
            model_name="claude-sonnet-4-6",
            provider_name="Anthropic",
        )
        config = _Config(
            tracker,
            model=_Model(name="claude-sonnet-4-6"),
            messages=[_Message("system", "Use lead_score.")],
        )
        ai_client = MagicMock()
        ai_client.completion_config.return_value = config
        anthropic_client = MagicMock()
        anthropic_client.messages.create.side_effect = RuntimeError("anthropic down")

        with (
            patch("serenia.skills.qualify_lead.get_ai_client", return_value=ai_client),
            patch(
                "serenia.skills.qualify_lead.anthropic.Anthropic",
                return_value=anthropic_client,
            ),
            patch("serenia.skills.qualify_lead.get_table", return_value=None),
        ):
            with self.assertRaises(RuntimeError):
                qualify_lead("Dana", "dana@example.com", "120 guests in June")

        error_calls = [
            call for call in ld_client.track.call_args_list
            if call.args[0] == "$ld:ai:generation:error"
        ]
        success_calls = [
            call for call in ld_client.track.call_args_list
            if call.args[0] == "$ld:ai:generation:success"
        ]
        self.assertEqual(len(error_calls), 1)
        self.assertEqual(success_calls, [])

    def test_disabled_config_skips_generation(self):
        config = _Config(tracker=None, enabled=False, model=None, provider=None, messages=None)
        ai_client = MagicMock()
        ai_client.completion_config.return_value = config

        with (
            patch("serenia.skills.qualify_lead.get_ai_client", return_value=ai_client),
            patch("serenia.skills.qualify_lead.anthropic.Anthropic") as anthropic_ctor,
            patch("serenia.skills.qualify_lead.get_table", return_value=None),
        ):
            result = qualify_lead("Dana", "dana@example.com", "just browsing")

        anthropic_ctor.assert_not_called()
        self.assertEqual(result["score"], "warm")
        self.assertEqual(result["action"], "send_nurture")


if __name__ == "__main__":
    unittest.main()
