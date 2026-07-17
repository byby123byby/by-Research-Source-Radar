#!/usr/bin/env python3

import copy
import json
import signal
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import retrieval_ab_benchmark as ab


SKILL_ROOT = Path(__file__).resolve().parents[1]
TASK_FILE = SKILL_ROOT / "references" / "supervision-retrieval-ab-tasks.json"


def benchmark_tasks():
    return ab.contract.load_json(TASK_FILE)


def small_tasks():
    data = benchmark_tasks()
    selected = copy.deepcopy(data["tasks"][:2])
    ids = [item["id"] for item in selected]
    data["tasks"] = selected
    data["task_sets"] = {"pilot": ids, "main": ids}
    return data


def answer(source_suffix="baseline", relevance_text="useful"):
    return {
        "summary": f"Summary for {source_suffix}",
        "sources": [
            {
                "rank": 1,
                "title": f"Source {source_suffix}",
                "url": f"https://github.com/example/{source_suffix}",
                "source_type": "github_repository",
                "relevance_rationale": relevance_text,
                "mechanism": "bounded mechanism",
                "limitations": "benchmark fixture",
            }
        ],
        "queries": [{"query": f"query {source_suffix}", "source": "GitHub"}],
        "gaps": ["fixture gap"],
        "constraint_notes": ["fixture constraint"],
    }


def response_for(trial, result, *, tokens=100, wall=60.0, status="completed"):
    return {
        "schema_version": ab.SCHEMA_VERSION,
        "trial_id": trial["trial_id"],
        "task_id": trial["task_id"],
        "condition": trial["condition"],
        "repetition": trial["repetition"],
        "execution": {
            "status": status,
            "model": "test-model",
            "started_at": "2026-07-16T00:00:00+00:00",
            "ended_at": "2026-07-16T00:01:00+00:00",
            "wall_seconds": wall,
            "input_tokens": tokens,
            "output_tokens": tokens,
            "empty_query_attempts": 0,
            "executor_exit_code": 0 if status == "completed" else 1,
            "event_log_sha256": "0" * 64,
            "error": "" if status == "completed" else "fixture failure",
        },
        "answer": result if status == "completed" else None,
    }


class RetrievalABBenchmarkTests(unittest.TestCase):
    def prepared(self, *, runs=1, seed=7):
        return ab.prepare_manifest(
            small_tasks(),
            phase="pilot",
            runs=runs,
            seed=seed,
            model="test-model",
            reasoning_effort="high",
            max_wall_seconds=60,
            max_sources=10,
        )

    def test_real_task_set_is_valid_and_has_frozen_sizes(self):
        data = benchmark_tasks()
        self.assertEqual([], ab.validate_tasks(data))
        self.assertEqual(30, len(data["tasks"]))
        self.assertEqual(8, len(data["task_sets"]["pilot"]))
        self.assertEqual(30, len(data["task_sets"]["main"]))
        self.assertEqual(list(ab.PRIMARY_METRICS), data["primary_metrics"])

    def test_task_validation_rejects_schema_category_source_and_set_errors(self):
        data = small_tasks()
        data["unexpected"] = True
        data["tasks"][0]["category"] = "marketing"
        data["tasks"][0]["source_types"] = ["social_hype"]
        data["task_sets"]["pilot"] = ["TASK-999"]
        errors = ab.validate_tasks(data)
        self.assertTrue(any("fields must be exactly" in item for item in errors))
        self.assertTrue(any("category is unsupported" in item for item in errors))
        self.assertTrue(any("source_types contains unsupported" in item for item in errors))
        self.assertTrue(any("known task IDs" in item for item in errors))

    def test_unsupported_task_and_manifest_versions_are_rejected(self):
        tasks = small_tasks()
        tasks["schema_version"] = 999
        self.assertTrue(any("schema_version" in item for item in ab.validate_tasks(tasks)))
        manifest, _ = self.prepared()
        manifest["schema_version"] = 999
        self.assertTrue(any("unsupported" in item for item in ab.validate_manifest(manifest)))

    def test_prepare_is_deterministic_balanced_and_prompt_identical_between_conditions(self):
        first, prompts_first = self.prepared(runs=2, seed=11)
        second, prompts_second = self.prepared(runs=2, seed=11)
        first_without_time = dict(first)
        second_without_time = dict(second)
        first_without_time.pop("created_at")
        second_without_time.pop("created_at")
        self.assertEqual(first_without_time, second_without_time)
        self.assertEqual(prompts_first, prompts_second)
        trials = first["trials"]
        self.assertEqual(8, len(trials))
        self.assertEqual(4, sum(item["condition"] == "baseline" for item in trials))
        self.assertEqual(4, sum(item["condition"] == "skill" for item in trials))
        by_task = {}
        for trial in trials:
            by_task.setdefault((trial["task_id"], trial["repetition"]), set()).add(trial["prompt_sha256"])
        self.assertTrue(all(len(values) == 1 for values in by_task.values()))

    def test_prepared_run_refuses_to_overwrite_and_manifest_rejects_traversal(self):
        manifest, prompts = self.prepared()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            ab.write_prepared_run(output, manifest, prompts)
            with self.assertRaises(FileExistsError):
                ab.write_prepared_run(output, manifest, prompts)
            mutated = copy.deepcopy(manifest)
            mutated["trials"][0]["prompt_path"] = "../secret"
            self.assertTrue(any("contained relative path" in item for item in ab.validate_manifest(mutated)))

    def test_manifest_rejects_unpaired_or_changed_condition_prompts(self):
        manifest, _ = self.prepared()
        mutated = copy.deepcopy(manifest)
        mutated["trials"][0]["prompt_sha256"] = "f" * 64
        self.assertTrue(any("paired prompts differ" in item for item in ab.validate_manifest(mutated)))
        mutated = copy.deepcopy(manifest)
        mutated["conditions"]["baseline"]["other_user_skills_present"] = True
        self.assertTrue(any("isolation design" in item for item in ab.validate_manifest(mutated)))

    def test_answer_validation_rejects_duplicate_noncontiguous_ranks_and_extra_fields(self):
        value = answer()
        duplicate = copy.deepcopy(value["sources"][0])
        duplicate["title"] = "Second"
        value["sources"].append(duplicate)
        value["extra"] = "not allowed"
        errors = ab.validate_answer(value)
        self.assertTrue(any("fields must be exactly" in item for item in errors))
        self.assertTrue(any("duplicate source rank" in item for item in errors))
        self.assertTrue(any("contiguous" in item for item in errors))

    def test_response_must_match_manifest_and_failed_response_cannot_contain_answer(self):
        manifest, _ = self.prepared()
        trial = manifest["trials"][0]
        value = response_for(trial, answer())
        value["condition"] = "skill" if trial["condition"] == "baseline" else "baseline"
        self.assertTrue(any("condition does not match" in item for item in ab.validate_response(value, manifest)))
        failed = response_for(trial, answer(), status="failed")
        failed["answer"] = answer()
        self.assertTrue(any("failed response must use null" in item for item in ab.validate_response(failed, manifest)))

    def test_response_validator_accepts_legacy_execution_without_empty_query_metric(self):
        manifest, _ = self.prepared()
        trial = manifest["trials"][0]
        value = response_for(trial, answer())
        value["execution"].pop("empty_query_attempts")
        self.assertEqual([], ab.validate_response(value, manifest))

    def test_empty_query_metric_ignores_started_placeholders_and_counts_completed_searches(self):
        events = [
            {
                "type": "item.started",
                "item": {"type": "web_search", "query": "", "action": {"type": "other"}},
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "web_search",
                    "query": "valid query",
                    "action": {"type": "search", "queries": ["valid query"]},
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "web_search",
                    "query": "",
                    "action": {"type": "search", "queries": []},
                },
            },
        ]
        payload = "\n".join(json.dumps(event) for event in events)
        self.assertEqual(1, ab.count_empty_query_attempts(payload))

    def test_response_rejects_timestamp_hash_and_completion_inconsistency(self):
        manifest, _ = self.prepared()
        trial = manifest["trials"][0]
        value = response_for(trial, answer())
        value["execution"]["started_at"] = "2026-07-16T00:02:00+00:00"
        value["execution"]["event_log_sha256"] = "not-a-hash"
        value["execution"]["executor_exit_code"] = 1
        errors = ab.validate_response(value, manifest)
        self.assertTrue(any("must not precede" in item for item in errors))
        self.assertTrue(any("event_log_sha256" in item for item in errors))
        self.assertTrue(any("exit_code 0" in item for item in errors))

    def test_source_normalization_deduplicates_github_doi_and_arxiv_forms(self):
        self.assertEqual(
            ab.normalized_source_key("https://github.com/Example/Repo/tree/main", "x"),
            ab.normalized_source_key("http://www.github.com/example/repo.git", "y"),
        )
        self.assertEqual(
            "doi:10.1000/example",
            ab.normalized_source_key("https://doi.org/10.1000/Example?x=1", "x"),
        )
        self.assertEqual(
            "arxiv:2401.00001",
            ab.normalized_source_key("https://arxiv.org/pdf/2401.00001.pdf", "x"),
        )
        self.assertEqual(
            ab.normalized_source_key(
                "https://doi.org/10.1145/3411764.3445186",
                "Screen Recognition: Creating Accessibility Metadata for Mobile Applications from Pixels",
                "paper",
            ),
            ab.normalized_source_key(
                "https://docs-assets.developer.apple.com/ml-research/papers/screen-recognition-chi-2021.pdf",
                "Screen Recognition: Creating Accessibility Metadata for Mobile Applications from Pixels",
                "paper",
            ),
        )

    def test_blind_pool_removes_condition_and_deduplicates_sources(self):
        manifest, _ = self.prepared()
        task_id = manifest["trials"][0]["task_id"]
        matching = [item for item in manifest["trials"] if item["task_id"] == task_id]
        responses = [response_for(item, answer("shared")) for item in matching]
        pool = ab.build_blind_pool(small_tasks(), manifest, responses)
        self.assertEqual(1, len(pool["items"]))
        serialized = json.dumps(pool)
        self.assertNotIn('"condition"', serialized)
        self.assertNotIn('"repetition"', serialized)
        expected_task = next(
            item for item in small_tasks()["tasks"] if item["id"] == pool["items"][0]["task_id"]
        )
        self.assertEqual(expected_task["constraints"], pool["items"][0]["task_constraints"])
        self.assertTrue(pool["items"][0]["evaluation_focus"])
        self.assertIsNone(pool["items"][0]["relevance"])

    def test_judgments_require_complete_human_labels_and_manifest_binding(self):
        manifest, _ = self.prepared()
        responses = [
            response_for(trial, answer(f"{trial['condition']}-{trial['task_id']}"))
            for trial in manifest["trials"]
        ]
        pool = ab.build_blind_pool(small_tasks(), manifest, responses)
        errors = ab.validate_judgments(pool, ab.canonical_hash(manifest))
        self.assertTrue(any("relevance must be" in item for item in errors))
        self.assertTrue(any("identity_valid must be judged" in item for item in errors))
        pool["manifest_sha256"] = "wrong"
        self.assertTrue(any("do not belong" in item for item in ab.validate_judgments(pool, ab.canonical_hash(manifest))))

    def test_score_rejects_missing_or_invented_judgment_items(self):
        manifest, _ = self.prepared()
        responses = [
            response_for(trial, answer(f"{trial['condition']}-{trial['task_id']}"))
            for trial in manifest["trials"]
        ]
        pool = ab.build_blind_pool(small_tasks(), manifest, responses)
        for item in pool["items"]:
            item["relevance"] = 1
            item["identity_valid"] = "valid"
            item["constraint_fit"] = 1
        pool["items"].pop()
        with self.assertRaisesRegex(ValueError, "exact pooled"):
            ab.score_benchmark(manifest, responses, pool, iterations=10, seed=1)

    def test_score_reports_positive_skill_difference_on_controlled_fixture(self):
        manifest, _ = self.prepared(runs=2)
        responses = []
        for trial in manifest["trials"]:
            suffix = f"{trial['condition']}-{trial['task_id']}"
            responses.append(response_for(trial, answer(suffix), tokens=100, wall=60.0))
        pool = ab.build_blind_pool(small_tasks(), manifest, responses)
        for item in pool["items"]:
            is_skill = "/skill-" in item["url"]
            item["relevance"] = 2 if is_skill else 1
            item["identity_valid"] = "valid"
            item["constraint_fit"] = 2 if is_skill else 1
        metrics = ab.score_benchmark(manifest, responses, pool, iterations=500, seed=3)
        self.assertEqual(len(manifest["trials"]), metrics["completed_response_count"])
        self.assertGreater(metrics["paired_task_bootstrap"]["ndcg_at_10"]["mean_difference"], 0)
        self.assertGreater(
            metrics["condition_summary"]["skill"]["valid_high_relevance_sources"],
            metrics["condition_summary"]["baseline"]["valid_high_relevance_sources"],
        )
        self.assertIn("frozen pooled-judgment benchmark", metrics["claim_boundary"])

    def test_score_rejects_missing_responses(self):
        manifest, _ = self.prepared()
        response = response_for(manifest["trials"][0], answer())
        pool = ab.build_blind_pool(small_tasks(), manifest, [response])
        for item in pool["items"]:
            item["relevance"] = 1
            item["identity_valid"] = "valid"
            item["constraint_fit"] = 1
        with self.assertRaisesRegex(ValueError, "missing responses"):
            ab.score_benchmark(manifest, [response], pool, iterations=10, seed=1)

    def test_irrelevant_or_invalid_sources_do_not_count_as_direct_fit(self):
        manifest, _ = self.prepared()
        trial = manifest["trials"][0]
        response = response_for(trial, answer("direct-fit"))
        key = ab.normalized_source_key(
            response["answer"]["sources"][0]["url"],
            response["answer"]["sources"][0]["title"],
            response["answer"]["sources"][0]["source_type"],
        )
        judgment = {
            (trial["task_id"], key): {
                "relevance": 0,
                "identity_valid": "valid",
                "constraint_fit": 2,
            }
        }
        self.assertEqual(0.0, ab.trial_metrics(response, judgment)["direct_fit_sources"])
        judgment[(trial["task_id"], key)]["relevance"] = 1
        judgment[(trial["task_id"], key)]["identity_valid"] = "invalid"
        self.assertEqual(0.0, ab.trial_metrics(response, judgment)["direct_fit_sources"])

    def test_event_usage_uses_largest_recorded_values_and_ignores_noise(self):
        payload = "\n".join(
            [
                "not json",
                json.dumps({"usage": {"input_tokens": 10, "output_tokens": 2}}),
                json.dumps({"nested": [{"input_tokens": 25}, {"output_tokens": 8}]}),
            ]
        )
        self.assertEqual((25, 8), ab.parse_event_usage(payload))

    def test_cli_prepare_pool_and_score_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks_path = root / "tasks.json"
            tasks_path.write_text(json.dumps(small_tasks()), encoding="utf-8")
            run_dir = root / "run"
            prepare_args = Namespace(
                tasks=str(tasks_path),
                output_dir=str(run_dir),
                phase="pilot",
                runs=1,
                seed=19,
                model="test-model",
                reasoning_effort="high",
                max_wall_seconds=60,
                max_sources=10,
            )
            self.assertEqual(0, ab.command_prepare(prepare_args))
            manifest = ab.load_valid_manifest(run_dir / "trial_manifest.json")
            for trial in manifest["trials"]:
                result = response_for(trial, answer(f"{trial['condition']}-{trial['task_id']}"))
                ab.contract.write_json_atomic(run_dir / trial["response_path"], result)

            judgments_path = root / "judgments.json"
            self.assertEqual(
                0,
                ab.command_pool(
                    Namespace(
                        run_dir=str(run_dir),
                        tasks=str(tasks_path),
                        output=str(judgments_path),
                    )
                ),
            )
            judgments = ab.contract.load_json(judgments_path)
            for item in judgments["items"]:
                item["relevance"] = 1
                item["identity_valid"] = "valid"
                item["constraint_fit"] = 1
            ab.contract.write_json_atomic(judgments_path, judgments)
            metrics_path = root / "metrics.json"
            report_path = root / "report.md"
            self.assertEqual(
                0,
                ab.command_score(
                    Namespace(
                        run_dir=str(run_dir),
                        judgments=str(judgments_path),
                        output=str(metrics_path),
                        report=str(report_path),
                        bootstrap_iterations=100,
                        seed=19,
                    )
                ),
            )
            self.assertTrue(metrics_path.is_file())
            self.assertIn("Retrieval Skill A/B Report", report_path.read_text(encoding="utf-8"))

    def test_load_responses_rejects_unrecognized_files(self):
        manifest, prompts = self.prepared()
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            ab.write_prepared_run(run_dir, manifest, prompts)
            (run_dir / "responses" / "unexpected.json").write_text("{}", encoding="utf-8")
            responses, errors = ab.load_responses(run_dir / "responses", manifest)
            self.assertEqual([], responses)
            self.assertTrue(any("unrecognized response file" in item for item in errors))

    def test_run_codex_trial_uses_ephemeral_skill_isolation_and_records_usage(self):
        manifest, prompts = self.prepared()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            ab.write_prepared_run(run_dir, manifest, prompts)
            source_home = root / "source-home"
            source_home.mkdir()
            (source_home / "auth.json").write_text("{}", encoding="utf-8")
            target_skill = root / "target-skill"
            target_skill.mkdir()
            (target_skill / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n", encoding="utf-8")
            codex = root / "codex"
            codex.write_text("binary placeholder", encoding="utf-8")
            codex.chmod(0o700)
            observed = []
            commands = []

            class FakeProcess:
                def __init__(self, command, kwargs):
                    self.command = command
                    self.kwargs = kwargs
                    self.pid = 12345
                    self.returncode = 0

                def communicate(self, input=None, timeout=None):
                    isolated_home = Path(self.kwargs["env"]["CODEX_HOME"])
                    condition_has_skill = any((isolated_home / "skills").iterdir())
                    observed.append(condition_has_skill)
                    output_index = self.command.index("--output-last-message") + 1
                    Path(self.command[output_index]).write_text(json.dumps(answer("runner")), encoding="utf-8")
                    events = json.dumps({"usage": {"input_tokens": 120, "output_tokens": 30}})
                    return events, ""

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            def fake_popen(command, **kwargs):
                commands.append(command)
                return FakeProcess(command, kwargs)

            trials = sorted(manifest["trials"], key=lambda item: item["condition"])
            with mock.patch.object(ab.subprocess, "Popen", side_effect=fake_popen):
                for trial in trials:
                    result = ab.run_codex_trial(
                        codex=codex,
                        source_codex_home=source_home,
                        target_skill=target_skill,
                        run_dir=run_dir,
                        manifest=manifest,
                        trial=trial,
                    )
                    self.assertEqual("completed", result["execution"]["status"])
                    self.assertEqual(120, result["execution"]["input_tokens"])
                    self.assertEqual(30, result["execution"]["output_tokens"])
            self.assertIn(False, observed)
            self.assertIn(True, observed)
            self.assertTrue(any("suppress_unstable_features_warning=true" in command for command in commands))

    def test_run_codex_trial_records_timeout_as_failed_response(self):
        manifest, prompts = self.prepared()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            ab.write_prepared_run(run_dir, manifest, prompts)
            source_home = root / "source-home"
            source_home.mkdir()
            (source_home / "auth.json").write_text("{}", encoding="utf-8")
            target_skill = root / "target-skill"
            target_skill.mkdir()
            (target_skill / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n", encoding="utf-8")
            codex = root / "codex"
            codex.write_text("binary placeholder", encoding="utf-8")
            codex.chmod(0o700)
            class TimeoutProcess:
                def __init__(self):
                    self.pid = 12345
                    self.returncode = None
                    self.calls = 0

                def communicate(self, input=None, timeout=None):
                    self.calls += 1
                    if self.calls == 1:
                        raise subprocess.TimeoutExpired([str(codex)], 1, output="event", stderr="slow")
                    return "", ""

                def poll(self):
                    return None

                def wait(self, timeout=None):
                    self.returncode = -signal.SIGTERM
                    return self.returncode

            with mock.patch.object(ab.subprocess, "Popen", return_value=TimeoutProcess()), mock.patch.object(
                ab.os, "killpg"
            ) as killpg:
                result = ab.run_codex_trial(
                    codex=codex,
                    source_codex_home=source_home,
                    target_skill=target_skill,
                    run_dir=run_dir,
                    manifest=manifest,
                    trial=manifest["trials"][0],
                )
            self.assertEqual("failed", result["execution"]["status"])
            self.assertEqual(124, result["execution"]["executor_exit_code"])
            self.assertIn("hard timeout", result["execution"]["error"])
            killpg.assert_called_once()

    def test_terminate_process_group_cleans_descendants_after_parent_exit(self):
        class ExitedParent:
            pid = 54321

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        with mock.patch.object(ab.os, "killpg") as killpg:
            ab._terminate_process_group(ExitedParent())
        killpg.assert_called_once_with(54321, signal.SIGTERM)

    def test_command_validate_tasks_reports_pass_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.json"
            invalid = root / "invalid.json"
            valid.write_text(json.dumps(small_tasks()), encoding="utf-8")
            malformed = small_tasks()
            malformed["schema_version"] = 77
            invalid.write_text(json.dumps(malformed), encoding="utf-8")
            self.assertEqual(0, ab.command_validate_tasks(Namespace(tasks=str(valid))))
            self.assertEqual(1, ab.command_validate_tasks(Namespace(tasks=str(invalid))))

    def test_live_runner_requires_regular_auth_and_explicit_confirmation(self):
        args = mock.Mock(confirm_live_run=False)
        with self.assertRaisesRegex(ValueError, "confirm-live-run"):
            ab.command_run(args)
        manifest, prompts = self.prepared()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run"
            ab.write_prepared_run(run_dir, manifest, prompts)
            source_home = root / "source"
            source_home.mkdir()
            target_skill = root / "skill"
            target_skill.mkdir()
            codex = root / "codex"
            codex.write_text("x", encoding="utf-8")
            codex.chmod(0o700)
            with self.assertRaisesRegex(ValueError, "auth.json"):
                ab.run_codex_trial(
                    codex=codex,
                    source_codex_home=source_home,
                    target_skill=target_skill,
                    run_dir=run_dir,
                    manifest=manifest,
                    trial=manifest["trials"][0],
                )


if __name__ == "__main__":
    unittest.main()
