from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROGRAM_HOOK_PATH = (
    REPOSITORY_ROOT / ".codex" / "hooks" / "country_outage_agent_program_review.py"
)
P1_HOOK_PATH = (
    REPOSITORY_ROOT / ".codex" / "hooks" / "country_outage_agent_p1_alignment.py"
)


def load_program_hook():
    specification = importlib.util.spec_from_file_location(
        "country_outage_agent_program_review_for_p1",
        PROGRAM_HOOK_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 Hook：{PROGRAM_HOOK_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_p1_hook():
    hook_directory = str(P1_HOOK_PATH.parent)
    if hook_directory not in sys.path:
        sys.path.insert(0, hook_directory)
    specification = importlib.util.spec_from_file_location(
        "country_outage_agent_p1_alignment_for_test",
        P1_HOOK_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 Hook：{P1_HOOK_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageAgentP1AlignmentTest(unittest.TestCase):
    def run_hook(self, stage: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(P1_HOOK_PATH), "--stage", stage],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_p1_config_documents_and_task_boundary_are_valid(self) -> None:
        module = load_program_hook()
        config = module.load_project_config("P1")
        self.assertEqual(module.validate_config(config, expected_project="P1"), [])
        self.assertEqual(module.validate_documents(config), [])
        self.assertEqual(module.validate_task_boundary(), [])

    def test_all_p1_stages_emit_semantic_review_without_claiming_acceptance(self) -> None:
        for stage in (f"S{index}" for index in range(5)):
            with self.subTest(stage=stage):
                result = self.run_hook(stage)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"阶段结束回检：{stage}", result.stdout)
                self.assertIn("开放 UserGoalPlan", result.stdout)
                self.assertIn("独立专家角色", result.stdout)
                self.assertIn("不代表验收案例", result.stdout)
                self.assertIn(
                    f"国家中断 Agent P1 最终验收回检：{stage}",
                    result.stdout,
                )
                self.assertIn("未提供 --evidence-manifest", result.stdout)

    def test_requirement_or_flexibility_drift_is_rejected(self) -> None:
        module = load_program_hook()
        config = module.load_project_config("P1")
        acceptance_path = module.safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        plan_path = module.safe_repository_path(config["plan_path"], "plan_path")
        acceptance = module.read_text(acceptance_path)
        plan = module.read_text(plan_path)

        errors = module.validate_document_texts(
            config,
            acceptance.replace("### P1-CTR-12：", "### P1-CTR-99：", 1),
            plan,
        )
        self.assertTrue(any("P1-CTR-01 至 P1-CTR-24" in error for error in errors))

        errors = module.validate_document_texts(
            config,
            acceptance,
            plan.replace("本计划不是固定任务清单", "本计划是固定任务清单", 1),
        )
        self.assertTrue(any("本计划不是固定任务清单" in error for error in errors))

    def test_plan_cannot_drop_a_due_requirement(self) -> None:
        module = load_program_hook()
        config = module.load_project_config("P1")
        acceptance_path = module.safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        plan_path = module.safe_repository_path(config["plan_path"], "plan_path")
        acceptance = module.read_text(acceptance_path)
        plan = module.read_text(plan_path)
        changed = plan.replace(
            "`P1-SCE-08`、`P1-SCE-10`",
            "`P1-SCE-10`",
            1,
        )
        errors = module.validate_document_texts(config, acceptance, changed)
        self.assertTrue(any("P1-SCE-08" in error for error in errors))

    def test_s1_vertical_slice_only_closes_address_series_related_scenarios(self) -> None:
        module = load_program_hook()
        config = module.load_project_config("P1")
        self.assertEqual(
            config["stage_due"]["S1"]["联合场景"],
            [
                "P1-SCE-01",
                "P1-SCE-03",
                "P1-SCE-10",
                "P1-SCE-13",
                "P1-SCE-14",
                "P1-SCE-15",
                "P1-SCE-17",
            ],
        )
        plan_path = module.safe_repository_path(config["plan_path"], "plan_path")
        s1_body = module.stage_body(config, module.read_text(plan_path), "S1")
        self.assertIsNotNone(s1_body)
        self.assertIn("不因 S1 的一条垂直切片提前宣告通过", s1_body)

    def test_page_coverage_product_semantics_cannot_drift(self) -> None:
        program = load_program_hook()
        p1_hook = load_p1_hook()
        config = program.load_project_config("P1")
        acceptance_path = program.safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        plan_path = program.safe_repository_path(config["plan_path"], "plan_path")
        acceptance = program.read_text(acceptance_path)
        plan = program.read_text(plan_path)

        self.assertEqual(
            p1_hook.validate_page_coverage_contract(config, acceptance, plan),
            [],
        )
        errors = p1_hook.validate_page_coverage_contract(
            config,
            acceptance.replace(
                "`IP` 默认表示 `IPv4 + IPv6`",
                "`IP` 默认表示 `IPv4`",
                1,
            ),
            plan,
        )
        self.assertTrue(any("IPv4 + IPv6" in error for error in errors))

        errors = p1_hook.validate_page_coverage_contract(
            config,
            acceptance.replace(
                "问题探针 Agent 不得同时充当最终判卷者",
                "问题探针 Agent 可以自行判卷",
                1,
            ),
            plan,
        )
        self.assertTrue(any("最终判卷者" in error for error in errors))

    def make_evidence_ref(
        self,
        root: Path,
        artifact_kind: str,
        evidence_kind: str,
        *,
        stage: str,
        run_id: str | None = None,
        captured_at: str = "2026-08-10T00:00:00Z",
        extra: dict | None = None,
    ) -> dict[str, str]:
        relative = f"raw/{artifact_kind}-{evidence_kind}.json"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "test_raw_evidence_v1",
            "artifact_kind": artifact_kind,
            "evidence_kind": evidence_kind,
            "candidate_id": "candidate-for-stage-test",
            "stage": stage,
            "run_id": run_id or f"run-{artifact_kind}",
            "captured_at": captured_at,
            "source": "deterministic-test-receipt",
        }
        if extra:
            payload.update(extra)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return {
            "kind": evidence_kind,
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def update_artifact_sha(self, root: Path, artifact: dict[str, str]) -> None:
        artifact["sha256"] = hashlib.sha256(
            (root / artifact["path"]).read_bytes()
        ).hexdigest()

    def make_stage_receipt(self, module, config, stage: str, root: Path) -> dict:
        component_names = {
            "frontend",
            "backend",
            "runtime",
            "semantic_planner",
            "model",
            "prompt",
            "schema",
            "capability_catalog",
            "policy",
            "tool_contracts",
            "operator_contracts",
            "oracle",
            "data_publication",
        }
        component_refs = [
            self.make_evidence_ref(
                root,
                "same_candidate_manifest",
                f"component_{component}",
                stage=stage,
                extra={"component": component, "identity": f"test-{component}-identity"},
            )
            for component in sorted(component_names)
        ] if stage == "S4" else []
        component_identities = {
            component: {
                "identity": f"test-{component}-identity",
                "evidence_kind": f"component_{component}",
                "sha256": next(
                    ref["sha256"]
                    for ref in component_refs
                    if ref["kind"] == f"component_{component}"
                ),
            }
            for component in component_names
        } if stage == "S4" else {}
        component_digest = hashlib.sha256(
            json.dumps(
                component_identities,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        explorer_cases = [
            {
                "case_id": f"probe-{index:03d}",
                "page_outcome_ids": [f"PCO-{index:02d}"],
                "expression_type": "generic",
                "persona": "network-operator",
                "conversation_seed": [],
                "question": f"测试页面用户结果 PCO-{index:02d}",
                "candidate_id": "candidate-for-stage-test",
                "event_identity": {"event_type": "country_outage"},
                "raw_agent_receipt_ref": f"raw_agent_receipts:probe-{index:03d}",
                "review_status": "candidate",
            }
            for index in range(1, 9)
        ]
        explorer_cases_digest = hashlib.sha256(
            json.dumps(explorer_cases, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        explorer_refs = [
            self.make_evidence_ref(
                root,
                "question_explorer_results",
                "raw_agent_receipts",
                stage=stage,
                run_id="question-explorer-run",
                extra={
                    "actor_id": "question-explorer-agent",
                    "cases_sha256": explorer_cases_digest,
                },
            )
        ] if stage == "S2" else []
        artifacts = []
        for kind in sorted(module.REQUIRED_ARTIFACT_KINDS[stage]):
            relative = f"{kind}.json"
            path = root / relative
            evidence_kinds = ["source_evidence"]
            if kind in {"product_semantic_truth", "independent_semantic_review"}:
                evidence_kinds = [
                    "reviewed_input",
                    "blind_truth",
                    "system_output",
                    "case_author_actor_receipt",
                    "reviewer_actor_receipt",
                ]
            elif kind == "question_explorer_results":
                evidence_kinds = []
            elif kind == "same_candidate_manifest":
                evidence_kinds = ["component_manifest"]
            elif kind == "browser_api_tool_evidence_state_trace":
                evidence_kinds = [
                    "browser_receipt",
                    "api_receipt",
                    "user_goal_plan",
                    "grounding_plan",
                    "tool_receipts",
                    "evidence_state",
                    "dialog_state_before",
                    "dialog_state_after",
                ]
            if kind == "same_candidate_manifest":
                evidence_refs = list(component_refs)
            elif kind == "question_explorer_results":
                evidence_refs = list(explorer_refs)
            else:
                evidence_refs = []
            for evidence_kind in evidence_kinds:
                captured_at = (
                    "2026-08-10T00:01:00Z"
                    if evidence_kind == "system_output"
                    else "2026-08-10T00:00:00Z"
                )
                run_id = f"run-{kind}"
                extra = None
                case_author_actor_id = (
                    "question-explorer-agent"
                    if stage == "S2"
                    else "human-case-author"
                )
                case_author_run_id = (
                    "question-explorer-run" if stage == "S2" else "case-author-run"
                )
                if evidence_kind == "reviewed_input":
                    run_id = case_author_run_id
                    extra = {
                        "actor_id": case_author_actor_id,
                        "candidate_identity_sha256": component_digest,
                    }
                    if stage == "S2":
                        extra.update(
                            {
                                "question_explorer_receipt_sha256": explorer_refs[0][
                                    "sha256"
                                ],
                                "question_explorer_cases_sha256": explorer_cases_digest,
                            }
                        )
                elif evidence_kind == "blind_truth":
                    reviewed_input_ref = next(
                        ref for ref in evidence_refs if ref["kind"] == "reviewed_input"
                    )
                    run_id = "semantic-reviewer-run"
                    extra = {
                        "actor_id": "semantic-reviewer-agent",
                        "reviewed_input_sha256": reviewed_input_ref["sha256"],
                        "candidate_identity_sha256": component_digest,
                        "truth_items": [
                            {
                                "case_id": case_id,
                                "expected_goals": ["address_series_change"],
                                "expected_entities": {"address_family": "both"},
                                "answerability": "supported",
                                "required_answer_points": ["IPv4 与 IPv6 分别回答"],
                                "forbidden_claims": ["不得推断恢复"],
                            }
                            for case_id in (
                                [case["case_id"] for case in explorer_cases]
                                if stage == "S2"
                                else ["test-item"]
                            )
                        ],
                    }
                elif evidence_kind == "system_output":
                    reviewed_input_ref = next(
                        ref for ref in evidence_refs if ref["kind"] == "reviewed_input"
                    )
                    run_id = "system-output-run"
                    extra = {
                        "reviewed_input_sha256": reviewed_input_ref["sha256"],
                        "candidate_identity_sha256": component_digest,
                    }
                elif evidence_kind == "case_author_actor_receipt":
                    run_id = case_author_run_id
                    extra = {
                        "actor_id": case_author_actor_id,
                        "denied_actions": ["mark_pass", "modify_implementation"],
                        "orchestrator_receipt_id": "orchestrator-case-author-001",
                    }
                elif evidence_kind == "reviewer_actor_receipt":
                    run_id = "semantic-reviewer-run"
                    extra = {
                        "actor_id": "semantic-reviewer-agent",
                        "denied_actions": [
                            "generate_probe_cases",
                            "modify_implementation",
                        ],
                        "orchestrator_receipt_id": "orchestrator-reviewer-001",
                    }
                elif evidence_kind == "component_manifest":
                    extra = {
                        "component_identities": component_identities,
                        "candidate_identity_sha256": component_digest,
                    }
                elif kind == "browser_api_tool_evidence_state_trace":
                    run_id = "journey-run-001"
                    extra = {
                        "journey_id": "journey-001",
                        "candidate_identity_sha256": component_digest,
                    }
                evidence_refs.append(
                    self.make_evidence_ref(
                        root,
                        kind,
                        evidence_kind,
                        stage=stage,
                        run_id=run_id,
                        captured_at=captured_at,
                        extra=extra,
                    )
                )
            evidence_hashes = {
                evidence["kind"]: evidence["sha256"] for evidence in evidence_refs
            }
            content = {
                "schema_version": "test_artifact_v1",
                "artifact_kind": kind,
                "stage": stage,
                "candidate_id": "candidate-for-stage-test",
                "status": "PASS",
                "evidence_refs": evidence_refs,
            }
            if kind in {"page_capability_outcome_map", "question_explorer_results"}:
                content["page_outcome_ids"] = list(module.PAGE_OUTCOME_IDS)
            if kind == "ip_question_execution_trace":
                content["questions"] = ["IP地址变化情况", "IP地址变化趋势"]
            if kind == "question_explorer_contract":
                content.update(
                    {
                        "question_explorer_actor_id": "question-explorer-agent",
                        "allowed_actions": ["generate_questions", "black_box_query"],
                        "denied_actions": ["write_truth", "mark_pass"],
                    }
                )
            if kind == "question_explorer_results":
                content.update(
                    {
                        "question_explorer_actor_id": "question-explorer-agent",
                        "question_explorer_run_id": "question-explorer-run",
                        "cases": explorer_cases,
                        "cases_sha256": explorer_cases_digest,
                        "raw_agent_receipts_sha256": explorer_refs[0]["sha256"],
                    }
                )
            if kind in {"product_semantic_truth", "independent_semantic_review"}:
                content.update(
                    {
                        "reviewer_role": "product_semantic_truth_reviewer",
                        "independent_from_question_explorer": True,
                        "verdict": "PASS",
                        "reviewed_items": [
                            {
                                "case_id": case_id,
                                "verdict": "PASS",
                                "semantic_diff": [],
                            }
                            for case_id in (
                                [case["case_id"] for case in explorer_cases]
                                if stage == "S2"
                                else ["test-item"]
                            )
                        ],
                        "case_author_actor_id": (
                            "question-explorer-agent"
                            if stage == "S2"
                            else "human-case-author"
                        ),
                        "reviewer_actor_id": "semantic-reviewer-agent",
                        "case_author_run_id": (
                            "question-explorer-run"
                            if stage == "S2"
                            else "case-author-run"
                        ),
                        "reviewer_run_id": "semantic-reviewer-run",
                        "blind_truth_created_at": "2026-08-10T00:00:00Z",
                        "system_output_revealed_at": "2026-08-10T00:01:00Z",
                        "review_completed_at": "2026-08-10T00:02:00Z",
                        "reviewed_input_sha256": evidence_hashes["reviewed_input"],
                        "blind_truth_sha256": evidence_hashes["blind_truth"],
                        "system_output_sha256": evidence_hashes["system_output"],
                        "case_author_actor_receipt_sha256": evidence_hashes[
                            "case_author_actor_receipt"
                        ],
                        "reviewer_actor_receipt_sha256": evidence_hashes[
                            "reviewer_actor_receipt"
                        ],
                        "candidate_identity_sha256": component_digest,
                    }
                )
                if stage == "S2":
                    content.update(
                        {
                            "question_explorer_receipt_sha256": explorer_refs[0][
                                "sha256"
                            ],
                            "question_explorer_cases_sha256": explorer_cases_digest,
                        }
                    )
            if kind == "same_candidate_manifest":
                content["component_identities"] = component_identities
                content["candidate_identity_sha256"] = component_digest
            if kind == "browser_api_tool_evidence_state_trace":
                content["journeys"] = [
                    {
                        "journey_id": "journey-001",
                        "run_id": "journey-run-001",
                        "candidate_id": "candidate-for-stage-test",
                        "candidate_identity_sha256": component_digest,
                        **{
                            f"{evidence_kind}_sha256": evidence_hashes[evidence_kind]
                            for evidence_kind in evidence_kinds
                        },
                    }
                ]
            if kind == "unclosed_unknowns":
                content.update({"unknowns": [], "blocking_count": 0})
            path.write_text(json.dumps(content), encoding="utf-8")
            artifacts.append(
                {
                    "kind": kind,
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        review_ref = next(
            artifact["path"]
            for artifact in artifacts
            if artifact["kind"]
            in {"product_semantic_truth", "independent_semantic_review"}
        )
        return {
            "schema_version": module.RECEIPT_SCHEMA_VERSION,
            "stage": stage,
            "task_spec_version": module.TASK_SPEC_VERSION,
            "plan_version": module.PLAN_VERSION,
            "candidate_id": "candidate-for-stage-test",
            "status": "PASS",
            "requirement_ids": sorted(module.flatten_due_requirements(config, stage)),
            "page_outcome_ids": sorted(module.REQUIRED_OUTCOMES_BY_STAGE[stage]),
            "artifacts": artifacts,
            "semantic_review": {
                "role_separated": True,
                "verdict": "PASS",
                "receipt_ref": review_ref,
            },
            "unresolved_blockers": [],
            "prohibited_claims": {
                "p2_complete": False,
                "rca_complete": False,
                "deployed": False,
                "production_verified": False,
            },
        }

    def test_stage_receipt_closes_identity_artifacts_reviewer_and_claims(self) -> None:
        program = load_program_hook()
        module = load_p1_hook()
        config = program.load_project_config("P1")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.make_stage_receipt(module, config, "S0", root)
            self.assertEqual(
                module.validate_stage_receipt(
                    config, "S0", receipt, artifact_root=root
                ),
                [],
            )

            mutations = []
            changed = deepcopy(receipt)
            changed["semantic_review"]["role_separated"] = False
            mutations.append((changed, "Reviewer"))
            changed = deepcopy(receipt)
            changed["page_outcome_ids"].remove("PCO-08")
            mutations.append((changed, "PCO-08"))
            changed = deepcopy(receipt)
            changed["artifacts"][0]["sha256"] = "0" * 64
            mutations.append((changed, "SHA-256"))
            changed = deepcopy(receipt)
            changed["prohibited_claims"]["p2_complete"] = True
            mutations.append((changed, "p2_complete"))
            changed = deepcopy(receipt)
            changed["unresolved_blockers"] = ["semantic mismatch"]
            mutations.append((changed, "未关闭阻断"))

            for changed, expected in mutations:
                with self.subTest(expected=expected):
                    errors = module.validate_stage_receipt(
                        config, "S0", changed, artifact_root=root
                    )
                    self.assertTrue(
                        any(expected in error for error in errors),
                        errors,
                    )

            changed = deepcopy(receipt)
            first = changed["artifacts"][0]
            artifact_path = root / first["path"]
            content = json.loads(artifact_path.read_text(encoding="utf-8"))
            content["candidate_id"] = "another-candidate"
            artifact_path.write_text(json.dumps(content), encoding="utf-8")
            first["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            errors = module.validate_stage_receipt(
                config, "S0", changed, artifact_root=root
            )
            self.assertTrue(any("candidate_id" in error for error in errors), errors)

    def test_evidence_and_reviewer_self_claims_cannot_bypass_stage_gate(self) -> None:
        program = load_program_hook()
        module = load_p1_hook()
        config = program.load_project_config("P1")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = self.make_stage_receipt(module, config, "S4", root)
            self.assertEqual(
                module.validate_stage_receipt(
                    config, "S4", receipt, artifact_root=root
                ),
                [],
            )

            review_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "independent_semantic_review"
            )
            review_path = root / review_entry["path"]
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["evidence_refs"] = ["claim:anything"]
            review_path.write_text(json.dumps(review), encoding="utf-8")
            self.update_artifact_sha(root, review_entry)
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("自报字符串" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S4", root)
            review_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "independent_semantic_review"
            )
            review_path = root / review_entry["path"]
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["reviewer_actor_id"] = review["case_author_actor_id"]
            review["blind_truth_created_at"] = review["system_output_revealed_at"]
            review_path.write_text(json.dumps(review), encoding="utf-8")
            self.update_artifact_sha(root, review_entry)
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("actor 不得相同" in error for error in errors), errors)
            self.assertTrue(any("先产生盲审真值" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S4", root)
            trace_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "browser_api_tool_evidence_state_trace"
            )
            trace_path = root / trace_entry["path"]
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            trace["journeys"][0]["candidate_id"] = "another-candidate"
            trace_path.write_text(json.dumps(trace), encoding="utf-8")
            self.update_artifact_sha(root, trace_entry)
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("阶段候选不一致" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S4", root)
            trace_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "browser_api_tool_evidence_state_trace"
            )
            trace_path = root / trace_entry["path"]
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            api_ref = next(
                ref for ref in trace["evidence_refs"] if ref["kind"] == "api_receipt"
            )
            api_path = root / api_ref["path"]
            api_receipt = json.loads(api_path.read_text(encoding="utf-8"))
            api_receipt["candidate_id"] = "other-candidate"
            api_path.write_text(json.dumps(api_receipt), encoding="utf-8")
            api_ref["sha256"] = hashlib.sha256(api_path.read_bytes()).hexdigest()
            trace["journeys"][0]["api_receipt_sha256"] = api_ref["sha256"]
            trace_path.write_text(json.dumps(trace), encoding="utf-8")
            self.update_artifact_sha(root, trace_entry)
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("原始回执 candidate_id 不一致" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S4", root)
            review_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "independent_semantic_review"
            )
            review_path = root / review_entry["path"]
            review = json.loads(review_path.read_text(encoding="utf-8"))
            blind_ref = next(
                ref for ref in review["evidence_refs"] if ref["kind"] == "blind_truth"
            )
            blind_path = root / blind_ref["path"]
            blind = json.loads(blind_path.read_text(encoding="utf-8"))
            blind["run_id"] = "case-author-run"
            blind_path.write_text(json.dumps(blind), encoding="utf-8")
            blind_ref["sha256"] = hashlib.sha256(blind_path.read_bytes()).hexdigest()
            review["blind_truth_sha256"] = blind_ref["sha256"]
            review_path.write_text(json.dumps(review), encoding="utf-8")
            self.update_artifact_sha(root, review_entry)
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("先验真值未绑定 Reviewer" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S4", root)
            review_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "independent_semantic_review"
            )
            review_path = root / review_entry["path"]
            review = json.loads(review_path.read_text(encoding="utf-8"))
            author_ref = next(
                ref
                for ref in review["evidence_refs"]
                if ref["kind"] == "case_author_actor_receipt"
            )
            reviewer_ref = next(
                ref
                for ref in review["evidence_refs"]
                if ref["kind"] == "reviewer_actor_receipt"
            )
            author_receipt = json.loads(
                (root / author_ref["path"]).read_text(encoding="utf-8")
            )
            reviewer_path = root / reviewer_ref["path"]
            reviewer_receipt = json.loads(reviewer_path.read_text(encoding="utf-8"))
            reviewer_receipt["orchestrator_receipt_id"] = author_receipt[
                "orchestrator_receipt_id"
            ]
            reviewer_path.write_text(json.dumps(reviewer_receipt), encoding="utf-8")
            reviewer_ref["sha256"] = hashlib.sha256(
                reviewer_path.read_bytes()
            ).hexdigest()
            review["reviewer_actor_receipt_sha256"] = reviewer_ref["sha256"]
            review_path.write_text(json.dumps(review), encoding="utf-8")
            self.update_artifact_sha(root, review_entry)
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("复用 orchestrator_receipt_id" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S2", root)
            explorer_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "question_explorer_results"
            )
            explorer_path = root / explorer_entry["path"]
            explorer = json.loads(explorer_path.read_text(encoding="utf-8"))
            raw_ref = explorer["evidence_refs"][0]
            raw_path = root / raw_ref["path"]
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            raw["actor_id"] = "semantic-reviewer-agent"
            raw["run_id"] = "semantic-reviewer-run"
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            raw_ref["sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            explorer["raw_agent_receipts_sha256"] = raw_ref["sha256"]
            explorer_path.write_text(json.dumps(explorer), encoding="utf-8")
            self.update_artifact_sha(root, explorer_entry)
            errors = module.validate_stage_receipt(
                config, "S2", receipt, artifact_root=root
            )
            self.assertTrue(any("原始探针回执未绑定探针 actor/run" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S4", root)
            manifest_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "same_candidate_manifest"
            )
            manifest_path = root / manifest_entry["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["component_identities"]["model"]["identity"] = (
                "claimed-other-model"
            )
            manifest_digest = hashlib.sha256(
                json.dumps(
                    manifest["component_identities"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            manifest["candidate_identity_sha256"] = manifest_digest
            component_manifest_ref = next(
                ref
                for ref in manifest["evidence_refs"]
                if ref["kind"] == "component_manifest"
            )
            component_manifest_path = root / component_manifest_ref["path"]
            component_manifest = json.loads(
                component_manifest_path.read_text(encoding="utf-8")
            )
            component_manifest["component_identities"] = manifest[
                "component_identities"
            ]
            component_manifest["candidate_identity_sha256"] = manifest_digest
            component_manifest_path.write_text(
                json.dumps(component_manifest), encoding="utf-8"
            )
            component_manifest_ref["sha256"] = hashlib.sha256(
                component_manifest_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.update_artifact_sha(root, manifest_entry)
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("组件 model 声明与原始回执不一致" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S4", root)
            review_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "independent_semantic_review"
            )
            receipt["artifacts"].append(deepcopy(review_entry))
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("kind 重复" in error for error in errors), errors)
            self.assertTrue(any("path 规范化后重复" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S2", root)
            explorer_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "question_explorer_results"
            )
            explorer_path = root / explorer_entry["path"]
            explorer = json.loads(explorer_path.read_text(encoding="utf-8"))
            explorer["cases"] = [{"case_id": "probe-001"}]
            explorer_path.write_text(json.dumps(explorer), encoding="utf-8")
            self.update_artifact_sha(root, explorer_entry)
            errors = module.validate_stage_receipt(
                config, "S2", receipt, artifact_root=root
            )
            self.assertTrue(any("缺少非空 question" in error for error in errors), errors)
            self.assertTrue(any("未实际覆盖声明的全部 PCO" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S4", root)
            review_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "independent_semantic_review"
            )
            review_path = root / review_entry["path"]
            review = json.loads(review_path.read_text(encoding="utf-8"))
            blind_ref = next(
                ref for ref in review["evidence_refs"] if ref["kind"] == "blind_truth"
            )
            blind_path = root / blind_ref["path"]
            blind = json.loads(blind_path.read_text(encoding="utf-8"))
            blind["truth_items"] = [{"case_id": "test-item"}]
            blind_path.write_text(json.dumps(blind), encoding="utf-8")
            blind_ref["sha256"] = hashlib.sha256(blind_path.read_bytes()).hexdigest()
            review["blind_truth_sha256"] = blind_ref["sha256"]
            review_path.write_text(json.dumps(review), encoding="utf-8")
            self.update_artifact_sha(root, review_entry)
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("expected_goals 必须是非空数组" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S4", root)
            review_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "independent_semantic_review"
            )
            review_path = root / review_entry["path"]
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["reviewed_items"][0].update(
                {"verdict": "FAIL", "semantic_diff": ["wrong fact"]}
            )
            review_path.write_text(json.dumps(review), encoding="utf-8")
            self.update_artifact_sha(root, review_entry)
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("不得上卷为 Reviewer 总体 PASS" in error for error in errors), errors)

            receipt = self.make_stage_receipt(module, config, "S4", root)
            unknowns_entry = next(
                item
                for item in receipt["artifacts"]
                if item["kind"] == "unclosed_unknowns"
            )
            unknowns_path = root / unknowns_entry["path"]
            unknowns = json.loads(unknowns_path.read_text(encoding="utf-8"))
            unknowns["unknowns"] = [
                {
                    "unknown_id": "U-1",
                    "subject": "unverified semantic case",
                    "blocking": True,
                    "next_validation": "rerun semantic review",
                    "owner": "semantic-reviewer-agent",
                }
            ]
            unknowns_path.write_text(json.dumps(unknowns), encoding="utf-8")
            self.update_artifact_sha(root, unknowns_entry)
            errors = module.validate_stage_receipt(
                config, "S4", receipt, artifact_root=root
            )
            self.assertTrue(any("blocking_count 与 unknowns 计算值不一致" in error for error in errors), errors)

    def test_goal_fidelity_and_grounding_safety_use_separate_gates(self) -> None:
        module = load_program_hook()
        config = module.load_project_config("P1")
        acceptance_path = module.safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        acceptance = module.read_text(acceptance_path)
        self.assertIn("`UserGoalPlan` 目标保真率不低于 95%", acceptance)
        self.assertIn("不能通过虚构额外目标抬高保真率", acceptance)
        self.assertIn("`GroundingPlan` 合法性是 100% 硬门", acceptance)
        self.assertIn("任何非法节点到达执行器", acceptance)
        self.assertNotIn("Semantic Plan 正确率", acceptance)

    def test_invalid_stage_is_rejected(self) -> None:
        result = self.run_hook("S5")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
