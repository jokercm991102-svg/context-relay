from dataclasses import FrozenInstanceError, asdict
from unittest import TestCase

from context_relay.models import (
    Assessment,
    ConfirmationEvent,
    DialogueEvent,
    DocumentEvidence,
    GitSnapshot,
    ObjectiveCandidate,
    SemanticEvidence,
    SessionMetrics,
    SessionReadResult,
)


class ModelTests(TestCase):
    def test_git_evidence_contract_has_content_and_topology_fingerprints(self):
        document = DocumentEvidence(
            "PROJECT_STATUS.md",
            True,
            size_bytes=12,
            modified_ns=34,
        )
        snapshot = GitSnapshot("/project", "/project", "HEAD", "a" * 40)

        self.assertIn("content_hash", asdict(document))
        self.assertIn("worktree_porcelain", asdict(snapshot))

    def test_semantic_contract_is_frozen_and_serializable(self):
        event = DialogueEvent(
            "user", "建立下版功能", "a" * 64, 7, "objective"
        )
        confirmation = ConfirmationEvent(
            "b" * 64,
            "approval",
            "V2 規格",
            "c" * 64,
            "approved",
            None,
            False,
            ("Explicit target",),
        )
        objective = ObjectiveCandidate(
            "建立下版功能",
            "user_prompt",
            event.source_hash,
            "inferred",
            "high",
            False,
            (),
            "approved",
            ("Latest actionable user objective",),
            (),
        )
        semantic = SemanticEvidence(
            objective,
            confirmation,
            (),
            1,
            ("README.md",),
            (),
        )
        assessment = Assessment("low", (), {}, semantic)
        result = SessionReadResult(SessionMetrics(path_hash="d" * 64), (event,))

        self.assertEqual(
            asdict(assessment)["semantic"]["objective"]["text"],
            "建立下版功能",
        )
        self.assertEqual(result.dialogue_events, (event,))
        with self.assertRaises(FrozenInstanceError):
            setattr(objective, "text", "changed")
