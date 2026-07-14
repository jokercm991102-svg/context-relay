from unittest import TestCase

from context_relay.analysis import analyze
from context_relay.models import (
    GitSnapshot,
    ObjectiveCandidate,
    SemanticEvidence,
    SessionMetrics,
)


class AnalysisTests(TestCase):
    def test_large_compacted_image_session_is_critical_with_evidence(self):
        git = GitSnapshot("$PROJECT", "$PROJECT", "main", "a" * 40)
        session = SessionMetrics(
            path_hash="abc",
            file_size_bytes=148 * 1024 * 1024,
            user_messages=96,
            turns_started=96,
            compactions=11,
            embedded_images=81,
            aborted_turns=14,
        )

        assessment = analyze(git, session)

        context = next(
            finding
            for finding in assessment.findings
            if finding.dimension == "context_pressure"
        )
        self.assertEqual(context.level, "critical")
        self.assertTrue(any("148" in item for item in context.evidence))
        self.assertTrue(context.limitations)
        self.assertGreaterEqual(
            assessment.etas["clean_handoff"].maximum_seconds, 180
        )

    def test_small_clean_input_does_not_raise_high_risk(self):
        git = GitSnapshot("$PROJECT", "$PROJECT", "main", "b" * 40)
        session = SessionMetrics(
            path_hash="abc", file_size_bytes=1024, turns_started=3
        )

        assessment = analyze(git, session)

        self.assertIn(assessment.overall_level, {"low", "moderate"})
        self.assertFalse(
            any(
                finding.level in {"high", "critical"}
                for finding in assessment.findings
            )
        )

    def test_goal_confidence_is_unavailable_without_text_analysis(self):
        git = GitSnapshot("$PROJECT", "$PROJECT", "main", "c" * 40)
        session = SessionMetrics(path_hash="abc", text_analysis_enabled=False)

        assessment = analyze(git, session)

        goal = next(
            finding
            for finding in assessment.findings
            if finding.dimension == "goal_drift"
        )
        self.assertEqual(goal.confidence, "unavailable")

    def test_corrupt_session_reduces_context_confidence(self):
        git = GitSnapshot("$PROJECT", "$PROJECT", "main", "c" * 40)
        session = SessionMetrics(
            path_hash="abc", lines=10, invalid_lines=6
        )

        assessment = analyze(git, session)

        context = next(
            finding
            for finding in assessment.findings
            if finding.dimension == "context_pressure"
        )
        self.assertEqual(context.confidence, "low")

    def test_semantic_evidence_is_passed_through_without_changing_v1_risk(self):
        git = GitSnapshot("$PROJECT", "$PROJECT", "main", "d" * 40)
        session = SessionMetrics(
            path_hash="abc",
            file_size_bytes=55 * 1024 * 1024,
            turns_started=41,
            compactions=4,
            goal_shift_signals=6,
            text_analysis_enabled=True,
        )
        objective = ObjectiveCandidate(
            "Ship semantic V2",
            "user_prompt",
            "e" * 64,
            "inferred",
            "high",
            False,
        )
        semantic = SemanticEvidence(objective, None, (), 1, (), ())

        baseline = analyze(git, session)
        assessment = analyze(git, session, semantic)

        self.assertIs(assessment.semantic, semantic)
        self.assertEqual(assessment.overall_level, baseline.overall_level)
        self.assertEqual(assessment.findings, baseline.findings)
        self.assertEqual(assessment.etas, baseline.etas)
