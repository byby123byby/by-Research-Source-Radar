#!/usr/bin/env python3

import argparse
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest import mock

import audit_release as audit

SKILL_ROOT = Path(__file__).resolve().parents[1]


class AuditReleaseTests(unittest.TestCase):
    def audit_args(self, root, **overrides):
        values = {
            "root": str(root),
            "manifest": audit.DEFAULT_MANIFEST_NAME,
            "timeout": 30.0,
            "strict_tools": False,
            "reset_history": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def minimal_audit_root(self, root, *, failing=False):
        scripts = root / "scripts"
        scripts.mkdir()
        body = (
            "import unittest\n"
            "class Smoke(unittest.TestCase):\n"
            f"    def test_smoke(self): self.assertEqual(1, {2 if failing else 1})\n"
            "    def test_failure_path(self): self.assertNotEqual(1, 2)\n"
        )
        (scripts / "test_smoke.py").write_text(body, encoding="utf-8")
        (root / "SKILL.md").write_text(
            "---\nname: smoke\ndescription: test\n---\n"
            "REQ-SMOKE\npublic claim\ndata representation\ntrigger\nbehavior\noutput\n"
            "migration compatibility\ndocumentation\n",
            encoding="utf-8",
        )
        def locator(marker):
            return {"path": "SKILL.md", "marker": marker}
        matrix = {
            "schema_version": audit.COMPLETENESS_SCHEMA_VERSION,
            "artifact": "research-discovery-and-translation-audit",
            "requirements": [{"id": "REQ-SMOKE", "locator": locator("REQ-SMOKE")}],
            "capabilities": [{
                "id": "smoke",
                "requirement_ids": ["REQ-SMOKE"],
                "public_claims": [locator("public claim")],
                "data_representation": [locator("data representation")],
                "triggers": [locator("trigger")],
                "behaviors": [locator("behavior")],
                "outputs": [locator("output")],
                "positive_tests": [{"path": "scripts/test_smoke.py", "marker": "def test_smoke("}],
                "negative_tests": [{"path": "scripts/test_smoke.py", "marker": "def test_failure_path("}],
                "migration_compatibility": [locator("migration compatibility")],
                "documentation": [locator("documentation")],
                "residual_boundaries": ["Minimal test fixture does not cover external systems."],
            }],
        }
        (root / audit.DEFAULT_COMPLETENESS_NAME).write_text(
            json.dumps(matrix, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_artifact_inventory_excludes_manifest_and_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("skill\n", encoding="utf-8")
            manifest = root / audit.DEFAULT_MANIFEST_NAME
            manifest.write_text("{}\n", encoding="utf-8")
            cache = root / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "ignored.pyc").write_bytes(b"ignored")
            git = root / ".git" / "objects"
            git.mkdir(parents=True)
            (git / "ignored").write_bytes(b"ignored")
            artifact = audit.collect_artifact(root, manifest)
            self.assertEqual(1, artifact["file_count"])
            self.assertEqual("SKILL.md", artifact["files"][0]["path"])

    def test_artifact_hash_changes_when_a_scoped_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "SKILL.md"
            source.write_text("before\n", encoding="utf-8")
            first = audit.collect_artifact(root, root / audit.DEFAULT_MANIFEST_NAME)
            source.write_text("after\n", encoding="utf-8")
            second = audit.collect_artifact(root, root / audit.DEFAULT_MANIFEST_NAME)
            self.assertNotEqual(first["artifact_sha256"], second["artifact_sha256"])

    def test_artifact_inventory_rejects_symbolic_links(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.txt"
            target.write_text("target\n", encoding="utf-8")
            link = root / "link.txt"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "symbolic links"):
                audit.collect_artifact(root, root / audit.DEFAULT_MANIFEST_NAME)

    def test_two_unchanged_clean_rounds_are_required(self):
        self.assertEqual(1, audit.next_clean_streak(None, "profile", clean=True))
        previous = {"profile_sha256": "profile", "clean_streak": 1, "result": "CLEAN_ROUND_1"}
        self.assertEqual(2, audit.next_clean_streak(previous, "profile", clean=True))
        converged = {"profile_sha256": "profile", "clean_streak": 2, "result": "PASS_CONVERGED"}
        self.assertEqual(2, audit.next_clean_streak(converged, "profile", clean=True))

    def test_artifact_or_profile_change_resets_clean_streak(self):
        previous = {"profile_sha256": "old", "clean_streak": 2, "result": "PASS_CONVERGED"}
        self.assertEqual(1, audit.next_clean_streak(previous, "new", clean=True))
        self.assertEqual(0, audit.next_clean_streak(previous, "old", clean=False))

    def test_strict_mode_requires_missing_static_analysis_tools(self):
        tools = {name: None for name in audit.OPTIONAL_TOOLS}
        relaxed = audit.build_check_specs(Path("/tmp/example"), strict_tools=False, tools=tools)
        strict = audit.build_check_specs(Path("/tmp/example"), strict_tools=True, tools=tools)
        self.assertFalse(next(item for item in relaxed if item.check_id == "ruff").required)
        self.assertTrue(next(item for item in strict if item.check_id == "ruff").required)

    def test_missing_optional_tool_is_recorded_not_run(self):
        spec = audit.CheckSpec("ruff", ("python", "-m", "ruff"), False, "static-analysis")
        result = audit.run_check(
            spec,
            root=Path.cwd(),
            timeout=1.0,
            tools={name: None for name in audit.OPTIONAL_TOOLS},
        )
        self.assertEqual("not_run", result["status"])

    def test_timeout_parser_rejects_nonpositive_and_nonfinite_values(self):
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                audit.positive_timeout(value)

    def test_audit_matrix_declares_convergence_and_limitations(self):
        areas = {item["area"] for item in audit.audit_matrix()}
        self.assertIn("convergence", areas)
        self.assertIn("static-analysis", areas)
        self.assertIn("source-to-outcome-completeness", areas)
        self.assertIn("retrieval-effectiveness-framework", areas)
        self.assertTrue(any("discovery-contract" in item["evidence"] for item in audit.audit_matrix()))

    def test_current_release_completeness_matrix_passes(self):
        result = audit.validate_release_completeness(SKILL_ROOT)
        self.assertEqual("passed", result["status"], result["errors"])
        self.assertEqual(9, result["requirements"])
        self.assertEqual(9, result["capabilities"])

    def test_completeness_matrix_rejects_missing_field_and_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.minimal_audit_root(root)
            matrix_path = root / audit.DEFAULT_COMPLETENESS_NAME
            data = audit.contract.load_json(matrix_path)
            del data["capabilities"][0]["outputs"]
            data["capabilities"][0]["documentation"][0]["marker"] = "missing marker"
            matrix_path.write_text(json.dumps(data), encoding="utf-8")
            result = audit.validate_release_completeness(root)
            self.assertEqual("failed", result["status"])
            self.assertTrue(any("fields do not match" in item for item in result["errors"]))
            self.assertTrue(any("marker was not found" in item for item in result["errors"]))

    def test_completeness_matrix_rejects_uncovered_requirement_and_fake_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.minimal_audit_root(root)
            matrix_path = root / audit.DEFAULT_COMPLETENESS_NAME
            data = audit.contract.load_json(matrix_path)
            data["requirements"].append({
                "id": "REQ-UNUSED",
                "locator": {"path": "SKILL.md", "marker": "REQ-SMOKE"},
            })
            data["capabilities"][0]["positive_tests"][0] = {
                "path": "SKILL.md",
                "marker": "public claim",
            }
            matrix_path.write_text(json.dumps(data), encoding="utf-8")
            result = audit.validate_release_completeness(root)
            self.assertEqual("failed", result["status"])
            self.assertTrue(any("REQ-UNUSED" in item for item in result["errors"]))
            self.assertTrue(any("must reference a test_*.py" in item for item in result["errors"]))

    def test_completeness_matrix_requires_distinct_positive_and_negative_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.minimal_audit_root(root)
            matrix_path = root / audit.DEFAULT_COMPLETENESS_NAME
            data = audit.contract.load_json(matrix_path)
            data["capabilities"][0]["negative_tests"] = list(data["capabilities"][0]["positive_tests"])
            matrix_path.write_text(json.dumps(data), encoding="utf-8")
            result = audit.validate_release_completeness(root)
            self.assertEqual("failed", result["status"])
            self.assertTrue(any("distinct positive and negative" in item for item in result["errors"]))

    def test_completeness_matrix_rejects_non_installable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.minimal_audit_root(root)
            (root / "README.md").write_text("repository-only claim\n", encoding="utf-8")
            matrix_path = root / audit.DEFAULT_COMPLETENESS_NAME
            data = audit.contract.load_json(matrix_path)
            data["capabilities"][0]["public_claims"][0] = {
                "path": "README.md",
                "marker": "repository-only claim",
            }
            matrix_path.write_text(json.dumps(data), encoding="utf-8")
            result = audit.validate_release_completeness(root)
            self.assertEqual("failed", result["status"])
            self.assertTrue(any("installed Skill package entry" in item for item in result["errors"]))

    def test_completeness_validator_survives_systematic_json_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.minimal_audit_root(root)
            matrix_path = root / audit.DEFAULT_COMPLETENESS_NAME
            base = audit.contract.load_json(matrix_path)
            replacements = (None, True, 0, "", [], {}, [None, {}])

            def paths(value, prefix=()):
                result = [prefix]
                if isinstance(value, dict):
                    for key, child in value.items():
                        result.extend(paths(child, prefix + (key,)))
                elif isinstance(value, list):
                    for index, child in enumerate(value):
                        result.extend(paths(child, prefix + (index,)))
                return result

            def replace(root_value, path, replacement):
                if not path:
                    return replacement
                parent = root_value
                for step in path[:-1]:
                    parent = parent[step]
                parent[path[-1]] = replacement
                return root_value

            for path in paths(base):
                for replacement in replacements:
                    mutated = replace(copy.deepcopy(base), path, copy.deepcopy(replacement))
                    matrix_path.write_text(json.dumps(mutated), encoding="utf-8")
                    try:
                        result = audit.validate_release_completeness(root)
                    except Exception as exc:
                        self.fail(f"completeness validator crashed at {path!r} with {replacement!r}: {exc}")
                    self.assertIn(result["status"], {"passed", "failed"})

    def test_reset_history_recovers_from_malformed_regular_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / audit.DEFAULT_MANIFEST_NAME
            path.write_text("not json", encoding="utf-8")
            previous, state = audit.load_previous_manifest(path, reset=True)
            self.assertIsNone(previous)
            self.assertIsNotNone(state)

    def test_invalid_manifest_requires_explicit_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / audit.DEFAULT_MANIFEST_NAME
            path.write_text("not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reset-history"):
                audit.load_previous_manifest(path, reset=False)

    def test_hash_regular_file_enforces_resource_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool"
            path.write_bytes(b"1234")
            self.assertEqual(audit.sha256_bytes(b"1234"), audit.hash_regular_file(path, max_bytes=4))
            with self.assertRaisesRegex(ValueError, "bounded regular file"):
                audit.hash_regular_file(path, max_bytes=3)

    def test_end_to_end_audit_requires_two_clean_rounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.minimal_audit_root(root)
            unavailable = {name: None for name in audit.OPTIONAL_TOOLS}
            with mock.patch.object(audit, "tool_inventory", return_value=unavailable):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(0, audit.run_audit(self.audit_args(root)))
                    first = audit.contract.load_json(root / audit.DEFAULT_MANIFEST_NAME)
                    self.assertEqual("CLEAN_ROUND_1", first["result"])
                    self.assertEqual(0, audit.run_audit(self.audit_args(root)))
            second = audit.contract.load_json(root / audit.DEFAULT_MANIFEST_NAME)
            self.assertEqual("PASS_CONVERGED", second["result"])
            self.assertEqual(2, second["clean_streak"])
            self.assertTrue(any("Optional static analyzers" in item for item in second["uncovered"]))

    def test_failed_required_check_resets_streak_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.minimal_audit_root(root, failing=True)
            unavailable = {name: None for name in audit.OPTIONAL_TOOLS}
            with mock.patch.object(audit, "tool_inventory", return_value=unavailable):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(1, audit.run_audit(self.audit_args(root)))
            manifest = audit.contract.load_json(root / audit.DEFAULT_MANIFEST_NAME)
            self.assertEqual("FAIL", manifest["result"])
            self.assertEqual(0, manifest["clean_streak"])
            self.assertTrue(manifest["findings"]["errors"])

    def test_audit_fails_when_release_completeness_is_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.minimal_audit_root(root)
            matrix_path = root / audit.DEFAULT_COMPLETENESS_NAME
            data = audit.contract.load_json(matrix_path)
            data["capabilities"][0]["negative_tests"] = []
            matrix_path.write_text(json.dumps(data), encoding="utf-8")
            unavailable = {name: None for name in audit.OPTIONAL_TOOLS}
            with mock.patch.object(audit, "tool_inventory", return_value=unavailable):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(1, audit.run_audit(self.audit_args(root)))
            manifest = audit.contract.load_json(root / audit.DEFAULT_MANIFEST_NAME)
            self.assertEqual("FAIL", manifest["result"])
            completeness = next(item for item in manifest["checks"] if item["id"] == "release-completeness")
            self.assertEqual("failed", completeness["status"])

    def test_audit_detects_artifact_change_during_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.minimal_audit_root(root)
            unavailable = {name: None for name in audit.OPTIONAL_TOOLS}
            changed = False

            def mutate_once(spec, **_kwargs):
                nonlocal changed
                if not changed:
                    (root / "changed.txt").write_text("changed\n", encoding="utf-8")
                    changed = True
                return {
                    "id": spec.check_id,
                    "category": spec.category,
                    "required": spec.required,
                    "status": "passed",
                    "returncode": 0,
                }

            with mock.patch.object(audit, "tool_inventory", return_value=unavailable), mock.patch.object(
                audit,
                "run_check",
                side_effect=mutate_once,
            ):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(1, audit.run_audit(self.audit_args(root)))
            manifest = audit.contract.load_json(root / audit.DEFAULT_MANIFEST_NAME)
            self.assertTrue(any("artifact changed" in item for item in manifest["findings"]["errors"]))

    def test_strict_audit_fails_when_static_tools_are_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.minimal_audit_root(root)
            unavailable = {name: None for name in audit.OPTIONAL_TOOLS}
            with mock.patch.object(audit, "tool_inventory", return_value=unavailable):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(1, audit.run_audit(self.audit_args(root, strict_tools=True)))
            manifest = audit.contract.load_json(root / audit.DEFAULT_MANIFEST_NAME)
            self.assertEqual("FAIL", manifest["result"])
            self.assertTrue(any("ruff" in item for item in manifest["findings"]["errors"]))


if __name__ == "__main__":
    unittest.main()
