from dataclasses import asdict
from hashlib import sha256
from unittest import TestCase

from context_relay.dialogue import make_dialogue_event
from context_relay.models import DocumentSection
from context_relay.semantics import build_semantic_evidence


def event(text, sequence, role="user"):
    result = make_dialogue_event(role, text, sequence)
    assert result is not None
    return result


def section(
    document,
    text,
    *,
    heading="current objective",
    head_matches=True,
):
    return DocumentSection(
        document=document,
        heading=heading,
        text=text,
        source_hash=sha256(text.encode("utf-8")).hexdigest(),
        recorded_head="a" * 40 if head_matches is not None else None,
        head_matches=head_matches,
    )


class SemanticTests(TestCase):
    def test_real_sequence_preserves_goal_and_approves_named_spec(self):
        semantic = build_semantic_evidence(
            (
                event(
                    "建立下版功能並核准實測，最好能測出優化的差距",
                    1,
                ),
                event("為什麼要忽略好的與核准？", 2),
                event("核准 V2 規格」", 3),
            ),
            (),
            (),
        )

        self.assertEqual(
            semantic.objective.text,
            "建立下版功能並核准實測，最好能測出優化的差距",
        )
        self.assertEqual(semantic.confirmation.target_label, "V2 規格")
        self.assertEqual(
            semantic.confirmation.target_hash,
            sha256("V2 規格".encode("utf-8")).hexdigest(),
        )
        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(semantic.objective.confirmation_status, "approved")

    def test_acknowledgement_is_not_approval(self):
        semantic = build_semantic_evidence(
            (event("建立 V2", 1), event("好的", 2)),
            (),
            (),
        )

        self.assertEqual(semantic.objective.text, "建立 V2")
        self.assertEqual(semantic.confirmation.status, "acknowledged")
        self.assertEqual(
            semantic.objective.confirmation_status,
            "acknowledged",
        )

    def test_single_pending_prompt_allows_bare_approval(self):
        prompt = event("請核准 V2 規格", 2, "assistant")
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("核准", 3),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(
            semantic.confirmation.target_label,
            "pending confirmation",
        )
        self.assertEqual(semantic.confirmation.target_hash, prompt.source_hash)
        self.assertFalse(semantic.confirmation.requires_confirmation)

    def test_bare_approval_rejects_non_specific_pending_prompts(self):
        prompts = (
            "請核准",
            "請確認你想要哪個規格？",
            "請確認要 A 還是 B？",
        )
        for prompt_text in prompts:
            with self.subTest(prompt=prompt_text):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        event(prompt_text, 2, "assistant"),
                        event("核准", 3),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "ambiguous",
                )
                self.assertTrue(
                    semantic.confirmation.requires_confirmation
                )
                self.assertIsNone(semantic.confirmation.target_label)

    def test_bare_approval_without_pending_target_is_ambiguous(self):
        semantic = build_semantic_evidence(
            (event("建立 V2", 1), event("核准", 2)),
            (),
            (),
        )

        self.assertEqual(semantic.objective.text, "建立 V2")
        self.assertEqual(semantic.confirmation.status, "ambiguous")
        self.assertTrue(semantic.confirmation.requires_confirmation)

    def test_zero_pending_approval_ignores_trailing_punctuation(self):
        approvals = (
            "核准！",
            "核准。",
            "核准？」",
            "核准…",
            "核准⋯",
            "核准—",
            "核准～",
            "核准《》",
            "核准\u200b",
        )
        for approval in approvals:
            with self.subTest(approval=approval):
                semantic = build_semantic_evidence(
                    (event("建立 V2", 1), event(approval, 2)),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "ambiguous",
                )
                self.assertTrue(
                    semantic.confirmation.requires_confirmation
                )
                self.assertIsNone(semantic.confirmation.target_label)

    def test_confirmation_noun_prefixes_are_ignored(self):
        statements = (
            "確認碼是 123456",
            "確認信已寄出",
            "確認鍵壞了",
            "核准函編號 42",
            "核准書版本 3",
        )
        for statement_text in statements:
            with self.subTest(statement=statement_text):
                statement = event(statement_text, 2)
                self.assertEqual(statement.event_kind, "confirmation")
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        statement,
                    ),
                    (),
                    (),
                )

                self.assertEqual(semantic.objective.text, "建立 V2")
                self.assertEqual(
                    semantic.objective.confirmation_status,
                    "unconfirmed",
                )
                self.assertIsNone(semantic.confirmation)

    def test_confirmation_noun_prefixes_preserve_prior_approval(self):
        statements = (
            "確認碼是 123456",
            "確認信已寄出",
            "確認鍵壞了",
            "核准函編號 42",
            "核准書版本 3",
        )
        for statement_text in statements:
            with self.subTest(statement=statement_text):
                prior = event("核准 V2 規格", 2)
                statement = event(statement_text, 3)
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prior,
                        statement,
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.source_hash,
                    prior.source_hash,
                )
                self.assertEqual(
                    semantic.confirmation.target_label,
                    "V2 規格",
                )

    def test_confirmation_without_keyword_boundary_is_ignored(self):
        statement = event("核准V2規格", 2)
        self.assertEqual(statement.event_kind, "confirmation")
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                statement,
            ),
            (),
            (),
        )

        self.assertIsNone(semantic.confirmation)
        self.assertEqual(
            semantic.objective.confirmation_status,
            "unconfirmed",
        )

    def test_confirmation_with_clear_delimiter_is_approved(self):
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                event("確認：V2 規格", 2),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(semantic.confirmation.target_label, "V2 規格")
        self.assertEqual(
            semantic.confirmation.target_hash,
            sha256("V2 規格".encode("utf-8")).hexdigest(),
        )

    def test_compound_confirmation_ignores_later_noun_clause(self):
        messages = (
            "確認。確認碼是 123456",
            "核准！核准函編號 42",
            "確認？確認鍵壞了",
            "確認.確認碼是 123456",
            "核准;核准函編號 42",
            "確認；確認鍵壞了",
        )
        for message in messages:
            with self.subTest(message=message):
                compound = event(message, 2)
                self.assertEqual(compound.event_kind, "confirmation")
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        compound,
                    ),
                    (),
                    (),
                )

                self.assertEqual(semantic.confirmation.status, "ambiguous")
                self.assertTrue(
                    semantic.confirmation.requires_confirmation
                )
                self.assertIsNone(semantic.confirmation.target_label)
                self.assertIsNone(semantic.confirmation.target_hash)

    def test_compound_confirmation_replaces_prior_with_bare_ambiguity(self):
        messages = (
            "確認。確認碼是 123456",
            "核准！核准函編號 42",
            "確認？確認鍵壞了",
            "確認.確認碼是 123456",
            "核准;核准函編號 42",
            "確認；確認鍵壞了",
        )
        for message in messages:
            with self.subTest(message=message):
                compound = event(message, 3)
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        event("核准 V2 規格", 2),
                        compound,
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.source_hash,
                    compound.source_hash,
                )
                self.assertEqual(semantic.confirmation.status, "ambiguous")
                self.assertTrue(
                    semantic.confirmation.requires_confirmation
                )
                self.assertIsNone(semantic.confirmation.target_label)
                self.assertIsNone(semantic.confirmation.target_hash)

    def test_compound_bare_approval_resolves_one_pending_prompt(self):
        prompt = event("請核准 V2 規格", 2, "assistant")
        compound = event("核准。確認碼是 123456", 3)
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                compound,
            ),
            (),
            (),
        )

        self.assertEqual(compound.event_kind, "confirmation")
        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(
            semantic.confirmation.target_label,
            "pending confirmation",
        )
        self.assertEqual(
            semantic.confirmation.target_hash,
            prompt.source_hash,
        )
        self.assertFalse(semantic.confirmation.requires_confirmation)

    def test_confirmation_first_clause_preserves_legal_forms(self):
        bare = build_semantic_evidence(
            (event("建立 V2", 1), event("核准。", 2)),
            (),
            (),
        )
        named = build_semantic_evidence(
            (event("建立 V2", 1), event("核准 V2 規格。", 2)),
            (),
            (),
        )
        prompt = event("請核准 V2 規格", 2, "assistant")
        combined = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("核准並開始實測", 3),
            ),
            (),
            (),
        )

        self.assertEqual(bare.confirmation.status, "ambiguous")
        self.assertIsNone(bare.confirmation.target_label)
        self.assertEqual(named.confirmation.status, "approved")
        self.assertEqual(named.confirmation.target_label, "V2 規格")
        self.assertEqual(combined.confirmation.status, "approved")
        self.assertEqual(
            combined.confirmation.target_label,
            "pending confirmation",
        )
        self.assertEqual(combined.confirmation.target_hash, prompt.source_hash)
        self.assertEqual(combined.confirmation.requested_action, "start")

    def test_confirmation_clause_preserves_enclosures_and_versions(self):
        messages = (
            "核准「V2.3 規格？」。後續說明",
            "核准 V2.3 規格。後續說明",
        )
        for message in messages:
            with self.subTest(message=message):
                compound = event(message, 2)
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        compound,
                    ),
                    (),
                    (),
                )

                self.assertEqual(compound.event_kind, "confirmation")
                self.assertEqual(semantic.confirmation.status, "approved")
                self.assertEqual(
                    semantic.confirmation.target_label,
                    "V2.3 規格",
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    sha256("V2.3 規格".encode("utf-8")).hexdigest(),
                )

    def test_two_pending_prompts_make_bare_approval_ambiguous(self):
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                event("請核准規格 A", 2, "assistant"),
                event("請核准規格 B", 3, "assistant"),
                event("核准", 4),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.objective.text, "建立 V2")
        self.assertEqual(semantic.confirmation.status, "ambiguous")
        self.assertTrue(semantic.confirmation.requires_confirmation)
        self.assertEqual(
            semantic.objective.confirmation_status,
            "ambiguous",
        )

    def test_binary_affirmation_approves_one_pending_prompt(self):
        prompt = event("請確認是否繼續", 2, "assistant")
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("yes", 3),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.kind, "affirmation")
        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(
            semantic.confirmation.target_label,
            "pending confirmation",
        )
        self.assertEqual(semantic.confirmation.target_hash, prompt.source_hash)

    def test_binary_affirmation_is_ambiguous_after_intervening_event(self):
        intervening_events = (
            event("為什麼需要確認？", 3),
            event("背景資訊", 3, "assistant"),
        )
        for intervening in intervening_events:
            with self.subTest(kind=intervening.event_kind):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        event("請確認是否繼續", 2, "assistant"),
                        intervening,
                        event("yes", 4),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "ambiguous",
                )
                self.assertTrue(
                    semantic.confirmation.requires_confirmation
                )

    def test_open_ended_prompt_is_not_a_binary_confirmation(self):
        prompts = (
            "請確認你想要哪個規格？",
            "舊規格已確認。請回覆你想要哪個新規格？",
            "請回覆你想要哪個規格？",
        )
        for prompt_text in prompts:
            with self.subTest(prompt=prompt_text):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        event(prompt_text, 2, "assistant"),
                        event("yes", 3),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "ambiguous",
                )
                self.assertTrue(
                    semantic.confirmation.requires_confirmation
                )

    def test_choice_prompts_are_not_binary_confirmations(self):
        prompts = (
            "請確認你想選哪一種規格？",
            "請確認要 A 還是 B？",
            "請確認 A、B 或 C 中選一項？",
        )
        for prompt_text in prompts:
            with self.subTest(prompt=prompt_text):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        event(prompt_text, 2, "assistant"),
                        event("yes", 3),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "ambiguous",
                )
                self.assertTrue(
                    semantic.confirmation.requires_confirmation
                )

    def test_wh_and_alternative_prompts_fail_closed(self):
        prompts = (
            "請確認選 A/B 二選一嗎？",
            "請確認選誰嗎？",
            "請確認何者適用嗎？",
            "請確認選 A 與 B 嗎？",
            "請確認選 A 和 B 嗎？",
            "請確認選 A／B 嗎？",
            "請確認選 A vs B 嗎？",
            "請確認要幾個嗎？",
            "請確認何時開始嗎？",
            "請確認哪裡適用嗎？",
            "請確認需要多少嗎？",
        )
        for prompt_text in prompts:
            for affirmation in ("yes", "可以"):
                with self.subTest(
                    prompt=prompt_text,
                    affirmation=affirmation,
                ):
                    semantic = build_semantic_evidence(
                        (
                            event("建立 V2", 1),
                            event(prompt_text, 2, "assistant"),
                            event(affirmation, 3),
                        ),
                        (),
                        (),
                    )

                    self.assertNotEqual(
                        semantic.confirmation.status,
                        "approved",
                    )
                    self.assertTrue(
                        semantic.confirmation.requires_confirmation
                    )

    def test_broad_which_and_how_many_prompts_fail_closed(self):
        prompts = (
            "請確認是否改用哪套方案？",
            "請確認是否改用哪款方案？",
            "請確認是否改用哪版方案？",
            "請確認是否改用哪條規則？",
            "請確認是否需要幾項？",
            "請確認是否需要幾份？",
            "請確認是否需要几份？",
        )
        for prompt_text in prompts:
            for affirmation in ("yes", "可以"):
                with self.subTest(
                    prompt=prompt_text,
                    affirmation=affirmation,
                ):
                    semantic = build_semantic_evidence(
                        (
                            event("建立 V2", 1),
                            event(prompt_text, 2, "assistant"),
                            event(affirmation, 3),
                        ),
                        (),
                        (),
                    )

                    self.assertEqual(
                        semantic.confirmation.status,
                        "ambiguous",
                    )
                    self.assertTrue(
                        semantic.confirmation.requires_confirmation
                    )

    def test_binary_clause_ignores_background_suffix(self):
        prompt = event(
            "請確認是否繼續。背景：why this matters",
            2,
            "assistant",
        )
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("yes", 3),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(
            semantic.confirmation.target_label,
            "pending confirmation",
        )
        self.assertEqual(semantic.confirmation.target_hash, prompt.source_hash)
        self.assertFalse(semantic.confirmation.requires_confirmation)

    def test_question_boundary_outside_quotes_ends_binary_clause(self):
        prompt = event(
            "請確認是否繼續？背景：why this matters",
            2,
            "assistant",
        )
        for affirmation in ("yes", "可以"):
            with self.subTest(affirmation=affirmation):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prompt,
                        event(affirmation, 3),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "approved",
                )
                self.assertEqual(
                    semantic.confirmation.target_label,
                    "pending confirmation",
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    prompt.source_hash,
                )

    def test_quoted_question_marks_do_not_end_binary_clause(self):
        prompts = (
            '請回覆 "Deploy V2?" yes or no? Background: why this matters',
            "請回覆「是否採用『V2？』規格」yes 或 no？背景：why this matters",
        )
        for prompt_text in prompts:
            with self.subTest(prompt=prompt_text):
                prompt = event(prompt_text, 2, "assistant")
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prompt,
                        event("yes", 3),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "approved",
                )
                self.assertEqual(
                    semantic.confirmation.target_label,
                    "pending confirmation",
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    prompt.source_hash,
                )

    def test_unbalanced_quotes_fail_closed(self):
        prompts = (
            "請確認「是否繼續？背景說明",
            '請確認 "whether to continue? background',
        )
        for prompt_text in prompts:
            with self.subTest(prompt=prompt_text):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        event(prompt_text, 2, "assistant"),
                        event("yes", 3),
                    ),
                    (),
                    (),
                )

                self.assertNotEqual(
                    semantic.confirmation.status,
                    "approved",
                )
                self.assertTrue(
                    semantic.confirmation.requires_confirmation
                )

    def test_english_binary_prompt_shapes_are_approved(self):
        prompts = (
            "請回覆 can we deploy V2 spec？",
            "請回覆 whether to deploy V2 spec？",
        )
        for prompt_text in prompts:
            with self.subTest(prompt=prompt_text):
                prompt = event(prompt_text, 2, "assistant")
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prompt,
                        event("yes", 3),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "approved",
                )
                self.assertEqual(
                    semantic.confirmation.target_label,
                    "pending confirmation",
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    prompt.source_hash,
                )
                self.assertFalse(
                    semantic.confirmation.requires_confirmation
                )

    def test_explicit_yes_or_no_clause_is_binary_confirmation(self):
        prompt = event(
            "請回覆「部署規格 A？」yes 或 no",
            2,
            "assistant",
        )
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("yes", 3),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(
            semantic.confirmation.target_label,
            "pending confirmation",
        )
        self.assertEqual(semantic.confirmation.target_hash, prompt.source_hash)
        self.assertFalse(semantic.confirmation.requires_confirmation)

    def test_chinese_yes_or_no_token_is_binary_confirmation(self):
        prompt = event("請回覆是否部署，是或否", 2, "assistant")
        for affirmation in ("yes", "可以"):
            with self.subTest(affirmation=affirmation):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prompt,
                        event(affirmation, 3),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "approved",
                )
                self.assertEqual(
                    semantic.confirmation.target_label,
                    "pending confirmation",
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    prompt.source_hash,
                )

    def test_binary_affirmation_without_prompt_is_acknowledgement(self):
        semantic = build_semantic_evidence(
            (event("建立 V2", 1), event("可以", 2)),
            (),
            (),
        )

        self.assertEqual(
            semantic.confirmation.status,
            "acknowledged",
        )

    def test_combined_approval_and_start_keeps_both_states(self):
        semantic = build_semantic_evidence(
            (
                event("建立 V2 並實測", 1),
                event("請核准 V2 規格", 2, "assistant"),
                event("核准並開始實測", 3),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(semantic.confirmation.requested_action, "start")
        self.assertEqual(semantic.objective.text, "建立 V2 並實測")

    def test_approval_then_start_resolves_unique_pending_target(self):
        prompt = event("請核准 V2 規格", 2, "assistant")
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("核准後開始實測", 3),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(
            semantic.confirmation.target_label,
            "pending confirmation",
        )
        self.assertEqual(semantic.confirmation.target_hash, prompt.source_hash)
        self.assertEqual(semantic.confirmation.requested_action, "start")

    def test_targetless_approval_and_start_requires_immediate_unique_prompt(
        self,
    ):
        cases = (
            (
                event("請核准 V2 規格", 2, "assistant"),
                event("背景資訊", 3, "assistant"),
            ),
            (
                event("請核准規格 A", 2, "assistant"),
                event("請核准規格 B", 3, "assistant"),
            ),
        )
        for prompt, intervening in cases:
            with self.subTest(intervening=intervening.normalized_text):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prompt,
                        intervening,
                        event("核准後開始實測", 4),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "ambiguous",
                )
                self.assertEqual(
                    semantic.confirmation.requested_action,
                    "start",
                )
                self.assertTrue(
                    semantic.confirmation.requires_confirmation
                )

    def test_named_approval_strips_trailing_start_action(self):
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                event("請核准 V2 規格", 2, "assistant"),
                event("核准 V2 規格並開始實測", 3),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.target_label, "V2 規格")
        self.assertEqual(
            semantic.confirmation.target_hash,
            sha256("V2 規格".encode("utf-8")).hexdigest(),
        )
        self.assertEqual(semantic.confirmation.requested_action, "start")

    def test_named_approval_supports_control_connector_variants(self):
        approvals = (
            "核准 V2 規格然後開始實測",
            "核准 V2 規格然後再開始實測",
            "核准 V2 規格後，開始實測",
            "核准 V2 規格並且開始實測",
            "核准 V2 規格後再開始實測",
            "核准 V2 規格並 繼續實測",
            "核准 V2 規格之後開始實測",
            "核准 V2 規格之後再開始驗證",
            "核准 V2 規格稍後開始測試",
            "核准 V2 規格隨後開始執行",
            "核准 V2 規格日後開始驗證",
            "核准 V2 規格往後繼續實測",
        )
        for approval in approvals:
            with self.subTest(approval=approval):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        event(approval, 2),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.target_label,
                    "V2 規格",
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    sha256("V2 規格".encode("utf-8")).hexdigest(),
                )
                self.assertEqual(
                    semantic.confirmation.requested_action,
                    "start",
                )
                self.assertFalse(
                    semantic.confirmation.requires_confirmation
                )

    def test_control_like_target_nouns_are_not_commands(self):
        targets = (
            "售後開始日期規格",
            "V2 規格之後開始版本",
            "V2 規格稍後開始日期",
            "V2 規格隨後開始版本",
            "V2 規格日後開始規格",
            "V2 規格往後開始日期",
        )
        for target in targets:
            with self.subTest(target=target):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        event(f"核准 {target}", 2),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.target_label,
                    target,
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    sha256(target.encode("utf-8")).hexdigest(),
                )
                self.assertIsNone(
                    semantic.confirmation.requested_action
                )

    def test_named_approval_canonicalizes_trailing_punctuation(self):
        prompt = event("請核准規格 A。", 2, "assistant")
        approved = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("核准規格 A。", 3),
            ),
            (),
            (),
        )
        after_bare_approval = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("核准規格 A。", 3),
                event("核准", 4),
            ),
            (),
            (),
        )

        self.assertEqual(approved.confirmation.target_label, "規格 A")
        self.assertEqual(
            approved.confirmation.target_hash,
            sha256("規格 A".encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            after_bare_approval.confirmation.status,
            "ambiguous",
        )

    def test_prompt_wrapper_matches_canonical_named_target(self):
        prompt = event(
            "請確認是否採用規格 A？",
            2,
            "assistant",
        )
        approved = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("核准規格 A", 3),
            ),
            (),
            (),
        )
        after_bare_approval = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("核准規格 A", 3),
                event("核准", 4),
            ),
            (),
            (),
        )

        self.assertEqual(approved.confirmation.status, "approved")
        self.assertEqual(approved.confirmation.target_label, "規格 A")
        self.assertEqual(
            approved.confirmation.target_hash,
            sha256("規格 A".encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            after_bare_approval.confirmation.status,
            "ambiguous",
        )

    def test_named_approval_unwraps_matching_enclosures(self):
        wrapped_targets = (
            "《規格 A》",
            "〈規格 A〉",
            "〔規格 A〕",
            "`規格 A`",
            "{規格 A}",
        )
        for wrapped_target in wrapped_targets:
            with self.subTest(target=wrapped_target):
                prompt = event("請核准規格 A", 2, "assistant")
                approved = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prompt,
                        event(f"核准{wrapped_target}", 3),
                    ),
                    (),
                    (),
                )
                after_bare_approval = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prompt,
                        event(f"核准{wrapped_target}", 3),
                        event("核准", 4),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    approved.confirmation.target_label,
                    "規格 A",
                )
                self.assertEqual(
                    approved.confirmation.target_hash,
                    sha256("規格 A".encode("utf-8")).hexdigest(),
                )
                self.assertEqual(
                    after_bare_approval.confirmation.status,
                    "ambiguous",
                )
                self.assertTrue(
                    after_bare_approval.confirmation.requires_confirmation
                )

    def test_control_preserves_existing_approval(self):
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                event("核准 V2 規格", 2),
                event("繼續", 3),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(semantic.confirmation.target_label, "V2 規格")
        self.assertEqual(semantic.confirmation.requested_action, "start")

    def test_control_without_active_does_not_reuse_prior_approval(self):
        semantic = build_semantic_evidence(
            (event("核准 V2 規格", 1), event("開始", 2)),
            (),
            (),
        )

        self.assertIsNone(semantic.objective)
        self.assertEqual(semantic.confirmation.status, "ambiguous")
        self.assertEqual(semantic.confirmation.requested_action, "start")
        self.assertTrue(semantic.confirmation.requires_confirmation)

    def test_control_without_active_downgrades_document_fallback(self):
        semantic = build_semantic_evidence(
            (event("核准 V2 規格", 1), event("開始", 2)),
            (
                section(
                    "PROJECT_STATUS.md",
                    "Ship documented V2",
                    head_matches=True,
                ),
            ),
            ("PROJECT_STATUS.md",),
        )

        self.assertEqual(semantic.objective.text, "Ship documented V2")
        self.assertTrue(semantic.objective.requires_confirmation)
        self.assertEqual(
            semantic.objective.confirmation_status,
            "ambiguous",
        )
        self.assertEqual(semantic.confirmation.requested_action, "start")

    def test_combined_control_without_active_requires_confirmation(self):
        semantic = build_semantic_evidence(
            (event("核准 V2 規格並開始實測", 1),),
            (
                section(
                    "PROJECT_STATUS.md",
                    "Delete production",
                    head_matches=True,
                ),
            ),
            ("PROJECT_STATUS.md",),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(semantic.confirmation.target_label, "V2 規格")
        self.assertEqual(semantic.confirmation.requested_action, "start")
        self.assertTrue(semantic.confirmation.requires_confirmation)
        self.assertTrue(semantic.objective.requires_confirmation)
        self.assertTrue(
            any(
                "no active objective" in reason
                for reason in semantic.confirmation.reasons
            )
        )

    def test_spaced_control_without_active_downgrades_document_fallback(self):
        semantic = build_semantic_evidence(
            (event("核准 V2 規格後，開始實測", 1),),
            (
                section(
                    "PROJECT_STATUS.md",
                    "Ship documented V2",
                    head_matches=True,
                ),
            ),
            ("PROJECT_STATUS.md",),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(semantic.confirmation.target_label, "V2 規格")
        self.assertEqual(semantic.confirmation.requested_action, "start")
        self.assertTrue(semantic.confirmation.requires_confirmation)
        self.assertTrue(semantic.objective.requires_confirmation)

    def test_start_substring_in_named_target_is_not_control(self):
        targets = ("開始日期規格", "繼續教育規格")
        for target in targets:
            with self.subTest(target=target):
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        event(f"核准 {target}", 2),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.status,
                    "approved",
                )
                self.assertEqual(
                    semantic.confirmation.target_label,
                    target,
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    sha256(target.encode("utf-8")).hexdigest(),
                )
                self.assertIsNone(
                    semantic.confirmation.requested_action
                )

    def test_amendment_and_reference_do_not_replace_parent_goal(self):
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                event("不要忽略好的與核准", 2),
                event("第二種", 3),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.objective.text, "建立 V2")
        self.assertEqual(
            semantic.objective.amendments,
            ("不要忽略好的與核准",),
        )
        self.assertTrue(semantic.objective.requires_confirmation)

    def test_actionable_amendment_without_parent_becomes_objective(self):
        semantic = build_semantic_evidence(
            (event("不要刪除現有資料", 1),),
            (),
            (),
        )

        self.assertEqual(semantic.objective.text, "不要刪除現有資料")
        self.assertEqual(semantic.objective.amendments, ())

    def test_replacement_starts_a_new_active_goal_and_resets_state(self):
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                event("不要忽略核准", 2),
                event("核准 V2 規格", 3),
                event("改成建立 V3", 4),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.objective.text, "改成建立 V3")
        self.assertEqual(semantic.objective.amendments, ())
        self.assertEqual(
            semantic.objective.confirmation_status,
            "unconfirmed",
        )
        self.assertIsNone(semantic.confirmation)

    def test_new_objective_clears_old_pending_prompts(self):
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                event("請核准規格 A", 2, "assistant"),
                event("請核准規格 B", 3, "assistant"),
                event("建立 V3", 4),
                event("核准", 5),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.objective.text, "建立 V3")
        self.assertEqual(semantic.confirmation.status, "ambiguous")
        self.assertTrue(semantic.confirmation.requires_confirmation)

    def test_named_approval_only_removes_matching_pending_target(self):
        prompt_b = event("請核准規格 B", 3, "assistant")
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                event("請核准規格 A", 2, "assistant"),
                prompt_b,
                event("核准規格 A", 4),
                event("核准", 5),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(
            semantic.confirmation.target_label,
            "pending confirmation",
        )
        self.assertEqual(
            semantic.confirmation.target_hash,
            prompt_b.source_hash,
        )

    def test_override_is_trimmed_confirmed_and_takes_precedence(self):
        semantic = build_semantic_evidence(
            (event("建立 V2", 1),),
            (
                section(
                    "PROJECT_STATUS.md",
                    "Documented V2",
                ),
            ),
            ("PROJECT_STATUS.md",),
            objective_override="  Ship confirmed V3  ",
            input_limitations=(),
        )

        self.assertEqual(semantic.objective.text, "Ship confirmed V3")
        self.assertEqual(semantic.objective.source_kind, "objective_override")
        self.assertEqual(semantic.objective.status, "confirmed")
        self.assertEqual(semantic.objective.confidence, "high")
        self.assertEqual(semantic.objective.confirmation_status, "confirmed")
        self.assertFalse(semantic.objective.requires_confirmation)
        self.assertEqual(semantic.objective.conflicts, ())

    def test_override_is_confirmed_without_dialogue(self):
        semantic = build_semantic_evidence(
            (),
            (),
            (),
            objective_override="Ship confirmed V2",
            input_limitations=(),
        )

        self.assertEqual(semantic.objective.status, "confirmed")
        self.assertEqual(semantic.objective.confidence, "high")
        self.assertFalse(semantic.objective.requires_confirmation)

    def test_blank_override_falls_back_to_active_dialogue(self):
        semantic = build_semantic_evidence(
            (event("建立 V2", 1),),
            (),
            (),
            objective_override="   ",
        )

        self.assertEqual(semantic.objective.text, "建立 V2")
        self.assertEqual(semantic.objective.source_kind, "user_prompt")

    def test_invisible_only_override_falls_back_to_active_dialogue(self):
        invisibles = (
            "\u200b",
            "\u200d",
            "\u2060",
            "\u00ad",
            "\ufeff",
            "\u034f",
            "\ufe0f",
        )
        for override in invisibles:
            with self.subTest(codepoints=tuple(map(ord, override))):
                semantic = build_semantic_evidence(
                    (event("建立 V2", 1),),
                    (),
                    (),
                    objective_override=override,
                )

                self.assertEqual(semantic.objective.text, "建立 V2")
                self.assertEqual(
                    semantic.objective.source_kind,
                    "user_prompt",
                )

    def test_invisible_only_override_without_fallback_is_absent(self):
        invisibles = (
            "\u200b",
            "\u200d",
            "\u2060",
            "\u00ad",
            "\ufeff",
            "\u034f",
            "\ufe0f",
        )
        for override in invisibles:
            with self.subTest(codepoints=tuple(map(ord, override))):
                semantic = build_semantic_evidence(
                    (),
                    (),
                    (),
                    objective_override=override,
                )

                self.assertIsNone(semantic.objective)

    def test_visible_override_removes_internal_default_ignorables(self):
        overrides = (
            ("Ship\u200b V3", "Ship V3"),
            ("發布\u2060 V3", "發布 V3"),
        )
        for raw, expected in overrides:
            with self.subTest(raw=raw):
                semantic = build_semantic_evidence(
                    (),
                    (),
                    (),
                    objective_override=raw,
                )

                self.assertEqual(semantic.objective.text, expected)
                self.assertEqual(
                    semantic.objective.source_kind,
                    "objective_override",
                )
                self.assertEqual(semantic.objective.status, "confirmed")
                self.assertEqual(semantic.objective.confidence, "high")
                self.assertFalse(semantic.objective.requires_confirmation)
                self.assertEqual(
                    semantic.objective.source_hash,
                    sha256(expected.encode("utf-8")).hexdigest(),
                )

    def test_head_matching_project_status_is_medium_fallback(self):
        objective = section(
            "PROJECT_STATUS.md",
            "Ship semantic V2",
            head_matches=True,
        )
        semantic = build_semantic_evidence(
            (),
            (objective,),
            ("PROJECT_STATUS.md",),
        )

        self.assertEqual(semantic.objective.text, "Ship semantic V2")
        self.assertEqual(semantic.objective.source_kind, "project_status")
        self.assertEqual(semantic.objective.status, "documented")
        self.assertEqual(semantic.objective.confidence, "medium")
        self.assertFalse(semantic.objective.requires_confirmation)

    def test_markerless_project_status_is_low_and_needs_confirmation(self):
        objective = section(
            "PROJECT_STATUS.md",
            "Ship semantic V2",
            head_matches=None,
        )
        semantic = build_semantic_evidence(
            (),
            (objective,),
            ("PROJECT_STATUS.md",),
        )

        self.assertEqual(semantic.objective.status, "documented")
        self.assertEqual(semantic.objective.confidence, "low")
        self.assertTrue(semantic.objective.requires_confirmation)
        self.assertTrue(
            any("HEAD marker" in item for item in semantic.limitations)
        )

    def test_stale_project_status_never_becomes_objective(self):
        stale_text = "STALE private objective"
        semantic = build_semantic_evidence(
            (),
            (
                section(
                    "PROJECT_STATUS.md",
                    stale_text,
                    head_matches=False,
                ),
            ),
            ("PROJECT_STATUS.md",),
        )

        self.assertIsNone(semantic.objective)
        self.assertTrue(
            any("stale" in item.casefold() for item in semantic.limitations)
        )
        self.assertNotIn(stale_text, repr(semantic))

    def test_prompt_wins_different_project_status_and_records_conflict(self):
        semantic = build_semantic_evidence(
            (event("Ship semantic V2", 1),),
            (
                section(
                    "PROJECT_STATUS.md",
                    "Ship legacy V1",
                ),
            ),
            ("PROJECT_STATUS.md",),
        )

        self.assertEqual(semantic.objective.text, "Ship semantic V2")
        self.assertTrue(semantic.objective.requires_confirmation)
        self.assertEqual(len(semantic.objective.conflicts), 1)
        self.assertIn(
            "PROJECT_STATUS.md",
            semantic.objective.conflicts[0],
        )

    def test_normalized_prompt_and_project_status_do_not_conflict(self):
        semantic = build_semantic_evidence(
            (event("Ship semantic V2", 1),),
            (
                section(
                    "PROJECT_STATUS.md",
                    "  ship   semantic v2  ",
                ),
            ),
            ("PROJECT_STATUS.md",),
        )

        self.assertEqual(semantic.objective.conflicts, ())
        self.assertFalse(semantic.objective.requires_confirmation)

    def test_readme_section_cannot_become_objective(self):
        semantic = build_semantic_evidence(
            (),
            (section("README.md", "Build unrelated product"),),
            ("README.md",),
        )

        self.assertIsNone(semantic.objective)
        self.assertEqual(semantic.documents_examined, ("README.md",))

    def test_next_steps_are_limited_to_five_in_input_order(self):
        sections = tuple(
            section(
                "NEXT_STEPS.md",
                f"Step {index}",
                heading="unchecked",
                head_matches=None,
            )
            for index in range(1, 8)
        )
        semantic = build_semantic_evidence(
            (),
            sections,
            ("NEXT_STEPS.md",),
        )

        self.assertEqual(
            semantic.next_steps,
            tuple(f"Step {index}" for index in range(1, 6)),
        )

    def test_explicit_next_steps_override_documents_and_are_bounded(self):
        evidence = build_semantic_evidence(
            (),
            (),
            (),
            objective_override="Ship the confirmed Plugin MVP",
            next_steps_override=(
                "  Add the manifest  ",
                "",
                "Write the Skill",
                "Add resume verification",
                "Run privacy tests",
                "Test local installation",
                "This sixth non-empty step is ignored",
            ),
        )

        self.assertEqual(
            evidence.next_steps,
            (
                "Add the manifest",
                "Write the Skill",
                "Add resume verification",
                "Run privacy tests",
                "Test local installation",
            ),
        )

    def test_input_limitation_downgrades_fresh_document_fallback(self):
        semantic = build_semantic_evidence(
            (),
            (
                section(
                    "PROJECT_STATUS.md",
                    "Ship semantic V2",
                    head_matches=True,
                ),
            ),
            ("PROJECT_STATUS.md",),
            input_limitations=("Session not supplied",),
        )

        self.assertEqual(semantic.objective.confidence, "low")
        self.assertTrue(semantic.objective.requires_confirmation)
        self.assertIn("Session not supplied", semantic.limitations)

    def test_reference_downgrades_fresh_document_fallback(self):
        semantic = build_semantic_evidence(
            (event("第二種", 1),),
            (
                section(
                    "PROJECT_STATUS.md",
                    "Ship semantic V2",
                    head_matches=True,
                ),
            ),
            ("PROJECT_STATUS.md",),
        )

        self.assertEqual(semantic.objective.status, "documented")
        self.assertEqual(semantic.objective.confidence, "medium")
        self.assertTrue(semantic.objective.requires_confirmation)
        self.assertIn(
            "Unresolved dialogue reference",
            semantic.objective.reasons,
        )

    def test_counts_sources_without_returning_assistant_messages(self):
        private_prompt = "請核准 PRIVATE assistant option alpha"
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                event(private_prompt, 2, "assistant"),
                event("好的", 3),
            ),
            (),
            ("README.md", "PROJECT_STATUS.md"),
        )

        self.assertEqual(semantic.dialogue_events_examined, 3)
        self.assertEqual(
            semantic.documents_examined,
            ("README.md", "PROJECT_STATUS.md"),
        )
        self.assertNotIn(private_prompt, repr(semantic))

    def test_pending_target_label_excludes_assistant_background(self):
        private_background = "PRIVATE background；請核准規格 A"
        prompt = event(private_background, 2, "assistant")
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("核准", 3),
            ),
            (),
            (),
        )

        self.assertEqual(
            semantic.confirmation.target_label,
            "pending confirmation",
        )
        self.assertEqual(semantic.confirmation.target_hash, prompt.source_hash)
        self.assertNotIn(private_background, repr(semantic))

    def test_delimited_prompt_label_drops_private_suffix(self):
        private = "PRIVATE assistant reasoning and secret"
        for delimiter in ("；", "，", ",", ";"):
            with self.subTest(delimiter=delimiter):
                prompt = event(
                    f"請核准規格 A{delimiter}{private}",
                    2,
                    "assistant",
                )
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prompt,
                        event("核准", 3),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.target_label,
                    "pending confirmation",
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    prompt.source_hash,
                )
                self.assertNotIn(private, repr(semantic))

    def test_unsafe_prompt_suffix_uses_fixed_generic_label(self):
        unsafe_prompts = (
            "請核准規格 A\nPRIVATE assistant reasoning and secret",
            "請核准規格 A PRIVATE assistant reasoning and secret",
            "請核准規格 A secret",
        )
        for prompt_text in unsafe_prompts:
            with self.subTest(prompt=prompt_text):
                prompt = event(prompt_text, 2, "assistant")
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prompt,
                        event("核准", 3),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.target_label,
                    "pending confirmation",
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    prompt.source_hash,
                )
                rendered = repr(semantic).casefold()
                for secret in ("private", "reasoning", "secret"):
                    self.assertNotIn(secret, rendered)

    def test_arbitrary_assistant_labels_never_enter_semantic_output(self):
        prompt_fragments = (
            "PRIVATE規格",
            "secret規格",
            "規格reasoning",
            "private_v2規格",
            "ZXQJ-8472規格",
        )
        for fragment in prompt_fragments:
            with self.subTest(fragment=fragment):
                prompt = event(f"請核准 {fragment}", 2, "assistant")
                semantic = build_semantic_evidence(
                    (
                        event("建立 V2", 1),
                        prompt,
                        event("核准", 3),
                    ),
                    (),
                    (),
                )

                self.assertEqual(
                    semantic.confirmation.target_label,
                    "pending confirmation",
                )
                self.assertEqual(
                    semantic.confirmation.target_hash,
                    prompt.source_hash,
                )
                rendered = (
                    f"{semantic!r} {asdict(semantic)!r}"
                ).casefold()
                self.assertNotIn(fragment.casefold(), rendered)
                self.assertNotIn(
                    prompt.normalized_text.casefold(),
                    rendered,
                )

    def test_generic_pending_label_is_not_cleared_by_named_approval(self):
        prompt = event(
            "請核准規格 A PRIVATE assistant reasoning and secret",
            2,
            "assistant",
        )
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                prompt,
                event("核准規格 A", 3),
                event("核准", 4),
            ),
            (),
            (),
        )

        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(
            semantic.confirmation.target_label,
            "pending confirmation",
        )
        self.assertEqual(semantic.confirmation.target_hash, prompt.source_hash)
        self.assertNotIn("PRIVATE", repr(semantic))
