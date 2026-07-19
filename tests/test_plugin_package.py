import json
import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class PluginPackageTests(TestCase):
    def test_readme_leads_with_beginner_quick_start_and_keeps_claims_bounded(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        def extract_sections(document, level):
            marker = "#" * level
            headings = list(
                re.finditer(
                    rf"^{marker} ([^#].*)$", document, re.MULTILINE
                )
            )
            return [
                (
                    heading.group(1),
                    document[
                        heading.end(): (
                            headings[index + 1].start()
                            if index + 1 < len(headings)
                            else len(document)
                        )
                    ],
                )
                for index, heading in enumerate(headings)
            ]

        top_level_sections = extract_sections(readme, 2)
        titles = [title for title, _ in top_level_sections]
        quick = titles.index("Three-step quick start")
        chinese = titles.index("简体中文")
        advanced = titles.index("Advanced CLI")
        self.assertLess(quick, chinese)
        self.assertLess(chinese, advanced)
        self.assertEqual(advanced, len(top_level_sections) - 1)

        english_sections = {
            title: content.casefold()
            for title, content in top_level_sections[:chinese]
        }
        chinese_sections = {
            title: content.casefold()
            for title, content in extract_sections(
                top_level_sections[chinese][1], 3
            )
        }
        parity_requirements = {
            "github installation": (
                (
                    english_sections["Install from GitHub"],
                    (
                        "codex plugin marketplace add jokercm991102-svg/context-relay --ref main",
                        "codex plugin add context-relay@context-relay",
                        "restart",
                    ),
                ),
                (
                    chinese_sections["从 GitHub 安装"],
                    (
                        "codex plugin marketplace add jokercm991102-svg/context-relay --ref main",
                        "codex plugin add context-relay@context-relay",
                        "重新启动",
                    ),
                ),
            ),
            "create and confirm": (
                (
                    english_sections["Three-step quick start"]
                    + english_sections["Beginner walkthrough"],
                    (
                        "$context-relay",
                        "this task is getting long",
                        "explicitly confirm",
                        "does not start the scan",
                    ),
                ),
                (
                    chinese_sections["三步快速开始"]
                    + chinese_sections["初学者操作示例"],
                    (
                        "$context-relay",
                        "这个任务变得很长",
                        "明确确认后",
                        "明确回复",
                        "不会开始扫描",
                    ),
                ),
            ),
            "fresh-task resume and verification": (
                (
                    english_sections["Beginner walkthrough"],
                    (
                        "fresh codex task",
                        "$context-relay` to resume",
                        "verifies the recorded project state",
                    ),
                ),
                (
                    chinese_sections["初学者操作示例"],
                    (
                        "新的 codex 任务",
                        "`$context-relay` 恢复工作",
                        "会先验证项目状态",
                    ),
                ),
            ),
            "privacy": (
                (
                    english_sections["Privacy and safety"],
                    (
                        "local, read-only",
                        "no network request",
                        "does not read the raw codex transcript",
                    ),
                ),
                (
                    chinese_sections["隐私与安全"],
                    (
                        "只在本地运行、只读",
                        "不发起网络请求",
                        "不读取原始 codex 对话",
                    ),
                ),
            ),
            "bundle trust boundary": (
                (
                    english_sections["Privacy and safety"],
                    (
                        "use only a bundle you created locally or otherwise trust",
                        "does not authenticate who created the bundle",
                        "does not authenticate the contents of the handoff files",
                        "shared, untrusted, or could have been modified",
                        (
                            "changes after verification and before use are outside "
                            "this mvp's protection"
                        ),
                        "review protects privacy, not authenticity",
                    ),
                ),
                (
                    chinese_sections["隐私与安全"],
                    (
                        "只使用你在本地创建或通过其他方式信任的交接包",
                        "不会认证交接包的创建者",
                        "不会认证交接文件的内容",
                        "来自分享、你不信任，或可能已被修改",
                        "验证后、使用前发生的修改不在本 mvp 的保护范围内",
                        "检查用于保护隐私，不是为了证明真实性",
                    ),
                ),
            ),
            "exit-code troubleshooting": (
                (
                    english_sections["Limits and troubleshooting"],
                    ("result `0`", "result `2`", "result `3`"),
                ),
                (
                    chinese_sections["限制与故障排除"],
                    ("结果 `0`", "结果 `2`", "结果 `3`"),
                ),
            ),
            "no faster-model guarantee": (
                (
                    english_sections["What is proven"]
                    + english_sections["Limits and troubleshooting"],
                    (
                        "does not make model inference faster",
                        "guarantee faster model responses",
                    ),
                ),
                (
                    chinese_sections["已经证明的内容"]
                    + chinese_sections["限制与故障排除"],
                    (
                        "不能证明 context relay 会让模型推理更快",
                        "不保证模型回复更快",
                    ),
                ),
            ),
        }
        for topic, language_requirements in parity_requirements.items():
            for language, (guide, phrases) in zip(
                ("english", "simplified chinese"), language_requirements
            ):
                for phrase in phrases:
                    with self.subTest(
                        topic=topic, language=language, phrase=phrase
                    ):
                        self.assertIn(phrase.casefold(), guide)
        for phrase in (
            "three objective elements",
            "v1 recovered none",
            "receiver comprehension remains unverified",
        ):
            self.assertIn(
                phrase,
                english_sections["What is proven"],
            )
        self.assertNotIn("/" + "Users" + "/", readme)
        self.assertNotIn(
            "session JSONL path",
            english_sections["Three-step quick start"],
        )

    def test_skill_contract_is_narrow_safe_and_bilingual(self):
        skill = (ROOT / "skills/context-relay/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("name: context-relay", skill)
        self.assertIn("description: Use when", skill)
        self.assertIn("这个任务变得很长", skill)
        self.assertIn(
            "not for ordinary coding requests",
            skill.casefold(),
        )

    def test_skill_announces_visible_confirmation_workflows(self):
        skill = (ROOT / "skills/context-relay/SKILL.md").read_text(
            encoding="utf-8"
        )
        required = (
            "Create has 4 visible steps and 2 confirmation gates.",
            "Resume has 3 visible steps and 1 confirmation gate.",
            "Confirmation required",
            "Running",
            "Complete",
            "Stopped",
            "Scanning will not start before confirmation.",
            "The handoff bundle will not be created before explicit confirmation.",
            "Show Running. Confirmation complete. Creating the local handoff bundle now.",
            "Verification will not start before confirmation.",
            "Show Running. Confirmation complete. Verifying the project and handoff bundle state now.",
            "Context Relay is not continuing execution.",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.casefold(), skill.casefold())

        forbidden = (
            "you can leave",
            "leave the screen",
            "wait elsewhere",
            "remain on the screen",
        )
        for phrase in forbidden:
            with self.subTest(forbidden=phrase):
                self.assertNotIn(phrase.casefold(), skill.casefold())

    def test_readme_explains_visible_confirmation_workflow(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        required = (
            "Create has 4 steps; steps 1 and 2 require your confirmation.",
            "Resume has 3 steps; step 1 requires your confirmation.",
            "Confirmation required",
            "it changes to **Running**. Confirmation complete.",
            "Create 共 4 个步骤，第 1、2 步需要你确认。",
            "Resume 共 3 个步骤，第 1 步需要你确认。",
            "需要确认",
            "状态会变为 **执行中**。确认部分已完成。",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.casefold(), readme.casefold())

        for phrase in (
            "Confirmation complete — Running",
            "确认部分已完成｜执行中",
        ):
            with self.subTest(forbidden=phrase):
                self.assertNotIn(phrase.casefold(), readme.casefold())

    def test_skill_contract_locks_normative_safety_semantics(self):
        skill = (ROOT / "skills/context-relay/SKILL.md").read_text(
            encoding="utf-8"
        )
        required = (
            (
                "Keep Context Relay local with no network request. Do not edit "
                "the target repository."
            ),
            (
                "Ask the user to confirm or correct that identified handoff. "
                "Treat “好的” as acknowledgement, not authorization; a bare "
                "unidentified approval is also not authorization. Do not run "
                "until the user explicitly confirms the shown Context Relay "
                "objective and steps."
            ),
            (
                "Resolve `scripts/run_context_relay.py` relative to this "
                "`SKILL.md`. Use only that resolved wrapper; do not invoke the "
                "core CLI directly. Run its `create` command with the project, "
                "confirmed objective, and each of the no more than five "
                "confirmed next steps."
            ),
            (
                "The wrapper sanitizes successful output: it prints the bundle "
                "as a home-relative `~` location and never exposes `Path.home()`."
            ),
            "Do not pass `--session` or `--include-text` in the beginner flow.",
            (
                "Inspect only the generated `manifest.json`, `CHECKPOINT.md`, "
                "`HANDOFF.md`, and `report.md`."
            ),
            "Do not inspect the original conversation.",
            (
                "Resolve `scripts/run_context_relay.py` relative to this "
                "`SKILL.md` and run its `resume` command before reading bundle "
                "contents."
            ),
            (
                "If verification reports a state match, read only `HANDOFF.md`, "
                "`CHECKPOINT.md`, `report.md`, and `manifest.json`. Do not read "
                "assessment.json."
            ),
            (
                "Once verification reports stale, do not re-run verification "
                "on that bundle and do not continue from it."
            ),
            "Require a bundle the user created locally and trusts.",
            (
                "If the bundle was shared, is untrusted, or could have been "
                "modified, stop before verification and ask for a fresh trusted "
                "Context Relay handoff."
            ),
            (
                "Verification checks recorded project state and manifest/bundle "
                "shape. It does not authenticate the bundle creator or "
                "handoff-file content."
            ),
            (
                "Do not claim verification protects against a local mutation "
                "after verification."
            ),
            (
                "Do not claim that Context Relay measures the context window "
                "or makes model inference faster."
            ),
            "Do not locate a session JSONL file automatically.",
            "Do not copy raw transcript text into the bundle.",
            (
                "Do not bypass output-path, staleness, privacy, or confirmation "
                "checks."
            ),
            "Tell the user to review a bundle before sharing it.",
        )
        for phrase in required:
            self.assertIn(phrase.casefold(), skill.casefold())

    def test_skill_closes_fresh_agent_privacy_and_stale_loopholes(self):
        skill = (ROOT / "skills/context-relay/SKILL.md").read_text(
            encoding="utf-8"
        )
        required = (
            (
                "Do not ask the user for a session JSONL, raw transcript, "
                "or permission to include either."
            ),
            (
                "Draft the objective from the visible current task, latest "
                "user request, and safe project documents instead."
            ),
            (
                "Once verification reports stale, do not re-run verification "
                "on that bundle and do not continue from it."
            ),
            "Ask for a fresh Context Relay handoff.",
        )
        for phrase in required:
            self.assertIn(phrase.casefold(), skill.casefold())

    def test_manifest_skill_path_exists_inside_plugin_root(self):
        manifest = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        skill_root = (ROOT / manifest["skills"]).resolve()
        try:
            skill_root.relative_to(ROOT.resolve())
        except ValueError:
            self.fail("manifest skill path escapes the plugin root")
        self.assertTrue((skill_root / "context-relay/SKILL.md").is_file())

    def test_manifest_has_valid_stable_contract(self):
        payload = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            payload,
            {
                "name": "context-relay",
                "version": "0.1.1",
                "description": (
                    "Create a local, read-only Codex project handoff and compare "
                    "recorded project state before resuming."
                ),
                "author": {"name": "Context Relay Contributors"},
                "homepage": "https://github.com/jokercm991102-svg/context-relay#readme",
                "repository": "https://github.com/jokercm991102-svg/context-relay",
                "license": "MIT",
                "keywords": [
                    "codex",
                    "context",
                    "handoff",
                    "productivity",
                ],
                "skills": "./skills/",
                "interface": {
                    "displayName": "Context Relay",
                    "shortDescription": (
                        "Create a local, read-only Codex project handoff."
                    ),
                    "longDescription": (
                        "Confirm the current objective and next steps, create a local "
                        "handoff, and compare recorded project state before resuming. "
                        "Use only user-trusted local bundles."
                    ),
                    "developerName": "Context Relay Contributors",
                    "category": "Productivity",
                    "capabilities": ["Interactive"],
                    "websiteURL": (
                        "https://github.com/jokercm991102-svg/context-relay"
                    ),
                    "defaultPrompt": [
                        "This task is getting long. Confirm the current objective "
                        "and prepare a handoff."
                    ],
                },
            },
        )
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2026 Context Relay Contributors", license_text)

    def test_bilingual_design_scopes_bundle_content_trust(self):
        design = (
            ROOT
            / "docs/superpowers/specs/2026-07-14-context-relay-plugin-mvp-design.md"
        ).read_text(encoding="utf-8")
        required = (
            (
                "Use only a bundle the user created locally and trusts. "
                "Verification checks recorded project state and manifest/bundle "
                "shape; it does not authenticate the bundle creator or "
                "handoff-file content."
            ),
            (
                "If a bundle was shared, is untrusted, or could have been "
                "modified, stop before verification and request a fresh trusted "
                "handoff. A local mutation after verification is outside this "
                "MVP's protection."
            ),
            (
                "只使用用户在本地创建并且信任的交接包。验证会检查已记录的项目状态以及"
                " manifest/交接包结构；它不会认证交接包创建者或交接文件内容。"
            ),
            (
                "如果交接包来自分享、不受信任或可能已被修改，请在验证前停止，并要求创建"
                "一份新的可信交接。验证后的本地修改不在本 MVP 的保护范围内。"
            ),
        )
        for phrase in required:
            self.assertIn(phrase, design)

    def test_success_terminology_means_only_recorded_state_match(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        validation = (
            ROOT
            / "docs/validation/2026-07-14-context-relay-v2-fixed-ab.md"
        ).read_text(encoding="utf-8")
        legacy_validation = (
            ROOT / "docs/validation/2026-07-13-66day-real-run.md"
        ).read_text(encoding="utf-8")
        design = (
            ROOT
            / "docs/superpowers/specs/2026-07-14-context-relay-plugin-mvp-design.md"
        ).read_text(encoding="utf-8")
        plan = (
            ROOT
            / "docs/superpowers/plans/2026-07-14-context-relay-plugin-mvp.md"
        ).read_text(encoding="utf-8")

        self.assertIn(
            (
                "For `verify`, exit `0` means the recorded project state matches; "
                "it does not authenticate bundle provenance or contents."
            ),
            readme,
        )
        self.assertNotIn("Exit codes are `0` for safe", readme)
        self.assertIn("`verification: state-match`", validation)
        self.assertIn("recorded project state matched", validation)
        self.assertIn("This proves recorded Git-state recovery", legacy_validation)
        self.assertNotIn("This proves safe state recovery", legacy_validation)
        self.assertIn(
            "whether the recorded project state matches",
            design,
        )
        self.assertIn("当前已记录的项目状态是否相符", design)
        self.assertIn(
            "enables a state-checked continuation from a trusted local bundle",
            design,
        )
        self.assertIn("支持从可信本地交接包继续已检查状态的工作", design)
        self.assertNotIn("verification: safe", plan)
        self.assertNotIn("verification is not safe", plan)
        self.assertNotIn("Exit codes are `0` for safe", plan)
        self.assertNotIn("Create a safe, local Codex project handoff.", plan)
        self.assertIn("verification: state-match", plan)

    def test_repo_marketplace_points_to_plugin_root(self):
        payload = json.loads(
            (ROOT / ".agents/plugins/marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["name"], "context-relay")
        self.assertEqual(payload["interface"]["displayName"], "Context Relay")
        self.assertEqual(len(payload["plugins"]), 1)
        plugin = payload["plugins"][0]
        self.assertEqual(plugin["name"], "context-relay")
        self.assertEqual(plugin["source"], {"source": "local", "path": "./"})
        self.assertEqual(plugin["policy"]["installation"], "AVAILABLE")
        self.assertEqual(plugin["policy"]["authentication"], "ON_INSTALL")
        self.assertEqual(plugin["category"], "Productivity")
