from unittest import TestCase

from context_relay.dialogue import make_dialogue_event, normalize_message


class DialogueTests(TestCase):
    def test_annotation_keeps_only_current_request(self):
        raw = (
            "<response-annotations>older text</response-annotations>\n"
            "## My request for Codex:\n"
            "建立下版功能並核准實測，最好能測出優化的差距"
        )
        self.assertEqual(
            normalize_message(raw),
            "建立下版功能並核准實測，最好能測出優化的差距",
        )

    def test_event_kinds_do_not_turn_confirmation_into_goal(self):
        cases = (
            ("建立下版功能並實測", "objective"),
            ("為什麼要忽略好的與核准？", "clarification"),
            ("核准 V2 規格」", "confirmation"),
            ("好的", "acknowledgement"),
            ("核准並開始實測", "confirmation"),
            ("核准後開始實測", "confirmation"),
            ("第二種", "reference"),
            ("繼續", "control"),
            ("繼續進行實測", "control"),
            ("目前 66 還在執行", "general"),
            (
                "現在66有在進行一個任務，等任務完成就核准",
                "general",
            ),
            ("改成建立 V3", "replacement"),
        )
        for sequence, (text, expected) in enumerate(cases):
            with self.subTest(text=text):
                event = make_dialogue_event("user", text, sequence)
                self.assertEqual(event.event_kind, expected)

    def test_environment_only_event_is_discarded(self):
        event = make_dialogue_event(
            "user",
            "<environment_context><cwd>/private/work</cwd></environment_context>",
            1,
        )
        self.assertIsNone(event)
