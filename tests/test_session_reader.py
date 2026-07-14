from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from context_relay.session_reader import read_session, read_session_input
from tests.helpers import write_jsonl


class SessionReaderTests(TestCase):
    def test_streams_codex_metrics_without_retaining_text(self):
        with TemporaryDirectory() as raw:
            path = Path(raw) / "session.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_started", "turn_id": "1"},
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "改成另一個目標",
                            "images": ["data:image/png;base64,AAA"],
                            "local_images": ["/tmp/a.png"],
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {"type": "context_compacted"},
                    },
                    {
                        "type": "event_msg",
                        "payload": {"type": "turn_aborted", "turn_id": "1"},
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "custom_tool_call_output",
                            "output": [
                                {
                                    "type": "image",
                                    "image_url": "data:image/png;base64,BBBB",
                                },
                                {"type": "text", "text": "safe"},
                            ],
                        },
                    },
                ],
            )

            metrics = read_session(path)

            self.assertEqual(metrics.turns_started, 1)
            self.assertEqual(metrics.compactions, 1)
            self.assertEqual(metrics.aborted_turns, 1)
            self.assertEqual(metrics.embedded_images, 2)
            self.assertEqual(
                metrics.embedded_image_bytes,
                len("data:image/png;base64,AAA".encode("utf-8"))
                + len("data:image/png;base64,BBBB".encode("utf-8")),
            )
            self.assertEqual(metrics.local_images, 1)
            self.assertEqual(metrics.goal_shift_signals, 0)
            self.assertFalse(metrics.text_analysis_enabled)

            with_text = read_session(path, include_text=True)
            self.assertEqual(with_text.goal_shift_signals, 1)
            self.assertNotIn("改成另一個目標", repr(with_text))

    def test_invalid_lines_are_counted_and_missing_file_is_partial(self):
        with TemporaryDirectory() as raw:
            path = Path(raw) / "broken.jsonl"
            path.write_text(
                '{"type":"event_msg","payload":{"type":"task_started"}}\n'
                "not-json\n",
                encoding="utf-8",
            )

            metrics = read_session(path)

            self.assertEqual(metrics.invalid_lines, 1)
            self.assertEqual(metrics.lines, 2)
            missing = read_session(Path(raw) / "missing.jsonl")
            self.assertTrue(missing.errors)
            self.assertNotIn(str(Path(raw)), repr(missing))

    def test_unknown_event_is_counted_and_high_corruption_is_visible(self):
        with TemporaryDirectory() as raw:
            path = Path(raw) / "future.jsonl"
            path.write_text(
                '{"type":"event_msg","payload":{"type":"future_event"}}\n'
                "broken-one\n"
                "broken-two\n",
                encoding="utf-8",
            )

            metrics = read_session(path)

            self.assertEqual(metrics.event_counts["event_msg:future_event"], 1)
            self.assertEqual(metrics.invalid_lines, 2)
            self.assertEqual(metrics.lines, 3)

    def test_include_text_keeps_last_forty_events_and_truncates_each(self):
        with TemporaryDirectory() as raw:
            path = Path(raw) / "dialogue.jsonl"
            records = [
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": f"建立項目 {index} " + ("x" * 5000),
                    },
                }
                for index in range(45)
            ]
            records.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "請核准 V2 規格"}
                        ],
                    },
                }
            )
            write_jsonl(path, records)
            result = read_session_input(path, include_text=True)
            self.assertEqual(len(result.dialogue_events), 40)
            self.assertEqual(
                result.dialogue_events[-1].event_kind,
                "confirmation_prompt",
            )
            self.assertTrue(
                all(
                    len(item.normalized_text) <= 4000
                    for item in result.dialogue_events
                )
            )
            self.assertEqual(result.metrics.user_messages, 45)
            self.assertIn("Dialogue events were truncated", result.limitations)

    def test_metadata_mode_never_retains_dialogue(self):
        with TemporaryDirectory() as raw:
            path = write_jsonl(
                Path(raw) / "private.jsonl",
                [
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "private objective",
                        },
                    }
                ],
            )
            result = read_session_input(path, include_text=False)
            self.assertEqual(result.dialogue_events, ())
            self.assertNotIn("private objective", repr(result))

    def test_unlink_and_atomic_replacement_during_read_fail_closed(self):
        for action in ("unlink", "replace"):
            with self.subTest(action=action), TemporaryDirectory() as raw:
                root = Path(raw)
                path = write_jsonl(
                    root / "session.jsonl",
                    [
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "user_message",
                                "message": "ORIGINAL PRIVATE OBJECTIVE",
                            },
                        }
                    ],
                )
                replacement = write_jsonl(
                    root / "replacement.jsonl",
                    [
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "task_started",
                                "padding": "replacement is a different size",
                            },
                        }
                    ],
                )
                original_open = Path.open
                resolved_path = path.resolve()
                changed = False

                def open_then_change(candidate, *args, **kwargs):
                    nonlocal changed
                    handle = original_open(candidate, *args, **kwargs)
                    if not changed and candidate == resolved_path:
                        changed = True
                        if action == "unlink":
                            path.unlink()
                        else:
                            replacement.replace(path)
                    return handle

                with patch("pathlib.Path.open", open_then_change):
                    result = read_session_input(path, include_text=True)

                self.assertTrue(changed)
                self.assertEqual(
                    result.metrics.errors,
                    ("Session unreadable",),
                )
                self.assertEqual(result.dialogue_events, ())
                self.assertEqual(result.limitations, ("Session unreadable",))
                self.assertNotIn("ORIGINAL PRIVATE OBJECTIVE", repr(result))
