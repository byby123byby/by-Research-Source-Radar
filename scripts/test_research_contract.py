#!/usr/bin/env python3

import copy
import http.client
import io
import os
import random
import re
import stat
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_contract as rc
import install_skill as installer


CHECKED_AT = "2020-01-01T00:00:00+00:00"
SKILL_ROOT = Path(__file__).resolve().parents[1]


def evidence(locator: str, *, kind: str = "url", status: str = "observed"):
    item = {
        "kind": kind,
        "locator": locator,
        "status": status,
        "checked_at": CHECKED_AT,
        "sha256": "",
        "note": "test evidence",
    }
    if kind == "manual" and status == "verified":
        item["checked_by"] = "test reviewer"
    return item


def verified_identity(kind: str = "doi", canonical_id: str = "doi:10.1186/s13643-020-01542-z"):
    return {
        "kind": kind,
        "value": canonical_id.split(":", 1)[1],
        "status": "verified",
        "verified_at": CHECKED_AT,
        "verification_method": "official metadata API",
        "canonical_id": canonical_id,
        "canonical_url": "https://doi.org/10.1186/s13643-020-01542-z",
        "resolved_title": "Example source",
        "title_match": 1.0,
        "evidence": "https://api.crossref.org/works/10.1186%2Fs13643-020-01542-z",
    }


def valid_contract(profile: str = "computing-software", mode: str = "full"):
    data = rc.template("Example", "Which mechanisms can improve the target system?", profile, mode)
    data["scope"].update({
        "languages": ["English", "Chinese"],
        "source_types": ["papers", "repositories", "official documentation"],
        "constraints": ["bounded deployment"],
        "inclusion": ["mechanism relevance"],
        "exclusion": ["no traceable source"],
    })
    data["search_lanes"] = {
        "direct_use": {"searched": True, "summary": "Searched directly deployable evidence."},
        "mechanism_transfer": {"searched": True, "summary": "Searched transferable mechanisms independently."},
    }
    data["source_classes"] = [
        {"name": "scholarly index", "status": "searched", "searched_at": CHECKED_AT, "interface": "official API"},
        {"name": "repository registry", "status": "searched", "searched_at": CHECKED_AT, "interface": "GitHub REST API"},
        {"name": "official organizations", "status": "searched", "searched_at": CHECKED_AT, "interface": "official website"},
    ]
    data["query_families"] = [
        {
            "id": f"Q{i}",
            "concept": concept,
            "lanes": ["direct_use", "mechanism_transfer"],
            "executions": [{
                "source": "scholarly index",
                "interface": "official API",
                "exact_query": concept,
                "executed_at": CHECKED_AT,
                "filters": [],
                "limits": [],
                "results_count": 10,
                "result_evidence": evidence(f"https://example.org/search/{i}"),
            }],
            "candidates_added": 1,
        }
        for i, concept in enumerate(("problem", "mechanism", "adjacent field", "failure mode"), 1)
    ]
    data["record_management"] = {
        "status": "completed",
        "deduplication_method": "Canonical DOI, repository identity, and normalized URL.",
        "records_identified": 40,
        "duplicates_removed": 5,
        "records_screened": 35,
        "records_deep_reviewed": 5,
        "records_included": 1,
        "exclusion_reason_counts": {"out_of_scope": 30, "insufficient_evidence": 4},
        "flow_evidence": evidence("https://example.org/flow"),
    }
    data["chaining"]["backward"] = {"status": "completed", "evidence": evidence("https://example.org/backward")}
    data["chaining"]["related_projects"] = {"status": "completed", "evidence": evidence("https://example.org/related")}
    data["candidates"] = [{
        "id": "S1",
        "title": "Example source",
        "type": "paper",
        "authority": "primary source",
        "source_snapshot": {
            "kind": "publication_version",
            "value": "version of record",
            "status": "verified",
            "verified_at": CHECKED_AT,
            "canonical_value": "doi:10.1186/s13643-020-01542-z",
            "evidence": "https://api.crossref.org/works/10.1186%2Fs13643-020-01542-z",
        },
        "source_identity": verified_identity(),
        "discovered_via": ["Q2", "chaining:related_projects"],
        "direct_use_fit": "low",
        "mechanism_fit": "high",
        "status": "adapt",
        "rationale": "Runtime differs but the mechanism transfers.",
        "review_depth": "deep",
        "reviewed": [
            evidence("Methods section", kind="section"),
            evidence("Limitations section", kind="section"),
        ],
        "not_reviewed": [],
        "open_questions": [],
    }]
    data["mechanisms"] = [{
        "id": "M1",
        "source_id": "S1",
        "statement": "A traceable mechanism changes the selected outcome.",
        "evidence_location": evidence("Methods section", kind="section"),
        "evidence_strength": "primary implementation plus test",
        "applicability": "adaptable under local constraints",
        "decision": "adapt",
        "translation": "Reimplement the bounded mechanism locally.",
        "implementation_status": "validated",
        "artifact": evidence("artifact manually inspected", kind="manual", status="verified"),
        "decision_effect": "changes route selection",
        "positive_test": evidence("positive test result", kind="manual", status="verified"),
        "failure_test": evidence("failure test result", kind="manual", status="verified"),
        "audit_evidence": evidence("audit result", kind="manual", status="verified"),
        "claim_boundary": "adaptation, not reproduction",
    }]
    data["gaps"] = [{
        "type": "coverage",
        "detail": "Private and unindexed work may be absent.",
        "impact": "A newer mechanism may be missed.",
        "mitigation": "Run a dated refresh.",
        "status": "open",
    }]
    data["stop_rule"] = {
        "rule": "Two expansion rounds add no new high-relevance mechanism class.",
        "satisfied": True,
        "evidence": evidence("https://example.org/saturation"),
    }
    data["coverage_statement"] = (
        "As of 2026-07-16, documented sources and query families were searched. "
        "This is not proof that every relevant source was found."
    )
    return data


def recorded_seed_discovery():
    return {
        "status": "recorded",
        "source_type": "screenshot",
        "platform": "user-provided Xiaohongshu screenshot",
        "source_locator": "conversation attachment",
        "shared_at": CHECKED_AT,
        "seed_summary": "A redacted screenshot describing a traceable research-memory project.",
        "retention": "redacted",
        "extraction_method": "manual inspection with OCR variants preserved",
        "extraction_confidence": 0.9,
        "uncertain_variants": ["example-memory", "example memory"],
        "source_evidence": evidence("user-provided screenshot", kind="note"),
        "mechanism_fingerprint": {
            "problem": "traceable research memory",
            "modalities": ["screenshot", "text"],
            "core_mechanisms": ["temporal knowledge graph", "retrieval"],
            "runtime_constraints": ["local-first deployment"],
            "claimed_evidence": ["project post claims lower retrieval cost"],
            "unresolved_claims": ["claimed benchmark not yet verified"],
        },
    }


def valid_source_depth_contract():
    data = valid_contract(mode="source-depth")
    data["source_classes"] = data["source_classes"][:1]
    data["query_families"] = []
    data["record_management"]["status"] = "not_applicable"
    for item in data["chaining"].values():
        item.clear()
        item.update({"status": "not_applicable", "reason": "Single-source depth review."})
    data["candidates"][0]["discovered_via"] = ["user_seed"]
    data["seed_discovery"] = recorded_seed_discovery()
    return data


class ResearchContractTests(unittest.TestCase):
    def completed_trend_discovery(self):
        return {
            "status": "completed",
            "reason": "",
            "window_days": 30,
            "definition": "Recent release activity or repeated independent technical coverage within 30 days.",
            "sources": [
                {
                    "name": "repository activity",
                    "independence_group": "GitHub platform activity",
                    "interface": "GitHub search and releases",
                    "exact_query": "topic:research created:>=2026-06-16",
                    "searched_at": CHECKED_AT,
                    "results_count": 10,
                    "result_evidence": evidence("https://example.org/trends/repositories"),
                },
                {
                    "name": "technical coverage",
                    "independence_group": "Independent technical publications",
                    "interface": "web search",
                    "exact_query": "research tool release technical blog",
                    "searched_at": CHECKED_AT,
                    "results_count": 8,
                    "result_evidence": evidence("https://example.org/trends/blogs"),
                },
            ],
            "signals": [
                {
                    "id": "T1",
                    "candidate_id": "S1",
                    "source": "repository activity",
                    "signal_type": "release_activity",
                    "value": "two releases in the observation window",
                    "observed_at": CHECKED_AT,
                    "evidence": evidence("https://example.org/trends/s1"),
                },
                {
                    "id": "T2",
                    "candidate_id": "S1",
                    "source": "technical coverage",
                    "signal_type": "technical_blog_frequency",
                    "value": "covered by independent technical publications",
                    "observed_at": CHECKED_AT,
                    "evidence": evidence("https://example.org/trends/s2"),
                },
            ],
            "claims": [
                {
                    "id": "TC1",
                    "candidate_id": "S1",
                    "label": "emerging",
                    "signal_ids": ["T1", "T2"],
                    "boundary": "Time-bounded attention signal, not evidence of quality or effectiveness.",
                }
            ],
            "triangulation_rule": "Use popularity language only after two independent signal classes agree.",
            "evidence_policy": "discovery_only",
        }

    def test_template_exposes_privacy_minimized_seed_provenance(self):
        data = rc.template("Example", "Question?", "computing-software")
        self.assertEqual("not_applicable", data["seed_discovery"]["status"])
        self.assertEqual("not_retained", data["seed_discovery"]["retention"])

    def test_user_seed_route_requires_recorded_seed_provenance(self):
        data = valid_contract()
        data["candidates"][0]["discovered_via"] = ["user_seed"]
        findings = rc.validate_contract(data)
        self.assertTrue(any("require recorded seed_discovery" in item for item in findings.errors))

    def test_recorded_seed_provenance_validates_and_renders(self):
        data = valid_contract()
        data["candidates"][0]["discovered_via"] = ["user_seed"]
        data["seed_discovery"] = recorded_seed_discovery()
        findings = rc.validate_contract(data)
        self.assertEqual([], findings.errors)
        report = rc.render_contract(data)
        self.assertIn("User-shared seed provenance", report)
        self.assertIn("Privacy-minimized summary", report)
        self.assertIn("temporal knowledge graph", report)

    def test_seed_provenance_rejects_invalid_confidence_evidence_and_fingerprint(self):
        data = valid_contract()
        data["candidates"][0]["discovered_via"] = ["user_seed"]
        data["seed_discovery"] = recorded_seed_discovery()
        data["seed_discovery"]["extraction_confidence"] = 1.5
        data["seed_discovery"]["source_evidence"]["status"] = "pending"
        data["seed_discovery"]["mechanism_fingerprint"]["core_mechanisms"] = []
        findings = rc.validate_contract(data)
        self.assertTrue(any("extraction_confidence" in item for item in findings.errors))
        self.assertTrue(any("source_evidence.status" in item for item in findings.errors))
        self.assertTrue(any("core_mechanisms" in item for item in findings.errors))

    def test_recorded_seed_provenance_requires_a_user_seed_route(self):
        data = valid_contract()
        data["seed_discovery"] = recorded_seed_discovery()
        findings = rc.validate_contract(data)
        self.assertTrue(any("requires a candidate discovered_via user_seed" in item for item in findings.errors))

    def test_diff_reports_seed_provenance_changes(self):
        old = valid_source_depth_contract()
        new = copy.deepcopy(old)
        new["seed_discovery"]["seed_summary"] = "A different privacy-minimized seed summary."
        diff = rc.contract_diff(old, new)
        changed_sections = {item["section"] for item in diff["sections_changed"]}
        self.assertIn("seed_discovery", changed_sections)

    def test_template_exposes_optional_trend_discovery(self):
        data = rc.template("Example", "Question?", "computing-software")
        self.assertEqual("not_requested", data["scope"]["trend_requirement"])
        self.assertEqual("not_applicable", data["trend_discovery"]["status"])

    def test_required_trend_sweep_must_be_completed(self):
        data = valid_contract()
        data["scope"]["trend_requirement"] = "required"
        findings = rc.validate_contract(data)
        self.assertTrue(any("needs completed trend_discovery" in item for item in findings.errors))

    def test_completed_trend_sweep_is_valid_and_routes_candidate(self):
        data = valid_contract()
        data["scope"]["trend_requirement"] = "required"
        data["trend_discovery"] = self.completed_trend_discovery()
        data["candidates"][0]["discovered_via"].append("trend:T1")
        findings = rc.validate_contract(data)
        self.assertEqual([], findings.errors)

    def test_trend_popularity_cannot_be_promoted_to_evidence_quality(self):
        data = valid_contract()
        data["scope"]["trend_requirement"] = "required"
        data["trend_discovery"] = self.completed_trend_discovery()
        data["trend_discovery"]["evidence_policy"] = "empirical_evidence"
        data["trend_discovery"]["sources"] = data["trend_discovery"]["sources"][:1]
        findings = rc.validate_contract(data)
        self.assertTrue(any("popularity is not evidence quality" in item for item in findings.errors))
        self.assertTrue(any("at least two independent sources" in item for item in findings.errors))

    def test_trend_route_must_bind_signal_to_same_candidate(self):
        data = valid_contract()
        data["scope"]["trend_requirement"] = "required"
        data["trend_discovery"] = self.completed_trend_discovery()
        data["candidates"][0]["discovered_via"].append("trend:missing")
        findings = rc.validate_contract(data)
        self.assertTrue(any("completed signal for that candidate" in item for item in findings.errors))

    def test_trend_claim_rejects_correlated_sources(self):
        data = valid_contract()
        data["scope"]["trend_requirement"] = "required"
        data["trend_discovery"] = self.completed_trend_discovery()
        data["trend_discovery"]["sources"][1]["independence_group"] = "GitHub platform activity"
        findings = rc.validate_contract(data)
        self.assertTrue(any("two independent source groups" in item for item in findings.errors))
        self.assertTrue(any("signals from at least two independent source groups" in item for item in findings.errors))

    def test_trend_fields_reject_non_scalar_or_unhashable_values_without_crashing(self):
        mutations = {
            "trend status": lambda trend: trend.__setitem__("status", {}),
            "claim signal_ids": lambda trend: trend["claims"][0].__setitem__("signal_ids", [[]]),
            "signal source": lambda trend: trend["signals"][0].__setitem__("source", []),
            "signal candidate": lambda trend: trend["signals"][0].__setitem__("candidate_id", []),
            "claim candidate": lambda trend: trend["claims"][0].__setitem__("candidate_id", []),
            "signal value": lambda trend: trend["signals"][0].__setitem__("value", {}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                data = valid_contract()
                data["scope"]["trend_requirement"] = "required"
                data["trend_discovery"] = self.completed_trend_discovery()
                mutate(data["trend_discovery"])
                findings = rc.validate_contract(data)
                self.assertTrue(findings.errors)

    def test_legacy_v2_contract_without_trend_section_remains_valid(self):
        data = valid_contract()
        data["scope"].pop("trend_requirement")
        data.pop("trend_discovery")
        findings = rc.validate_contract(data)
        self.assertEqual([], findings.errors)

    def test_cli_rejects_nonpositive_timeout(self):
        parser = rc.build_parser()
        with redirect_stderr(io.StringIO()):
            for value in ("0", "-1", "nan", "inf", "-inf"):
                with self.subTest(value=value), self.assertRaises(SystemExit):
                    parser.parse_args(["validate", "contract.json", "--timeout", value])

    def test_offline_schema_pass_does_not_claim_live_identity_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            rc.write_json_atomic(path, valid_contract())
            args = rc.build_parser().parse_args(["validate", str(path), "--base", directory])
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, args.func(args))
            rendered = output.getvalue()
            self.assertIn("SCHEMA_PASS", rendered)
            self.assertIn("was not re-resolved", rendered)
            self.assertNotIn("ONLINE_IDENTITY_PASS", rendered)

    def test_atomic_json_write_uses_unique_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            with mock.patch.object(rc.tempfile, "mkstemp", wraps=rc.tempfile.mkstemp) as create:
                rc.write_json_atomic(path, {"value": 1})
            self.assertEqual({"value": 1}, rc.load_json(path))
            self.assertTrue(create.called)
            self.assertEqual(f".{path.name}.", create.call_args.kwargs["prefix"])
            self.assertEqual([], list(path.parent.glob(f".{path.name}.*.tmp")))

    def test_atomic_writes_preserve_existing_mode_and_use_private_contract_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = root / "contract.json"
            rc.write_json_atomic(contract, {"value": 1})
            self.assertEqual(0o600, stat.S_IMODE(contract.stat().st_mode))
            contract.chmod(0o640)
            rc.write_json_atomic(contract, {"value": 2})
            self.assertEqual(0o640, stat.S_IMODE(contract.stat().st_mode))

            report = root / "report.md"
            rc.write_text_atomic(report, "report\n", default_mode=0o644)
            self.assertEqual(0o644, stat.S_IMODE(report.stat().st_mode))

    def test_atomic_writers_sync_parent_directory_after_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(rc, "fsync_directory") as contract_sync:
                rc.write_json_atomic(root / "contract.json", {"value": 1})
            contract_sync.assert_called_once_with(root)

            with mock.patch.object(installer, "fsync_directory") as installer_sync:
                installer.write_text_atomic(root / "AGENTS.md", "instructions\n")
            installer_sync.assert_called_once_with(root)

    def test_contract_writer_reports_when_replace_committed_but_directory_sync_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            with mock.patch.object(rc, "fsync_directory", side_effect=OSError("injected sync failure")):
                with self.assertRaisesRegex(RuntimeError, "output was replaced"):
                    rc.write_json_atomic(path, {"value": 1})
            self.assertEqual({"value": 1}, rc.load_json(path))

    def test_verify_sources_does_not_overwrite_concurrent_contract_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            data = valid_contract()
            rc.write_json_atomic(path, data)
            verified = copy.deepcopy(data["candidates"][0]["source_identity"])
            snapshot = copy.deepcopy(data["candidates"][0]["source_snapshot"])

            def verify_then_edit(*_args, **_kwargs):
                concurrent = rc.load_json(path)
                concurrent["project"] = "concurrent edit"
                rc.write_json_atomic(path, concurrent)
                return copy.deepcopy(verified)

            args = rc.build_parser().parse_args(["verify-sources", str(path), "--write"])
            with mock.patch.object(rc, "verify_candidate_source", side_effect=verify_then_edit), mock.patch.object(
                rc,
                "verify_candidate_snapshot",
                return_value=snapshot,
            ):
                with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
                    args.func(args)

            self.assertEqual("concurrent edit", rc.load_json(path)["project"])

    def test_render_refuses_existing_report_without_force(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = root / "contract.json"
            report = root / "report.md"
            rc.write_json_atomic(contract, valid_contract())
            report.write_text("keep me\n", encoding="utf-8")

            args = rc.build_parser().parse_args([
                "render",
                str(contract),
                "--output",
                str(report),
                "--base",
                directory,
            ])
            with self.assertRaisesRegex(SystemExit, "refusing to overwrite"):
                args.func(args)
            self.assertEqual("keep me\n", report.read_text(encoding="utf-8"))

            forced = rc.build_parser().parse_args([
                "render",
                str(contract),
                "--output",
                str(report),
                "--base",
                directory,
                "--force",
            ])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, forced.func(forced))
            self.assertIn("# Example discovery and translation audit", report.read_text(encoding="utf-8"))

    def test_render_does_not_replace_output_created_during_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = root / "contract.json"
            report = root / "report.md"
            data = valid_contract()
            rc.write_json_atomic(contract, data)
            original_render = rc.render_contract

            def render_then_create(value):
                report.write_text("concurrent report\n", encoding="utf-8")
                return original_render(value)

            args = rc.build_parser().parse_args([
                "render",
                str(contract),
                "--output",
                str(report),
                "--base",
                directory,
            ])
            with mock.patch.object(rc, "render_contract", side_effect=render_then_create):
                with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
                    args.func(args)
            self.assertEqual("concurrent report\n", report.read_text(encoding="utf-8"))

    def test_report_scanner_rejects_oversized_input(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.md"
            report.write_text("oversized", encoding="utf-8")
            findings = rc.Findings()
            with mock.patch.object(rc, "MAX_REPORT_BYTES", 1):
                self.assertEqual("", rc.read_report_text(report, findings))
            self.assertTrue(any("was not scanned" in item for item in findings.errors))

    def test_atomic_contract_write_rejects_symbolic_link(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            link = root / "contract.json"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                rc.write_json_atomic(link, {"value": 1})
            self.assertEqual("{}\n", target.read_text(encoding="utf-8"))

    def test_load_json_rejects_non_utf8_with_controlled_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_bytes(b"\xff\xfe\x00")
            with self.assertRaisesRegex(SystemExit, "not valid UTF-8"):
                rc.load_json(path)

    def test_json_parse_errors_neutralize_control_and_bidi_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text('{"safe\\u001b\\u202e": 1, "safe\\u001b\\u202e": 2}', encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                rc.load_json(path)
            message = str(raised.exception)
            self.assertNotIn("\x1b", message)
            self.assertNotIn("\u202E", message)
            self.assertIn("\\u001B", message)
            self.assertIn("\\u202E", message)

    def test_load_json_rejects_oversized_contract_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_bytes(b" " * (rc.MAX_CONTRACT_BYTES + 1))
            with self.assertRaisesRegex(SystemExit, "contract exceeds"):
                rc.load_json(path)

    def test_load_json_rejects_excessive_depth_and_node_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = "0"
            for _ in range(rc.MAX_JSON_DEPTH + 2):
                nested = '{"x":' + nested + "}"
            depth_path = root / "depth.json"
            depth_path.write_text(nested, encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "nesting depth"):
                rc.load_json(depth_path)

            nodes_path = root / "nodes.json"
            nodes_path.write_text('{"items":[0,1,2,3,4,5,6,7,8,9]}', encoding="utf-8")
            with mock.patch.object(rc, "MAX_JSON_NODES", 5):
                with self.assertRaisesRegex(SystemExit, "JSON nodes"):
                    rc.load_json(nodes_path)

    def test_atomic_json_write_refuses_contract_larger_than_read_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            with mock.patch.object(rc, "MAX_CONTRACT_BYTES", 20):
                with self.assertRaisesRegex(ValueError, "contract exceeds"):
                    rc.write_json_atomic(path, {"value": "too large for patched limit"})
            self.assertFalse(path.exists())

    def test_diff_tolerates_malformed_ids_in_diagnostic_mode(self):
        old = {"candidates": [{"id": []}], "mechanisms": [{"id": {}}]}
        new = {"candidates": [{"id": {}}], "mechanisms": [{"id": []}]}
        result = rc.contract_diff(old, new)
        self.assertEqual([], result["candidates"]["added"])
        self.assertEqual([], result["mechanisms"]["changed"])

    def test_skill_package_metadata_and_references_are_self_consistent(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        license_text = (SKILL_ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertLess(len(skill_text.splitlines()), 500)
        self.assertRegex(
            license_text,
            r"^MIT License\n\nCopyright \(c\) [12]\d{3}(?:-[12]\d{3})? [^\r\n]+\n",
        )
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        self.assertIn("The above copyright notice and this permission notice shall be included", license_text)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', license_text)
        self.assertIn("IN NO EVENT SHALL THE", license_text)
        self.assertIn("LIABILITY", license_text)
        frontmatter = skill_text.split("---", 2)[1]
        self.assertIn("name: research-discovery-and-translation-audit", frontmatter)
        self.assertIn("description:", frontmatter)
        for relative_path in (
            "LICENSE",
            "RELEASE_COMPLETENESS.json",
            "references/domain-profiles.md",
            "references/discovery-protocol.md",
            "references/seed-to-neighbor-discovery.md",
            "references/release-requirements.md",
            "references/contract-schema.md",
            "references/portability.md",
            "agents/openai.yaml",
            "scripts/install_skill.py",
            "scripts/research_contract.py",
        ):
            self.assertTrue((SKILL_ROOT / relative_path).is_file(), relative_path)
        interface = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "Research Discovery and Translation Audit"', interface)
        self.assertIn("authoritative metadata", interface)
        short_description = next(
            line.split('"', 2)[1]
            for line in interface.splitlines()
            if line.strip().startswith("short_description:")
        )
        self.assertGreaterEqual(len(short_description), 25)
        self.assertLessEqual(len(short_description), 64)
        self.assertIn("$research-discovery-and-translation-audit", interface)

    def test_documentation_local_links_and_translation_sections_are_consistent(self):
        documents = [
            SKILL_ROOT / "SKILL.md",
            *(SKILL_ROOT / "references").glob("*.md"),
        ]
        readme = SKILL_ROOT / "README.md"
        chinese_readme = SKILL_ROOT / "README.zh-CN.md"
        if readme.is_file() and chinese_readme.is_file():
            documents.extend((readme, chinese_readme))
        link_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

        def markdown_anchors(path):
            headings = re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", path.read_text(encoding="utf-8"), re.M)
            return {
                re.sub(r"\s+", "-", re.sub(r"[^\w\- ]", "", heading.lower()).strip())
                for heading in headings
            }

        for document in documents:
            for raw_target in link_pattern.findall(document.read_text(encoding="utf-8")):
                target = raw_target.strip()
                if target.startswith(("https://", "http://", "mailto:")):
                    continue
                parsed_target = urllib.parse.urlsplit(target)
                path_text = urllib.parse.unquote(parsed_target.path)
                resolved = (document.parent / path_text).resolve() if path_text else document.resolve()
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(resolved.is_relative_to(SKILL_ROOT.resolve()))
                    self.assertTrue(resolved.exists(), f"broken local link in {document}: {target}")
                    if parsed_target.fragment:
                        fragment = urllib.parse.unquote(parsed_target.fragment)
                        self.assertIn(fragment, markdown_anchors(resolved), f"broken anchor in {document}: {target}")

        heading_pairs = (
            ("Quick Start", "快速开始"),
            ("Use It For", "适合用它做什么"),
            ("Inputs and Outputs", "输入与产出"),
            ("Workflow", "工作流程"),
            ("Emerging and Popular Discovery", "新兴与热门内容发现"),
            ("Seed-to-Neighbor Discovery", "线索到邻近项目发现"),
            ("Coverage And Token Efficiency", "覆盖面与 Token 效率"),
            ("Modes", "工作模式"),
            ("Boundaries", "能力边界"),
            ("Installation", "安装"),
            ("Host and IDE Compatibility", "宿主与 IDE 兼容性"),
            ("Research Contract and CLI", "研究合同与命令行"),
            ("Source Verification", "来源身份验证"),
            ("Multilingual and Academic Workflows", "多语言与学术工作流"),
            ("Security and Integrity", "安全与研究诚信"),
            ("Repository Layout", "仓库结构"),
            ("Retrieval Effectiveness Benchmark", "检索效果 A/B Benchmark"),
            ("Audit Convergence", "审计收敛"),
            ("Tests", "测试"),
            ("Feedback and Contributions", "反馈与贡献"),
            ("License", "许可证"),
            ("Before Publication", "正式发布前"),
            ("Documentation Inspiration", "文档设计参考"),
        )
        if readme.is_file() and chinese_readme.is_file():
            english = re.findall(r"^## (.+)$", readme.read_text(encoding="utf-8"), re.M)
            chinese = re.findall(r"^## (.+)$", chinese_readme.read_text(encoding="utf-8"), re.M)
            self.assertEqual([pair[0] for pair in heading_pairs], english)
            self.assertEqual([pair[1] for pair in heading_pairs], chinese)

    def test_neighbor_coverage_probe_and_token_layers_are_documented(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("### Adaptive Coverage And Token Budget", skill_text)
        self.assertIn("one targeted gap query", skill_text)
        self.assertIn("L0", skill_text)
        self.assertIn("L1", skill_text)
        self.assertIn("L2 only when the user asks", skill_text)
        self.assertIn("four completed web-search calls", skill_text)
        self.assertIn("eight query strings", skill_text)

    def test_neighbor_mult_path_fusion_and_two_stage_reranking_are_documented(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("### Multi-Path Fusion And Two-Stage Reranking", skill_text)
        self.assertIn("canonical identity", skill_text)
        self.assertIn("discovery_paths", skill_text)
        self.assertIn("Stage 1, cheap gate", skill_text)
        self.assertIn("Stage 2, focused rerank", skill_text)
        self.assertIn("top six to eight", skill_text)
        self.assertIn("path diversity only as a tie-breaker", skill_text)
        self.assertIn("do not return raw search pages", skill_text)

    def test_neighbor_fusion_separates_provenance_from_relevance_and_opaque_scores(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("not relevance evidence", skill_text)
        self.assertIn("cannot override an identity mismatch", skill_text)
        self.assertIn("structured lexicographic order rather than a single opaque aggregate score", skill_text)
        self.assertIn("L2 reading remains limited", skill_text)

    def test_user_seed_recovery_and_hard_negative_gate_are_documented(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference_text = (SKILL_ROOT / "references/seed-to-neighbor-discovery.md").read_text(encoding="utf-8")
        self.assertIn("### User-Seed Recovery And Hard-Negative Gate", skill_text)
        self.assertIn("anchor_seed", skill_text)
        self.assertIn("known_leads", skill_text)
        self.assertIn("uncovered_known_leads", skill_text)
        self.assertIn("recovered_known_leads", skill_text)
        self.assertIn("hard_negative_check: not_run", skill_text)
        self.assertIn("### 4A. User-seed recovery and hard negatives", reference_text)

    def test_user_seed_recovery_does_not_claim_social_graph_or_exhaustive_precision(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference_text = (SKILL_ROOT / "references/seed-to-neighbor-discovery.md").read_text(encoding="utf-8")
        self.assertIn("do not silently add remembered or private social-feed items", skill_text)
        self.assertIn("Do not infer them from private social-platform history", reference_text)
        self.assertIn("do not prove exhaustive", reference_text)

    def test_neighbor_stop_rule_has_no_legacy_conflicting_sentence(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("The global neighbor stop rule below remains authoritative", skill_text)
        self.assertNotIn("stop after the seed plus 3–5 distinct candidate families", skill_text)

    def test_native_project_installer_copies_complete_package_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.install(
                "agents-project",
                project=project,
                force=False,
                source=SKILL_ROOT,
            )
            self.assertEqual((project / ".agents" / "skills" / installer.SKILL_NAME).resolve(), destination.resolve())
            self.assertTrue((destination / "LICENSE").is_file())
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertTrue((destination / "RELEASE_COMPLETENESS.json").is_file())
            self.assertTrue((destination / "references" / "portability.md").is_file())
            with self.assertRaises(FileExistsError):
                installer.install("agents-project", project=project, force=False, source=SKILL_ROOT)

    def test_installer_rejects_wrong_required_entry_types(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            (source / "agents").mkdir(parents=True)
            (source / "references").mkdir()
            (source / "scripts").mkdir()
            (source / "LICENSE").mkdir()
            (source / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\n", encoding="utf-8")
            (source / "RELEASE_COMPLETENESS.json").write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "LICENSE must be a regular file"):
                installer.validate_package_source(source)

            (source / "LICENSE").rmdir()
            (source / "LICENSE").write_text("test license\n", encoding="utf-8")
            (source / "scripts").rmdir()
            (source / "scripts").write_text("not a directory\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "scripts must be a directory"):
                installer.validate_package_source(source)

    def test_installer_rejects_symbolic_links_inside_source_package(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "agents").mkdir(parents=True)
            (source / "references").mkdir()
            (source / "scripts").mkdir()
            (source / "LICENSE").write_text("test license\n", encoding="utf-8")
            (source / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\n", encoding="utf-8")
            (source / "RELEASE_COMPLETENESS.json").write_text("{}\n", encoding="utf-8")
            external = root / "private.txt"
            external.write_text("private", encoding="utf-8")
            (source / "references" / "linked.md").symlink_to(external)
            project = root / "project"
            with self.assertRaisesRegex(ValueError, "must not contain symbolic links"):
                installer.install("agents-project", project=project, force=False, source=source)
            self.assertFalse(installer.target_path("agents-project", project).exists())

    def test_installer_rejects_special_files_inside_source_package(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO files are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "agents").mkdir(parents=True)
            (source / "references").mkdir()
            (source / "scripts").mkdir()
            (source / "LICENSE").write_text("test license\n", encoding="utf-8")
            (source / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\n", encoding="utf-8")
            (source / "RELEASE_COMPLETENESS.json").write_text("{}\n", encoding="utf-8")
            os.mkfifo(source / "scripts" / "blocked.fifo")
            project = root / "project"
            with self.assertRaisesRegex(ValueError, "only regular files"):
                installer.install("agents-project", project=project, force=False, source=source)
            self.assertFalse(installer.target_path("agents-project", project).exists())

    def test_installer_rejects_source_package_over_size_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "agents").mkdir(parents=True)
            (source / "references").mkdir()
            (source / "scripts").mkdir()
            (source / "LICENSE").write_text("test license\n", encoding="utf-8")
            (source / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\n", encoding="utf-8")
            (source / "RELEASE_COMPLETENESS.json").write_text("{}\n", encoding="utf-8")
            project = root / "project"
            with mock.patch.object(installer, "MAX_PACKAGE_BYTES", 1):
                with self.assertRaisesRegex(ValueError, "package exceeds"):
                    installer.install("agents-project", project=project, force=False, source=source)
            self.assertFalse(installer.target_path("agents-project", project).exists())

    def test_force_install_replaces_stale_package_without_leaving_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("github-project", project)
            destination.mkdir(parents=True)
            (destination / "stale.txt").write_text("stale", encoding="utf-8")
            installer.install("github-project", project=project, force=True, source=SKILL_ROOT)
            self.assertFalse((destination / "stale.txt").exists())
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.backup-*")))

    def test_installer_does_not_replace_destination_created_during_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("github-project", project)
            real_stage = installer.stage_package

            def stage_then_create(*args, **kwargs):
                staging = real_stage(*args, **kwargs)
                destination.mkdir(parents=True)
                (destination / "concurrent.txt").write_text("preserve\n", encoding="utf-8")
                return staging

            with mock.patch.object(installer, "stage_package", side_effect=stage_then_create):
                with self.assertRaisesRegex(RuntimeError, "destination changed concurrently"):
                    installer.install("github-project", project=project, force=False, source=SKILL_ROOT)
            self.assertEqual("preserve\n", (destination / "concurrent.txt").read_text(encoding="utf-8"))
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.stage-*")))

    def test_force_installer_does_not_replace_destination_edited_during_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("github-project", project)
            destination.mkdir(parents=True)
            stale = destination / "stale.txt"
            stale.write_text("before\n", encoding="utf-8")
            real_stage = installer.stage_package

            def stage_then_edit(*args, **kwargs):
                staging = real_stage(*args, **kwargs)
                stale.write_text("concurrent edit\n", encoding="utf-8")
                return staging

            with mock.patch.object(installer, "stage_package", side_effect=stage_then_edit):
                with self.assertRaisesRegex(RuntimeError, "destination changed concurrently"):
                    installer.install("github-project", project=project, force=True, source=SKILL_ROOT)
            self.assertEqual("concurrent edit\n", stale.read_text(encoding="utf-8"))
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.stage-*")))

    def test_project_installer_rechecks_parent_path_immediately_before_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("github-project", project)
            calls = 0
            real_guard = installer.reject_symlinked_parent_components

            def fail_second_guard(root, parent):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ValueError("injected parent-path replacement")
                return real_guard(root, parent)

            with mock.patch.object(installer, "reject_symlinked_parent_components", side_effect=fail_second_guard):
                with self.assertRaisesRegex(ValueError, "parent-path replacement"):
                    installer.install("github-project", project=project, force=False, source=SKILL_ROOT)
            self.assertEqual(2, calls)
            self.assertFalse(destination.exists())
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.stage-*")))

    def test_installer_rejects_staging_content_changed_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("github-project", project)

            def mutate_staging():
                staging = next(destination.parent.glob(f".{installer.SKILL_NAME}.stage-*"))
                (staging / "SKILL.md").write_text("concurrent replacement\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "staged package changed concurrently"):
                installer.copy_package(
                    SKILL_ROOT,
                    destination,
                    force=False,
                    precommit=mutate_staging,
                )
            self.assertFalse(destination.exists())
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.stage-*")))

    def test_installer_bounds_destination_state_scan_before_force_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("github-project", project)
            destination.mkdir(parents=True)
            stale = destination / "stale.txt"
            stale.write_text("preserve\n", encoding="utf-8")
            with mock.patch.object(installer, "MAX_DESTINATION_STATE_ENTRIES", 1):
                with self.assertRaisesRegex(RuntimeError, "destination exceeds"):
                    installer.install("github-project", project=project, force=True, source=SKILL_ROOT)
            self.assertEqual("preserve\n", stale.read_text(encoding="utf-8"))

    def test_backup_cleanup_failure_keeps_committed_install_and_reports_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("github-project", project)
            destination.mkdir(parents=True)
            (destination / "stale.txt").write_text("stale", encoding="utf-8")
            real_remove = installer.remove_path

            def fail_backup_cleanup(path):
                if ".backup-" in Path(path).name:
                    raise OSError("injected cleanup failure")
                return real_remove(path)

            with mock.patch.object(installer, "remove_path", side_effect=fail_backup_cleanup):
                with self.assertRaisesRegex(RuntimeError, "installation committed"):
                    installer.install("github-project", project=project, force=True, source=SKILL_ROOT)
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertFalse((destination / "stale.txt").exists())
            self.assertEqual(1, len(list(destination.parent.glob(f".{installer.SKILL_NAME}.backup-*"))))

    def test_backup_cleanup_refuses_concurrently_replaced_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("github-project", project)
            destination.mkdir(parents=True)
            (destination / "stale.txt").write_text("stale", encoding="utf-8")
            real_commit = installer.commit_staged_package

            def commit_then_replace_backup(*args, **kwargs):
                backup, installed_state = real_commit(*args, **kwargs)
                self.assertIsNotNone(backup)
                backup_path = backup[0]
                installer.remove_path(backup_path)
                backup_path.mkdir()
                (backup_path / "replacement.txt").write_text("preserve", encoding="utf-8")
                return backup, installed_state

            with mock.patch.object(installer, "commit_staged_package", side_effect=commit_then_replace_backup):
                with self.assertRaisesRegex(RuntimeError, "backup changed concurrently"):
                    installer.install("github-project", project=project, force=True, source=SKILL_ROOT)

            self.assertTrue((destination / "SKILL.md").is_file())
            backups = list(destination.parent.glob(f".{installer.SKILL_NAME}.backup-*"))
            self.assertEqual(1, len(backups))
            self.assertEqual("preserve", (backups[0] / "replacement.txt").read_text(encoding="utf-8"))

    def test_force_install_replaces_regular_file_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("github-project", project)
            destination.parent.mkdir(parents=True)
            destination.write_text("not a directory", encoding="utf-8")
            installer.install("github-project", project=project, force=True, source=SKILL_ROOT)
            self.assertTrue(destination.is_dir())
            self.assertTrue((destination / "SKILL.md").is_file())

    def test_broken_symlink_destination_requires_force_and_is_replaced_safely(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("agents-project", project)
            destination.parent.mkdir(parents=True)
            destination.symlink_to(project / "missing-target")
            with self.assertRaises(FileExistsError):
                installer.install("agents-project", project=project, force=False, source=SKILL_ROOT)
            installer.install("agents-project", project=project, force=True, source=SKILL_ROOT)
            self.assertFalse(destination.is_symlink())
            self.assertTrue((destination / "SKILL.md").is_file())

    def test_portable_project_installer_preserves_and_updates_instruction_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            agents = project / "AGENTS.md"
            agents.write_text("# Existing instructions\n\nKeep this text.\n", encoding="utf-8")
            agents.chmod(0o640)
            installer.install("portable-project", project=project, force=False, source=SKILL_ROOT)
            installer.install("portable-project", project=project, force=True, source=SKILL_ROOT)
            agents_text = agents.read_text(encoding="utf-8")
            gemini_text = (project / "GEMINI.md").read_text(encoding="utf-8")
            self.assertIn("Keep this text.", agents_text)
            self.assertEqual(1, agents_text.count(installer.MANAGED_BEGIN))
            self.assertEqual(1, agents_text.count(installer.MANAGED_END))
            self.assertEqual(1, gemini_text.count(installer.MANAGED_BEGIN))
            self.assertIn(f".agent-skills/{installer.SKILL_NAME}/SKILL.md", gemini_text)
            self.assertEqual(0o640, stat.S_IMODE(agents.stat().st_mode))
            self.assertEqual(0o644, stat.S_IMODE((project / "GEMINI.md").stat().st_mode))

    def test_portable_install_rolls_back_package_and_instructions_on_partial_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("portable-project", project)
            destination.mkdir(parents=True)
            (destination / "previous.txt").write_text("previous package", encoding="utf-8")
            agents = project / "AGENTS.md"
            agents.write_text("original agents\n", encoding="utf-8")
            original_write = installer.write_text_atomic

            def fail_gemini(path, text, **kwargs):
                if Path(path).name == "GEMINI.md":
                    raise OSError("injected write failure")
                return original_write(path, text, **kwargs)

            with mock.patch.object(installer, "write_text_atomic", side_effect=fail_gemini):
                with self.assertRaisesRegex(OSError, "injected write failure"):
                    installer.install("portable-project", project=project, force=True, source=SKILL_ROOT)

            self.assertEqual("original agents\n", agents.read_text(encoding="utf-8"))
            self.assertFalse((project / "GEMINI.md").exists())
            self.assertEqual("previous package", (destination / "previous.txt").read_text(encoding="utf-8"))
            self.assertFalse((destination / "SKILL.md").exists())
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.backup-*")))
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.stage-*")))

    def test_portable_install_rolls_back_instruction_committed_before_directory_sync_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("portable-project", project)
            agents = project / "AGENTS.md"
            agents.write_text("original agents\n", encoding="utf-8")
            real_sync = installer.fsync_directory
            failed = False

            def fail_first_project_root_sync(path):
                nonlocal failed
                if Path(path).resolve() == project.resolve() and not failed:
                    failed = True
                    raise OSError("injected sync failure")
                return real_sync(path)

            with mock.patch.object(installer, "fsync_directory", side_effect=fail_first_project_root_sync):
                with self.assertRaisesRegex(installer.CommittedWriteSyncError, "instruction file was replaced"):
                    installer.install("portable-project", project=project, force=False, source=SKILL_ROOT)

            self.assertTrue(failed)
            self.assertEqual("original agents\n", agents.read_text(encoding="utf-8"))
            self.assertFalse((project / "GEMINI.md").exists())
            self.assertFalse(destination.exists())

    def test_package_commit_rolls_back_when_directory_sync_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("github-project", project)
            destination.mkdir(parents=True)
            (destination / "previous.txt").write_text("previous package", encoding="utf-8")
            real_sync = installer.fsync_directory
            failed = False

            def fail_once(path):
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError("injected sync failure")
                return real_sync(path)

            with mock.patch.object(installer, "fsync_directory", side_effect=fail_once):
                with self.assertRaisesRegex(RuntimeError, "commit was rolled back"):
                    installer.install("github-project", project=project, force=True, source=SKILL_ROOT)

            self.assertTrue(failed)
            self.assertEqual("previous package", (destination / "previous.txt").read_text(encoding="utf-8"))
            self.assertFalse((destination / "SKILL.md").exists())
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.backup-*")))
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.stage-*")))

    def test_portable_rollback_preserves_concurrently_edited_installed_package(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("portable-project", project)
            destination.mkdir(parents=True)
            (destination / "previous.txt").write_text("previous package", encoding="utf-8")
            original_write = installer.write_text_atomic

            def edit_package_then_fail(path, text, **kwargs):
                if Path(path).name == "GEMINI.md":
                    (destination / "concurrent.txt").write_text("preserve me\n", encoding="utf-8")
                    raise OSError("injected write failure")
                return original_write(path, text, **kwargs)

            with mock.patch.object(installer, "write_text_atomic", side_effect=edit_package_then_fail):
                with self.assertRaisesRegex(RuntimeError, "rollback also failed"):
                    installer.install("portable-project", project=project, force=True, source=SKILL_ROOT)

            self.assertEqual("preserve me\n", (destination / "concurrent.txt").read_text(encoding="utf-8"))
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertEqual(1, len(list(destination.parent.glob(f".{installer.SKILL_NAME}.backup-*"))))

    def test_portable_install_preserves_previous_package_when_package_swap_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = installer.target_path("portable-project", project)
            destination.mkdir(parents=True)
            (destination / "previous.txt").write_text("previous package", encoding="utf-8")
            real_replace = installer.os.replace

            def fail_stage_swap(source, target):
                source_path = Path(source)
                target_path = Path(target)
                if ".stage-" in source_path.name and target_path == destination:
                    raise OSError("injected package swap failure")
                return real_replace(source, target)

            with mock.patch.object(installer.os, "replace", side_effect=fail_stage_swap):
                with self.assertRaisesRegex(OSError, "injected package swap failure"):
                    installer.install("portable-project", project=project, force=True, source=SKILL_ROOT)

            self.assertEqual("previous package", (destination / "previous.txt").read_text(encoding="utf-8"))
            self.assertFalse((project / "AGENTS.md").exists())
            self.assertFalse((project / "GEMINI.md").exists())
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.backup-*")))
            self.assertEqual([], list(destination.parent.glob(f".{installer.SKILL_NAME}.stage-*")))

    def test_portable_installer_rejects_broken_managed_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            path = project / "AGENTS.md"
            path.write_text(installer.MANAGED_BEGIN + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                installer.install("portable-project", project=project, force=False, source=SKILL_ROOT)
            self.assertFalse((project / ".agent-skills" / installer.SKILL_NAME).exists())

    def test_portable_installer_rejects_symlinked_instruction_files_before_copy(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            external = project / "external.md"
            external.write_text("external\n", encoding="utf-8")
            (project / "AGENTS.md").symlink_to(external)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                installer.install("portable-project", project=project, force=False, source=SKILL_ROOT)
            self.assertEqual("external\n", external.read_text(encoding="utf-8"))
            self.assertFalse((project / ".agent-skills" / installer.SKILL_NAME).exists())

    def test_valid_contract_passes_all_profiles(self):
        for profile in sorted(rc.PROFILES):
            with self.subTest(profile=profile):
                findings = rc.validate_contract(valid_contract(profile))
                self.assertEqual([], findings.errors)

    def test_humanities_and_arts_profiles_are_supported_and_documented(self):
        profiles = ("humanities-languages-culture", "arts-design-media")
        domain_text = (SKILL_ROOT / "references/domain-profiles.md").read_text(encoding="utf-8")
        for profile in profiles:
            with self.subTest(profile=profile):
                self.assertIn(profile, rc.PROFILES)
                findings = rc.validate_contract(valid_contract(profile))
                self.assertEqual([], findings.errors)
        self.assertIn("## Humanities, Languages, And Cultural Heritage", domain_text)
        self.assertIn("## Arts, Design, And Media", domain_text)
        self.assertIn("provenance", domain_text)
        self.assertIn("copyright", domain_text)

    def test_source_depth_uses_mode_specific_floors(self):
        findings = rc.validate_contract(valid_source_depth_contract())
        self.assertEqual([], findings.errors)

    def test_missing_mechanism_transfer_lane_is_blocked_for_full_mode(self):
        data = valid_contract()
        data["search_lanes"]["mechanism_transfer"]["searched"] = False
        findings = rc.validate_contract(data)
        self.assertTrue(any("mechanism_transfer" in item for item in findings.errors))

    def test_query_execution_must_record_exact_reproducible_search(self):
        data = valid_contract()
        data["query_families"][0]["executions"][0]["exact_query"] = ""
        findings = rc.validate_contract(data)
        self.assertTrue(any("exact_query" in item for item in findings.errors))

    def test_record_flow_is_consistent(self):
        data = valid_contract()
        data["record_management"]["records_included"] = 99
        findings = rc.validate_contract(data)
        self.assertTrue(any("records_included exceeds" in item for item in findings.errors))

    def test_record_included_count_matches_selected_candidates(self):
        data = valid_contract()
        data["record_management"]["records_included"] = 0
        findings = rc.validate_contract(data)
        self.assertTrue(any("must equal the number" in item for item in findings.errors))

    def test_unavailable_sources_do_not_satisfy_searched_source_floor(self):
        data = valid_contract()
        for index, source in enumerate(data["source_classes"][1:], 1):
            source.clear()
            source.update({"name": f"unavailable-{index}", "status": "unavailable", "reason": "blocked"})
        findings = rc.validate_contract(data)
        self.assertTrue(any("searched source class" in item for item in findings.errors))

    def test_systematic_review_requires_search_peer_review(self):
        data = valid_contract()
        data["scope"]["review_type"] = "systematic"
        findings = rc.validate_contract(data)
        self.assertTrue(any("require recorded search-strategy peer review" in item for item in findings.errors))

    def test_selected_paper_warns_without_publication_status_check(self):
        findings = rc.validate_contract(valid_contract())
        self.assertTrue(any("correction/retraction" in item for item in findings.warnings))

    def test_selected_candidate_requires_verified_identity(self):
        data = valid_contract()
        data["candidates"][0]["source_identity"]["status"] = "pending"
        findings = rc.validate_contract(data)
        self.assertTrue(any("identity is verified" in item for item in findings.errors))

    def test_selected_candidate_requires_verified_snapshot(self):
        data = valid_contract()
        data["candidates"][0]["source_snapshot"]["status"] = "pending"
        findings = rc.validate_contract(data)
        self.assertTrue(any("snapshot is verified" in item for item in findings.errors))

    def test_paper_cannot_use_reachable_url_as_identity(self):
        data = valid_contract()
        identity = data["candidates"][0]["source_identity"]
        identity.update({"kind": "official_url", "value": "https://example.org", "canonical_id": "url:https://example.org"})
        findings = rc.validate_contract(data)
        self.assertTrue(any("DOI, arXiv, or PMID" in item for item in findings.errors))

    def test_github_repository_requires_github_identity(self):
        data = valid_contract()
        data["candidates"][0]["type"] = "github_repository"
        findings = rc.validate_contract(data)
        self.assertTrue(any("GitHub API" in item for item in findings.errors))

    def test_unknown_candidate_type_is_blocked(self):
        data = valid_contract()
        data["candidates"][0]["type"] = "paper_and_repository"
        findings = rc.validate_contract(data)
        self.assertTrue(any("controlled v2 candidate type" in item for item in findings.errors))

    def test_duplicate_canonical_source_is_blocked(self):
        data = valid_contract()
        duplicate = copy.deepcopy(data["candidates"][0])
        duplicate["id"] = "S2"
        duplicate["status"] = "monitor"
        data["candidates"].append(duplicate)
        findings = rc.validate_contract(data)
        self.assertTrue(any("duplicates canonical" in item for item in findings.errors))

    def test_selected_candidate_requires_atomic_mechanism_mapping(self):
        data = valid_contract()
        data["mechanisms"] = []
        findings = rc.validate_contract(data)
        self.assertTrue(any("no atomic mechanism" in item for item in findings.errors))

    def test_unselected_source_cannot_support_implemented_mechanism(self):
        data = valid_contract()
        data["candidates"][0]["status"] = "exclude"
        data["record_management"]["records_included"] = 0
        findings = rc.validate_contract(data)
        self.assertTrue(any("unselected source" in item for item in findings.errors))

    def test_candidate_discovery_route_must_be_traceable(self):
        data = valid_contract()
        data["candidates"][0]["discovered_via"] = ["unknown-route"]
        findings = rc.validate_contract(data)
        self.assertTrue(any("unknown route" in item for item in findings.errors))

    def test_shallow_candidate_cannot_be_adapted(self):
        data = valid_contract()
        data["candidates"][0]["review_depth"] = "screened"
        findings = rc.validate_contract(data)
        self.assertTrue(any("without deep review" in item for item in findings.errors))

    def test_string_fake_evidence_is_blocked(self):
        data = valid_contract()
        data["mechanisms"][0]["positive_test"] = "passed trust me"
        findings = rc.validate_contract(data)
        self.assertTrue(any("positive_test must be an object" in item for item in findings.errors))

    def test_verified_file_evidence_checks_hash(self):
        data = valid_contract()
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "artifact.txt"
            artifact.write_text("evidence", encoding="utf-8")
            data["mechanisms"][0]["artifact"] = {
                "kind": "file",
                "locator": str(artifact),
                "status": "verified",
                "checked_at": CHECKED_AT,
                "sha256": "0" * 64,
                "note": "",
            }
            findings = rc.validate_contract(data, base_path=Path(directory))
        self.assertTrue(any("sha256 does not match" in item for item in findings.errors))

    def test_verified_file_requires_hash_even_without_base_path(self):
        data = valid_contract()
        data["mechanisms"][0]["artifact"] = {
            "kind": "file",
            "locator": "artifact.txt",
            "status": "verified",
            "checked_at": CHECKED_AT,
            "sha256": "",
        }
        findings = rc.validate_contract(data)
        self.assertTrue(any("sha256 is required" in item for item in findings.errors))

    def test_verified_url_evidence_requires_checker_and_method(self):
        data = valid_contract()
        data["mechanisms"][0]["positive_test"] = {
            "kind": "url",
            "locator": "https://example.org/result",
            "status": "verified",
            "checked_at": CHECKED_AT,
        }
        findings = rc.validate_contract(data)
        self.assertTrue(any("checked_by" in item for item in findings.errors))
        self.assertTrue(any("verification_method" in item for item in findings.errors))

    def test_verified_source_timestamps_require_utc_and_reject_future_values(self):
        naive = valid_contract()
        naive["candidates"][0]["source_identity"]["verified_at"] = "2020-01-01T00:00:00"
        findings = rc.validate_contract(naive)
        self.assertTrue(any("timezone-aware UTC" in item for item in findings.errors))

        future = valid_contract()
        future["candidates"][0]["source_snapshot"]["verified_at"] = "2999-01-01T00:00:00+00:00"
        findings = rc.validate_contract(future)
        self.assertTrue(any("cannot be in the future" in item for item in findings.errors))

    def test_search_and_evidence_times_require_timezone_and_cannot_be_future(self):
        data = valid_contract()
        data["source_classes"][0]["searched_at"] = "2020-01-01T12:00:00"
        data["query_families"][0]["executions"][0]["executed_at"] = "2099-01-01T00:00:00+00:00"
        data["mechanisms"][0]["evidence_location"]["checked_at"] = "2099-01-01"
        findings = rc.validate_contract(data)
        self.assertTrue(any("searched_at must be" in item for item in findings.errors))
        self.assertTrue(any("executed_at cannot be in the future" in item for item in findings.errors))
        self.assertTrue(any("checked_at cannot be in the future" in item for item in findings.errors))

    def test_contract_creation_cutoff_and_year_range_are_temporally_consistent(self):
        data = valid_contract()
        data["created_at"] = "2099-01-01"
        data["scope"]["cutoff_date"] = "2020-01-01"
        data["scope"]["year_range"] = {"from": 2022, "to": 2021}
        findings = rc.validate_contract(data)
        self.assertTrue(any("created_at cannot be in the future" in item for item in findings.errors))
        self.assertTrue(any("from cannot exceed" in item for item in findings.errors))
        self.assertTrue(any("to cannot exceed the cutoff year" in item for item in findings.errors))

    def test_malformed_nested_types_return_errors_not_exceptions(self):
        mutations = [
            ("scope", []),
            ("source_classes", [None, None, None]),
            ("query_families", [None, None, None, None]),
            ("candidates", [None]),
            ("mechanisms", [None]),
            ("gaps", [None]),
        ]
        for key, value in mutations:
            with self.subTest(key=key):
                data = valid_contract()
                data[key] = value
                findings = rc.validate_contract(data)
                self.assertTrue(findings.errors)

    def test_unhashable_nested_enum_values_return_errors_not_exceptions(self):
        mutations = [
            ("profile", lambda data: data.__setitem__("profile", [])),
            ("mode", lambda data: data.__setitem__("mode", {})),
            ("review_type", lambda data: data["scope"].__setitem__("review_type", [])),
            ("source_status", lambda data: data["source_classes"][0].__setitem__("status", {})),
            ("query_lane", lambda data: data["query_families"][0].__setitem__("lanes", [[]])),
            ("evidence_kind", lambda data: data["stop_rule"]["evidence"].__setitem__("kind", [])),
            ("candidate_type", lambda data: data["candidates"][0].__setitem__("type", {})),
            ("candidate_id", lambda data: data["candidates"][0].__setitem__("id", [])),
            ("candidate_status", lambda data: data["candidates"][0].__setitem__("status", [])),
            ("identity_kind", lambda data: data["candidates"][0]["source_identity"].__setitem__("kind", {})),
            ("snapshot_kind", lambda data: data["candidates"][0]["source_snapshot"].__setitem__("kind", [])),
            ("mechanism_source", lambda data: data["mechanisms"][0].__setitem__("source_id", {})),
            ("mechanism_decision", lambda data: data["mechanisms"][0].__setitem__("decision", [])),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                data = valid_contract()
                mutate(data)
                findings = rc.validate_contract(data)
                self.assertTrue(findings.errors)

    def test_completed_contract_survives_systematic_json_structure_mutation(self):
        base = valid_contract()
        base["scope"]["trend_requirement"] = "required"
        base["trend_discovery"] = self.completed_trend_discovery()
        replacement_values = (
            None,
            True,
            False,
            0,
            -1,
            1.5,
            "",
            " ",
            "x",
            [],
            [[]],
            [None, {}],
            {},
            {"x": []},
            10**100,
            float("nan"),
            float("inf"),
        )

        def paths(value, prefix=()):
            result = [prefix]
            if isinstance(value, dict):
                for key, child in value.items():
                    result.extend(paths(child, prefix + (key,)))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    result.extend(paths(child, prefix + (index,)))
            return result

        def resolve(root, path):
            value = root
            for step in path:
                value = value[step]
            return value

        def replace(root, path, value):
            parent = resolve(root, path[:-1])
            parent[path[-1]] = value

        all_paths = paths(base)
        for path in all_paths[1:]:
            for replacement in replacement_values:
                data = copy.deepcopy(base)
                replace(data, path, copy.deepcopy(replacement))
                try:
                    findings = rc.validate_contract(data)
                except Exception as exc:
                    self.fail(f"validator crashed after replacing {path!r} with {replacement!r}: {exc}")
                self.assertIsInstance(findings.errors, list)

        for path in all_paths:
            original = resolve(base, path)
            if isinstance(original, list):
                for addition in ([], {}):
                    data = copy.deepcopy(base)
                    resolve(data, path).append(copy.deepcopy(addition))
                    try:
                        findings = rc.validate_contract(data)
                    except Exception as exc:
                        self.fail(f"validator crashed after appending {addition!r} at {path!r}: {exc}")
                    self.assertIsInstance(findings.errors, list)

            if isinstance(original, dict):
                for key in original:
                    data = copy.deepcopy(base)
                    del resolve(data, path)[key]
                    try:
                        findings = rc.validate_contract(data)
                    except Exception as exc:
                        self.fail(f"validator crashed after deleting {path + (key,)!r}: {exc}")
                    self.assertIsInstance(findings.errors, list)

    def test_unbounded_claim_variants_are_blocked(self):
        phrases = [
            "We found all relevant projects.",
            "This review maps the complete landscape.",
            "No meaningful work was missed.",
            "This is the definitive map of the field.",
            "我们已经覆盖了全部重要项目。",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                findings = rc.Findings()
                rc.scan_unbounded_text(phrase, findings, "report")
                self.assertTrue(findings.errors)

    def test_bounded_disclaimer_is_allowed(self):
        findings = rc.Findings()
        rc.scan_unbounded_text("This is not proof that every relevant source was found.", findings, "report")
        self.assertEqual([], findings.errors)

    def test_template_is_not_a_false_pass(self):
        findings = rc.validate_contract(rc.template("P", "Q", "computing-software"))
        self.assertGreater(len(findings.errors), 10)

    def test_refresh_diff_detects_search_and_evidence_changes(self):
        old = valid_contract()
        new = copy.deepcopy(old)
        new["query_families"][0]["executions"][0]["exact_query"] = "different query"
        new["mechanisms"][0]["positive_test"]["locator"] = "different test"
        diff = rc.contract_diff(old, new)
        changed_sections = {item["section"] for item in diff["sections_changed"]}
        self.assertIn("query_families", changed_sections)
        self.assertEqual(["positive_test"], diff["mechanisms"]["changed"][0]["fields"])

    def test_v1_migration_does_not_trust_legacy_evidence(self):
        old = valid_contract()
        old["contract_version"] = 1
        old["candidates"][0]["source_snapshot"] = "version 1"
        old["mechanisms"][0]["positive_test"] = "passed"
        old["candidates"][0].pop("source_identity")
        old["candidates"][0]["url"] = "https://doi.org/10.1186/s13643-020-01542-z"
        migrated = rc.migrate_v1_to_v2(old)
        self.assertEqual(2, migrated["contract_version"])
        self.assertEqual("pending", migrated["mechanisms"][0]["positive_test"]["status"])
        self.assertEqual("pending", migrated["candidates"][0]["source_identity"]["status"])
        self.assertEqual("pending", migrated["candidates"][0]["source_snapshot"]["status"])

    def test_malformed_v1_migration_fails_with_controlled_error(self):
        data = {"contract_version": 1, "candidates": {}}
        with self.assertRaisesRegex(ValueError, "candidates must be a list"):
            rc.migrate_v1_to_v2(data)

    @mock.patch("research_contract.fetch_json")
    def test_github_verification_uses_official_metadata(self, fetch_json):
        fetch_json.return_value = ({
            "full_name": "openai/openai-python",
            "name": "openai-python",
            "html_url": "https://github.com/openai/openai-python",
            "description": "Official Python library",
            "archived": False,
            "fork": False,
            "visibility": "public",
            "default_branch": "main",
            "pushed_at": CHECKED_AT,
            "license": {"spdx_id": "Apache-2.0"},
        }, "https://api.github.com/repos/openai/openai-python")
        candidate = {
            "title": "openai-python",
            "source_identity": {"kind": "github", "value": "openai/openai-python"},
        }
        result = rc.verify_candidate_source(candidate, 1.0)
        self.assertEqual("verified", result["status"])
        self.assertEqual("github:openai/openai-python", result["canonical_id"])

    @mock.patch("research_contract.now_iso", return_value="2042-03-04T05:06:07+00:00")
    @mock.patch("research_contract.fetch_json")
    def test_source_verification_uses_runtime_utc_timestamp(self, fetch_json, _now_iso):
        fetch_json.return_value = ({
            "full_name": "openai/openai-python",
            "name": "openai-python",
            "html_url": "https://github.com/openai/openai-python",
            "description": "Official Python library",
            "archived": False,
            "fork": False,
            "visibility": "public",
            "default_branch": "main",
            "pushed_at": CHECKED_AT,
            "license": {"spdx_id": "Apache-2.0"},
        }, "https://api.github.com/repos/openai/openai-python")
        candidate = {
            "title": "openai-python",
            "source_identity": {"kind": "github", "value": "openai/openai-python"},
        }

        result = rc.verify_candidate_source(candidate, 1.0)

        self.assertEqual("2042-03-04T05:06:07+00:00", result["verified_at"])

    @mock.patch("research_contract.fetch_bytes")
    def test_github_commit_snapshot_is_verified(self, fetch_bytes):
        fetch_bytes.return_value = (
            ("a" * 40).encode("ascii"),
            "https://api.github.com/repos/openai/openai-python/commits/main",
            200,
        )
        snapshot = rc.verify_github_snapshot(
            "openai/openai-python",
            {"kind": "commit", "value": "main"},
            1.0,
        )
        self.assertEqual("verified", snapshot["status"])
        self.assertEqual("a" * 40, snapshot["canonical_value"])
        self.assertEqual("a" * 40, snapshot["value"])
        self.assertEqual("main", snapshot["requested_value"])

    @mock.patch("research_contract.fetch_json")
    def test_github_release_snapshot_pins_tag_object_id(self, fetch_json):
        fetch_json.side_effect = [
            (
                {"tag_name": "v1.2.3", "html_url": "https://github.com/example/repo/releases/tag/v1.2.3"},
                "https://api.github.com/repos/example/repo/releases/tags/v1.2.3",
            ),
            (
                {"ref": "refs/tags/v1.2.3", "object": {"sha": "b" * 40}, "url": "tag-api"},
                "https://api.github.com/repos/example/repo/git/ref/tags/v1.2.3",
            ),
        ]
        snapshot = rc.verify_github_snapshot(
            "example/repo",
            {"kind": "release", "value": "v1.2.3"},
            1.0,
        )
        self.assertEqual("verified", snapshot["status"])
        self.assertEqual("b" * 40, snapshot["canonical_value"])
        self.assertEqual("v1.2.3", snapshot["value"])

    @mock.patch("research_contract.fetch_bytes")
    def test_github_commit_snapshot_rejects_wrong_final_revision_and_object_id(self, fetch_bytes):
        fetch_bytes.return_value = (
            ("b" * 40).encode("ascii"),
            "https://api.github.com/repos/example/repo/commits/other",
            200,
        )
        with self.assertRaisesRegex(ValueError, "requested revision"):
            rc.verify_github_snapshot("example/repo", {"kind": "commit", "value": "main"}, 1.0)

        fetch_bytes.return_value = (
            ("b" * 40).encode("ascii"),
            "https://api.github.com/repos/example/repo/commits/" + "a" * 40,
            200,
        )
        with self.assertRaisesRegex(ValueError, "requested object ID"):
            rc.verify_github_snapshot("example/repo", {"kind": "commit", "value": "a" * 40}, 1.0)

    @mock.patch("research_contract.fetch_json")
    def test_github_tag_and_release_reject_wrong_object_identity(self, fetch_json):
        fetch_json.return_value = (
            {"ref": "refs/tags/other", "object": {"sha": "b" * 40}},
            "https://api.github.com/repos/example/repo/git/ref/tags/v1.2.3",
        )
        with self.assertRaisesRegex(ValueError, "requested tag"):
            rc.resolve_github_tag_ref("example/repo", "v1.2.3", 1.0)

        fetch_json.return_value = (
            {"tag_name": "other", "html_url": "https://github.com/example/repo/releases/tag/other"},
            "https://api.github.com/repos/example/repo/releases/tags/v1.2.3",
        )
        with self.assertRaisesRegex(ValueError, "requested release"):
            rc.verify_github_snapshot("example/repo", {"kind": "release", "value": "v1.2.3"}, 1.0)

    @mock.patch("research_contract.fetch_bytes")
    def test_github_commit_snapshot_rejects_malformed_object_id(self, fetch_bytes):
        fetch_bytes.return_value = (
            b'{"sha":"not-a-git-object"}',
            "https://api.github.com/repos/example/repo/commits/main",
            200,
        )
        with self.assertRaisesRegex(ValueError, "canonical Git object ID"):
            rc.verify_github_snapshot("example/repo", {"kind": "commit", "value": "main"}, 1.0)

    @mock.patch("research_contract.verify_candidate_source")
    def test_online_verification_detects_authoritative_title_change(self, verify_source):
        data = valid_contract()
        verify_source.return_value = {
            **data["candidates"][0]["source_identity"],
            "resolved_title": "Different authoritative title",
        }
        findings = rc.Findings()
        rc.validate_online_sources(data, findings, timeout=1.0, allow_official_url=False)
        self.assertTrue(any("resolved_title differs" in item for item in findings.errors))

    @mock.patch("research_contract.verify_candidate_snapshot")
    @mock.patch("research_contract.verify_candidate_source")
    def test_online_verification_detects_snapshot_drift(self, verify_source, verify_snapshot):
        data = valid_contract()
        verify_source.return_value = copy.deepcopy(data["candidates"][0]["source_identity"])
        verify_snapshot.return_value = {
            **data["candidates"][0]["source_snapshot"],
            "canonical_value": "different-version",
        }
        findings = rc.Findings()
        rc.validate_online_sources(data, findings, timeout=1.0, allow_official_url=False)
        self.assertTrue(any("canonical_value differs" in item for item in findings.errors))

    @mock.patch("research_contract.fetch_json", side_effect=ValueError("HTTP 404"))
    def test_fake_github_repository_fails_verification(self, _fetch_json):
        candidate = {
            "title": "invented repository",
            "source_identity": {"kind": "github", "value": "not-real/not-real"},
        }
        result = rc.verify_candidate_source(candidate, 1.0)
        self.assertEqual("failed", result["status"])
        self.assertIn("404", result["evidence"])

    def test_official_url_retrieval_requires_explicit_opt_in(self):
        candidate = {
            "title": "Official standard",
            "source_identity": {"kind": "official_url", "value": "https://example.org/standard"},
        }
        result = rc.verify_candidate_source(candidate, 1.0)
        self.assertEqual("failed", result["status"])
        self.assertIn("explicit --allow-official-url", result["evidence"])

    def test_private_official_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "localhost"):
            rc.ensure_public_http_url("https://localhost/private")

    def test_plain_http_official_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            rc.ensure_public_http_url("http://example.org/paper")

    def test_official_url_rejects_embedded_credentials(self):
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            rc.ensure_public_http_url("https://user:secret@example.org/paper")

    @mock.patch("research_contract.ensure_public_http_url")
    @mock.patch("research_contract.fetch_bytes")
    def test_public_official_url_without_title_uses_final_hostname(self, fetch_bytes, _ensure_public):
        fetch_bytes.return_value = (b'{"status":"ok"}', "https://EXAMPLE.org:443/metadata#section", 200)
        result = rc.verify_official_url("https://example.org/metadata", 1.0)
        self.assertEqual("example.org", result["resolved_title"])
        self.assertEqual("url:https://example.org/metadata", result["canonical_id"])
        self.assertEqual("https://example.org/metadata", result["canonical_url"])

    @mock.patch("research_contract.time.sleep")
    @mock.patch("research_contract.urllib.request.build_opener")
    def test_incomplete_network_read_becomes_controlled_failure(self, build_opener, _sleep):
        build_opener.return_value.open.side_effect = http.client.IncompleteRead(b"partial", 10)
        with self.assertRaisesRegex(ValueError, "network error"):
            rc.fetch_bytes("https://api.github.com/repos/openai/openai-python", 0.1, retries=1)

    @mock.patch("research_contract.urllib.request.build_opener")
    def test_metadata_fetch_rejects_non_https_url_before_opening(self, build_opener):
        for url in ("http://api.github.com/repos/openai/openai-python", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "credential-free HTTPS"):
                rc.fetch_bytes(url, 0.1, retries=0)
        build_opener.assert_not_called()

    @mock.patch.dict("os.environ", {"GITHUB_TOKEN": "secret-token"})
    def test_github_token_is_never_sent_to_other_hosts(self):
        crossref_headers = rc.request_headers("https://api.crossref.org/works/10.1/example")
        github_headers = rc.request_headers("https://api.github.com/repos/openai/openai-python")
        insecure_github_headers = rc.request_headers("http://api.github.com/repos/openai/openai-python")
        self.assertNotIn("Authorization", crossref_headers)
        self.assertNotIn("Authorization", insecure_github_headers)
        self.assertEqual("Bearer secret-token", github_headers["Authorization"])
        request = rc.urllib.request.Request(
            "https://api.github.com/repos/openai/openai-python",
            headers=github_headers,
        )
        redirected = rc.SafeMetadataRedirectHandler(public_only=False).redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://example.org/no-token",
        )
        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Authorization"))

        with self.assertRaisesRegex(ValueError, "credential-free HTTPS"):
            rc.SafeMetadataRedirectHandler(public_only=False).redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "http://api.github.com/repos/openai/openai-python",
            )

    @mock.patch.dict("os.environ", {"GITHUB_TOKEN": "secret-token\nInjected: value"})
    def test_github_token_control_characters_are_rejected_without_echoing_secret(self):
        with self.assertRaisesRegex(ValueError, "invalid control characters") as raised:
            rc.request_headers("https://api.github.com/repos/openai/openai-python")
        self.assertNotIn("secret-token", str(raised.exception))

    def test_authoritative_metadata_redirect_cannot_leave_allowed_https_host(self):
        request = rc.urllib.request.Request("https://api.github.com/repos/openai/openai-python")
        handler = rc.SafeMetadataRedirectHandler(
            public_only=False,
            allowed_hosts={"api.github.com"},
        )
        with self.assertRaisesRegex(ValueError, "left its authoritative HTTPS host"):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://example.org/forged-metadata",
            )
        with self.assertRaisesRegex(ValueError, "left its authoritative HTTPS host"):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "http://api.github.com/repos/openai/openai-python",
            )

    @mock.patch("research_contract.fetch_json")
    def test_github_verifier_rejects_wrong_final_repository_path(self, fetch_json):
        fetch_json.return_value = (
            {
                "full_name": "openai/openai-python",
                "name": "openai-python",
                "html_url": "https://github.com/openai/openai-python",
            },
            "https://api.github.com/repos/attacker/other",
        )
        with self.assertRaisesRegex(ValueError, "final URL"):
            rc.verify_github("openai/openai-python", 1.0)

    @mock.patch("research_contract.fetch_bytes")
    def test_arxiv_verifier_rejects_wrong_authoritative_final_url(self, fetch_bytes):
        fetch_bytes.return_value = (
            b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/1706.03762</id><title>Attention Is All You Need</title></entry></feed>''',
            "https://example.org/api/query?id_list=1706.03762",
            200,
        )
        with self.assertRaisesRegex(ValueError, "final URL"):
            rc.verify_arxiv("1706.03762", 1.0)

    @mock.patch("research_contract.fetch_json")
    def test_doi_verification_matches_title(self, fetch_json):
        fetch_json.return_value = ({
            "message": {
                "DOI": "10.1186/s13643-020-01542-z",
                "title": ["PRISMA-S: an extension to the PRISMA Statement for Reporting Literature Searches in Systematic Reviews"],
                "URL": "https://doi.org/10.1186/s13643-020-01542-z",
                "publisher": "Springer Science and Business Media LLC",
                "type": "journal-article",
            }
        }, "https://api.crossref.org/works/10.1186%2Fs13643-020-01542-z")
        candidate = {
            "title": "PRISMA-S: an extension to the PRISMA Statement for Reporting Literature Searches in Systematic Reviews",
            "source_identity": {"kind": "doi", "value": "10.1186/s13643-020-01542-z"},
        }
        result = rc.verify_candidate_source(candidate, 1.0)
        self.assertEqual("verified", result["status"])
        self.assertEqual("doi:10.1186/s13643-020-01542-z", result["canonical_id"])

    @mock.patch("research_contract.fetch_json")
    def test_title_match_ignores_only_case_whitespace_and_punctuation(self, fetch_json):
        fetch_json.return_value = ({
            "message": {
                "DOI": "10.1000/example",
                "title": ["A Study: Search, Memory & Agents"],
                "URL": "https://doi.org/10.1000/example",
            }
        }, "https://api.crossref.org/works/10.1000%2Fexample")
        candidate = {
            "title": "a study search memory agents",
            "source_identity": {"kind": "doi", "value": "10.1000/example"},
        }
        result = rc.verify_candidate_source(candidate, 1.0)
        self.assertEqual("verified", result["status"])
        self.assertEqual(1.0, result["title_match"])

    @mock.patch("research_contract.fetch_json")
    def test_real_identifier_with_wrong_title_is_rejected(self, fetch_json):
        fetch_json.return_value = ({
            "message": {
                "DOI": "10.1000/example",
                "title": ["A Real Paper About Search Reporting"],
                "URL": "https://doi.org/10.1000/example",
            }
        }, "https://api.crossref.org/works/10.1000%2Fexample")
        candidate = {
            "title": "Completely Unrelated Invented Claim",
            "source_identity": {"kind": "doi", "value": "10.1000/example"},
        }
        result = rc.verify_candidate_source(candidate, 1.0)
        self.assertEqual("failed", result["status"])
        self.assertIn("title mismatch", result["evidence"])

    @mock.patch("research_contract.fetch_json")
    def test_high_similarity_but_different_title_is_rejected(self, fetch_json):
        fetch_json.return_value = ({
            "message": {
                "DOI": "10.1000/example",
                "title": ["A Real Paper About Search Reporting"],
                "URL": "https://doi.org/10.1000/example",
            }
        }, "https://api.crossref.org/works/10.1000%2Fexample")
        candidate = {
            "title": "A Real Paper About Search Ranking",
            "source_identity": {"kind": "doi", "value": "10.1000/example"},
        }
        result = rc.verify_candidate_source(candidate, 1.0)
        self.assertGreater(result["title_match"], 0.58)
        self.assertEqual("failed", result["status"])

    @mock.patch("research_contract.fetch_json")
    def test_title_override_never_bypasses_authoritative_mismatch(self, fetch_json):
        fetch_json.return_value = ({
            "message": {
                "DOI": "10.1000/example",
                "title": ["A Real Paper About Search Reporting"],
                "URL": "https://doi.org/10.1000/example",
            }
        }, "https://api.crossref.org/works/10.1000%2Fexample")
        candidate = {
            "title": "Completely Unrelated Invented Claim",
            "source_identity": {
                "kind": "doi",
                "value": "10.1000/example",
                "title_match_override": {
                    "reason": "claimed translation",
                    "approved_by": "reviewer",
                },
            },
        }
        result = rc.verify_candidate_source(candidate, 1.0)
        self.assertEqual("failed", result["status"])

    def test_validated_identity_rejects_deprecated_title_override(self):
        data = valid_contract()
        data["candidates"][0]["source_identity"]["title_match_override"] = {
            "reason": "claimed translation",
            "approved_by": "reviewer",
        }
        findings = rc.validate_contract(data)
        self.assertTrue(any("title_match_override is unsupported" in item for item in findings.errors))

    def test_validation_recomputes_title_match_instead_of_trusting_stored_score(self):
        data = valid_contract()
        data["candidates"][0]["title"] = "Invented title"
        data["candidates"][0]["source_identity"]["title_match"] = 1.0
        findings = rc.validate_contract(data)
        self.assertTrue(any("title_match is inconsistent" in item for item in findings.errors))
        self.assertTrue(any("does not exactly match" in item for item in findings.errors))

    def test_github_normalization_accepts_repository_urls_but_not_ambiguous_bare_paths(self):
        self.assertEqual("openai/openai-python", rc.normalize_github("openai/openai-python"))
        self.assertEqual(
            "openai/openai-python",
            rc.normalize_github("https://github.com/openai/openai-python/issues/1?x=1"),
        )
        self.assertEqual(
            "openai/openai-python",
            rc.normalize_github("git@github.com:openai/openai-python.git"),
        )
        self.assertEqual("openai/openai-python/extra", rc.normalize_github("openai/openai-python/extra"))
        with self.assertRaisesRegex(ValueError, "owner/repository"):
            rc.verify_github("openai/openai-python/extra", 1.0)

    def test_renderer_includes_unresolved_gaps(self):
        rendered = rc.render_contract(valid_contract())
        self.assertIn("### Search execution", rendered)
        self.assertIn("### Record management", rendered)
        self.assertIn("publication\\_version", rendered)
        self.assertIn("Positive/failure evidence", rendered)
        self.assertIn("## 8. Residual risks and refresh trigger", rendered)
        self.assertIn("Private and unindexed work", rendered)
        self.assertIn("### Stop and refresh", rendered)

    def test_renderer_escapes_untrusted_markdown_in_table_cells(self):
        data = valid_contract()
        data["candidates"][0]["title"] = '<img src=x>|[spoof](https://example.org)'
        rendered = rc.render_contract(data)
        self.assertNotIn("<img src=x>", rendered)
        self.assertIn("&lt;img src=x&gt;", rendered)
        self.assertIn("\\|", rendered)
        self.assertIn("\\[spoof\\]", rendered)

    def test_renderer_escapes_untrusted_coverage_and_gap_html(self):
        data = valid_contract()
        data["coverage_statement"] = "<script>alert(1)</script> not proof that every source was found"
        data["gaps"][0]["detail"] = "<img src=x onerror=alert(1)>"
        rendered = rc.render_contract(data)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img src=x", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", rendered)

    def test_terminal_and_markdown_outputs_neutralize_control_and_bidi_characters(self):
        malicious = "safe\x1b[31m\u202Etxt"
        markdown = rc.markdown_cell(malicious)
        self.assertNotIn("\x1b", markdown)
        self.assertNotIn("\u202E", markdown)
        self.assertIn("\\\\u001B", markdown)
        self.assertIn("\\\\u202E", markdown)

        findings = rc.Findings()
        findings.error(malicious)
        output = io.StringIO()
        with redirect_stdout(output):
            rc.print_findings(findings)
        rendered = output.getvalue()
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\u202E", rendered)
        self.assertIn("\\u001B", rendered)
        self.assertIn("\\u202E", rendered)

    def test_verify_sources_neutralizes_untrusted_candidate_id_in_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            contract = Path(directory) / "contract.json"
            data = valid_contract()
            data["candidates"][0]["id"] = "S1\x1b[31m\u202E"
            rc.write_json_atomic(contract, data)
            identity = verified_identity()
            snapshot = data["candidates"][0]["source_snapshot"]
            args = rc.build_parser().parse_args(["verify-sources", str(contract)])
            output = io.StringIO()
            with mock.patch.object(rc, "verify_candidate_source", return_value=identity), mock.patch.object(
                rc,
                "verify_candidate_snapshot",
                return_value=snapshot,
            ), redirect_stdout(output):
                self.assertEqual(0, args.func(args))
            rendered = output.getvalue()
            self.assertNotIn("\x1b", rendered)
            self.assertNotIn("\u202E", rendered)
            self.assertIn("\\u001B", rendered)
            self.assertIn("\\u202E", rendered)

    def test_verified_file_evidence_cannot_escape_base_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "project"
            base.mkdir()
            outside = root / "outside.txt"
            outside.write_text("private", encoding="utf-8")
            item = evidence("../outside.txt", kind="file", status="verified")
            item["sha256"] = rc.sha256_file(outside)
            findings = rc.Findings()
            rc.validate_evidence_ref(item, findings, "evidence", base_path=base, minimum_status="verified")
            self.assertTrue(any("must stay within" in message for message in findings.errors))

    def test_verified_file_evidence_rejects_symlink_and_oversized_file(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target.txt"
            target.write_text("evidence", encoding="utf-8")
            link = base / "link.txt"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")
            linked = evidence("link.txt", kind="file", status="verified")
            linked["sha256"] = rc.sha256_file(target)
            findings = rc.Findings()
            rc.validate_evidence_ref(linked, findings, "evidence", base_path=base, minimum_status="verified")
            self.assertTrue(any("symbolic link" in message for message in findings.errors))

            regular = evidence("target.txt", kind="file", status="verified")
            regular["sha256"] = rc.sha256_file(target)
            with mock.patch.object(rc, "MAX_EVIDENCE_FILE_BYTES", 1):
                findings = rc.Findings()
                rc.validate_evidence_ref(regular, findings, "evidence", base_path=base, minimum_status="verified")
            self.assertTrue(any("evidence file exceeds" in message for message in findings.errors))

    def test_completed_optional_record_management_is_still_validated(self):
        data = valid_source_depth_contract()
        data["record_management"] = copy.deepcopy(valid_contract()["record_management"])
        data["record_management"]["records_screened"] = -1
        findings = rc.validate_contract(data)
        self.assertTrue(any("records_screened" in message for message in findings.errors))

    def test_candidate_cannot_claim_pending_chaining_route(self):
        data = valid_contract()
        data["candidates"][0]["discovered_via"] = ["chaining:forward"]
        findings = rc.validate_contract(data)
        self.assertTrue(any("completed chaining path" in message for message in findings.errors))

    def test_verified_identity_and_snapshot_must_be_internally_consistent(self):
        data = valid_contract()
        data["candidates"][0]["source_identity"]["canonical_id"] = "doi:10.0000/wrong"
        data["candidates"][0]["source_snapshot"]["canonical_value"] = "not-a-source-id"
        findings = rc.validate_contract(data)
        self.assertTrue(any("canonical_id is inconsistent" in message for message in findings.errors))
        self.assertTrue(any("canonical_value must match" in message for message in findings.errors))

    def test_selected_github_snapshot_requires_canonical_object_id(self):
        data = valid_contract()
        candidate = data["candidates"][0]
        candidate["title"] = "owner/repository"
        candidate["type"] = "github_repository"
        candidate["source_identity"] = {
            "kind": "github",
            "value": "owner/repository",
            "status": "verified",
            "verified_at": CHECKED_AT,
            "verification_method": "GitHub REST API",
            "canonical_id": "github:owner/repository",
            "canonical_url": "https://github.com/owner/repository",
            "resolved_title": "owner/repository",
            "title_match": 1.0,
            "evidence": "https://api.github.com/repos/owner/repository",
            "metadata": {"name": "repository"},
        }
        candidate["source_snapshot"] = {
            "kind": "commit",
            "value": "main",
            "status": "verified",
            "verified_at": CHECKED_AT,
            "canonical_value": "main",
            "evidence": "https://github.com/owner/repository/commit/main",
        }
        findings = rc.validate_contract(data)
        self.assertTrue(any("canonical Git object ID" in message for message in findings.errors))

    def test_github_snapshot_evidence_must_match_recorded_revision(self):
        data = valid_contract()
        candidate = data["candidates"][0]
        candidate["title"] = "owner/repository"
        candidate["type"] = "github_repository"
        candidate["source_identity"] = {
            "kind": "github",
            "value": "owner/repository",
            "status": "verified",
            "verified_at": CHECKED_AT,
            "verification_method": "GitHub REST API",
            "canonical_id": "github:owner/repository",
            "canonical_url": "https://github.com/owner/repository",
            "resolved_title": "owner/repository",
            "title_match": 1.0,
            "evidence": "https://api.github.com/repos/owner/repository",
            "metadata": {"name": "repository"},
        }
        candidate["source_snapshot"] = {
            "kind": "commit",
            "value": "a" * 40,
            "status": "verified",
            "verified_at": CHECKED_AT,
            "canonical_value": "a" * 40,
            "evidence": f"https://github.com/owner/repository/commit/{'b' * 40}",
        }
        findings = rc.validate_contract(data)
        self.assertTrue(any("must identify the recorded GitHub commit revision" in message for message in findings.errors))

        valid_snapshots = (
            {
                "kind": "commit",
                "value": "a" * 40,
                "canonical_value": "a" * 40,
                "evidence": f"https://github.com/owner/repository/commit/{'a' * 40}",
            },
            {
                "kind": "tag",
                "value": "v1.0.0",
                "canonical_value": "a" * 40,
                "evidence": "https://api.github.com/repos/owner/repository/git/refs/tags/v1.0.0",
            },
            {
                "kind": "release",
                "value": "v1.0.0",
                "canonical_value": "a" * 40,
                "evidence": "https://github.com/owner/repository/releases/tag/v1.0.0",
            },
        )
        for snapshot in valid_snapshots:
            with self.subTest(kind=snapshot["kind"]):
                candidate["source_snapshot"] = {
                    **snapshot,
                    "status": "verified",
                    "verified_at": CHECKED_AT,
                }
                self.assertEqual([], rc.validate_contract(data).errors)

    def test_contradictory_mechanism_decision_and_status_is_rejected(self):
        data = valid_contract()
        data["mechanisms"][0]["decision"] = "reject"
        data["mechanisms"][0]["rationale"] = "Not suitable."
        findings = rc.validate_contract(data)
        self.assertTrue(any("cannot be implemented or validated" in message for message in findings.errors))

    def test_coverage_statement_requires_string_and_explicit_boundary(self):
        data = valid_contract()
        data["coverage_statement"] = "The documented searches were completed."
        findings = rc.validate_contract(data)
        self.assertTrue(any("explicitly state" in message for message in findings.errors))
        data["coverage_statement"] = {"not": "text"}
        findings = rc.validate_contract(data)
        self.assertTrue(any("coverage_statement must be a string" in message for message in findings.errors))

    def test_duplicate_source_names_and_query_concepts_are_normalized(self):
        data = valid_contract()
        duplicate_source = copy.deepcopy(data["source_classes"][0])
        duplicate_source["name"] = "  SCHOLARLY   INDEX  "
        data["source_classes"].append(duplicate_source)
        data["query_families"][1]["concept"] = "  PROBLEM  "
        findings = rc.validate_contract(data)
        self.assertTrue(any("duplicate source-class name" in message for message in findings.errors))
        self.assertTrue(any("duplicate query-family concept" in message for message in findings.errors))

    def test_scalar_and_string_list_fields_are_type_checked(self):
        data = valid_contract()
        data["project"] = ["not", "text"]
        data["scope"]["languages"] = ["English", 3]
        findings = rc.validate_contract(data)
        self.assertTrue(any("project must be a non-empty string" in message for message in findings.errors))
        self.assertTrue(any("scope.languages entries must be non-empty strings" in message for message in findings.errors))

    def test_github_identity_rejects_embedded_credentials(self):
        with self.assertRaisesRegex(ValueError, "owner/repository"):
            rc.verify_github("https://token@github.com/owner/repository", 1.0)

    def test_pmid_normalization_accepts_canonical_forms_only(self):
        self.assertEqual("12345", rc.normalize_pmid("PMID: 12345"))
        self.assertEqual("12345", rc.normalize_pmid("https://pubmed.ncbi.nlm.nih.gov/12345/"))
        self.assertEqual("", rc.normalize_pmid("paper-12345-draft"))

    @mock.patch("research_contract.fetch_json")
    def test_malformed_pmid_metadata_is_a_controlled_failure(self, fetch_json):
        fetch_json.return_value = (
            {"result": []},
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=12345&retmode=json",
        )
        with self.assertRaisesRegex(ValueError, "PMID was not found"):
            rc.verify_pmid("12345", 1.0)

    def test_project_installer_rejects_symlinked_package_parent(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            external = root / "external"
            project.mkdir()
            external.mkdir()
            try:
                (project / ".agents").symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "symbolic-link path component"):
                installer.install("agents-project", project=project, force=False, source=SKILL_ROOT)
            self.assertEqual([], list(external.iterdir()))

    def test_renderer_contains_required_audit_sections_and_evidence(self):
        rendered = rc.render_contract(valid_contract())
        for heading in (
            "## 1. Question and scope",
            "## 2. Coverage and search execution",
            "## 3. Candidate appraisal",
            "## 4. Source-depth limits",
            "## 5. Atomic mechanism and translation matrix",
            "## 6. Implemented evidence and tests",
            "## 7. Deferred, rejected, unresolved, and unread items",
            "## 8. Residual risks and refresh trigger",
            "## 9. Bounded coverage statement",
        ):
            self.assertIn(heading, rendered)
        self.assertIn("changes route selection", rendered)
        self.assertIn("positive test result", rendered)
        self.assertIn("audit result", rendered)

    @mock.patch("research_contract.fetch_bytes")
    def test_arxiv_verification_rejects_dtd_and_entity_declarations(self, fetch_bytes):
        fetch_bytes.return_value = (
            b'<!DOCTYPE feed [<!ENTITY x "boom">]><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&x;</title></entry></feed>',
            "https://export.arxiv.org/api/query?id_list=2401.00001",
            200,
        )
        with self.assertRaisesRegex(ValueError, "unsafe DTD"):
            rc.verify_arxiv("2401.00001", 1.0)

    @mock.patch("research_contract.fetch_bytes")
    def test_arxiv_verification_parses_atom_without_elementtree(self, fetch_bytes):
        fetch_bytes.return_value = (
            b'''<?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry><id>https://arxiv.org/abs/1706.03762</id><title> Attention Is All You Need </title></entry>
            </feed>''',
            "https://export.arxiv.org/api/query?id_list=1706.03762",
            200,
        )
        result = rc.verify_arxiv("1706.03762", 1.0)
        self.assertEqual("Attention Is All You Need", result["resolved_title"])
        self.assertEqual("https://arxiv.org/abs/1706.03762", result["canonical_url"])

    def test_validator_renderer_and_migration_survive_random_json_shapes(self):
        # A fixed seed makes malformed-input coverage reproducible across audit runs.
        rng = random.Random(20260716)  # nosec B311

        def value(depth=0):
            scalars = [None, True, False, -1, 0, 1, 1.5, "", "text", "<script>"]
            if depth >= 3:
                return rng.choice(scalars)
            choice = rng.randrange(4)
            if choice == 0:
                return rng.choice(scalars)
            if choice == 1:
                return [value(depth + 1) for _ in range(rng.randrange(4))]
            return {f"k{index}": value(depth + 1) for index in range(rng.randrange(4))}

        keys = list(rc.template("x", "y", "computing-software").keys())
        for _ in range(500):
            payload = {key: value() for key in rng.sample(keys, rng.randrange(len(keys) + 1))}
            findings = rc.validate_contract(payload)
            self.assertIsInstance(findings.errors, list)
            self.assertIsInstance(rc.render_contract(payload), str)
            self.assertIsInstance(rc.contract_diff(payload, {"candidates": value(), "mechanisms": value()}), dict)

            legacy = {"contract_version": 1}
            for key in ("scope", "chaining", "stop_rule", "source_classes", "query_families", "candidates", "mechanisms"):
                if rng.random() < 0.5:
                    legacy[key] = value()
            try:
                migrated = rc.migrate_v1_to_v2(legacy)
            except ValueError:
                continue
            self.assertIsInstance(migrated, dict)

    def test_renderer_tolerates_invalid_collection_shapes_in_draft_mode(self):
        data = valid_contract()
        data["query_families"] = None
        data["candidates"] = {}
        data["mechanisms"] = "invalid"
        data["gaps"] = None
        data["created_at"] = {"binary": b"\xff", "non_finite": float("nan")}
        rendered = rc.render_contract(data)
        self.assertIn("### Search execution", rendered)
        self.assertIn("## 3. Candidate appraisal", rendered)
        self.assertIn("## 8. Residual risks and refresh trigger", rendered)

    def test_contract_reader_rejects_duplicate_keys_and_non_finite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text('{"contract_version": 2, "contract_version": 1}', encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "duplicate JSON key"):
                rc.load_json(path)
            path.write_text('{"contract_version": NaN}', encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "non-finite JSON number"):
                rc.load_json(path)

    @mock.patch("research_contract.fetch_bytes")
    def test_metadata_reader_rejects_duplicate_keys_and_non_finite_numbers(self, fetch_bytes):
        fetch_bytes.return_value = (b'{"value": 1, "value": 2}', "https://example.org", 200)
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            rc.fetch_json("https://example.org", 1.0)
        fetch_bytes.return_value = (b'{"value": Infinity}', "https://example.org", 200)
        with self.assertRaisesRegex(ValueError, "non-finite JSON number"):
            rc.fetch_json("https://example.org", 1.0)

    @mock.patch("research_contract.fetch_bytes")
    def test_metadata_reader_rejects_excessive_depth_and_node_count(self, fetch_bytes):
        nested = "0"
        for _ in range(rc.MAX_JSON_DEPTH + 2):
            nested = '{"x":' + nested + "}"
        fetch_bytes.return_value = (nested.encode("utf-8"), "https://example.org", 200)
        with self.assertRaisesRegex(ValueError, "metadata response exceeds JSON nesting depth"):
            rc.fetch_json("https://example.org", 1.0)

        fetch_bytes.return_value = (b'{"items":[0,1,2,3,4,5,6,7,8,9]}', "https://example.org", 200)
        with mock.patch.object(rc, "MAX_JSON_NODES", 5):
            with self.assertRaisesRegex(ValueError, "metadata response exceeds 5 JSON nodes"):
                rc.fetch_json("https://example.org", 1.0)

    def test_contract_and_report_readers_reject_symbolic_links(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "contract.json"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")
            with self.assertRaisesRegex(SystemExit, "symbolic link"):
                rc.load_json(link)
            findings = rc.Findings()
            self.assertEqual("", rc.read_report_text(link, findings))
            self.assertTrue(any("symbolic link" in item for item in findings.errors))

    def test_json_writer_and_canonical_hash_reject_non_finite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            with self.assertRaisesRegex(ValueError, "Out of range float values"):
                rc.write_json_atomic(path, {"value": float("nan")})
            with self.assertRaisesRegex(ValueError, "Out of range float values"):
                rc.canonical_hash({"value": float("inf")})

    def test_evidence_locator_must_be_lexically_inside_base(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base"
            base.mkdir()
            target = base / "evidence.txt"
            target.write_text("evidence", encoding="utf-8")
            alias = root / "outside-alias.txt"
            try:
                alias.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "lexically stay within"):
                rc.bounded_evidence_file(str(alias), base)

    def test_stable_ids_are_case_insensitively_unique(self):
        data = valid_contract()
        duplicate_candidate = copy.deepcopy(data["candidates"][0])
        duplicate_candidate["id"] = "s1"
        duplicate_candidate["status"] = "exclude"
        data["candidates"].append(duplicate_candidate)
        duplicate_mechanism = copy.deepcopy(data["mechanisms"][0])
        duplicate_mechanism["id"] = "m1"
        duplicate_mechanism["implementation_status"] = "planned"
        data["mechanisms"].append(duplicate_mechanism)
        data["query_families"][1]["id"] = "q1"
        findings = rc.validate_contract(data)
        self.assertTrue(any("duplicate query-family id" in item for item in findings.errors))
        self.assertTrue(any("duplicate candidate id" in item for item in findings.errors))
        self.assertTrue(any("duplicate mechanism id" in item for item in findings.errors))

    def test_portable_installer_bounds_and_strictly_decodes_instruction_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            agents = project / "AGENTS.md"
            agents.write_text("existing instructions", encoding="utf-8")
            with mock.patch.object(installer, "MAX_INSTRUCTION_BYTES", 1):
                with self.assertRaisesRegex(ValueError, "instruction file exceeds"):
                    installer.install("portable-project", project=project, force=False, source=SKILL_ROOT)
            self.assertFalse((project / ".agent-skills" / installer.SKILL_NAME).exists())

            agents.write_bytes(b"\xff\xfe")
            with self.assertRaisesRegex(UnicodeError, "UTF-8"):
                installer.install("portable-project", project=project, force=False, source=SKILL_ROOT)
            self.assertFalse((project / ".agent-skills" / installer.SKILL_NAME).exists())

    def test_portable_installer_does_not_overwrite_concurrent_instruction_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            agents = project / "AGENTS.md"
            agents.write_text("original\n", encoding="utf-8")
            real_stage = installer.stage_package

            def stage_then_edit(*args, **kwargs):
                staging = real_stage(*args, **kwargs)
                agents.write_text("concurrent edit\n", encoding="utf-8")
                return staging

            with mock.patch.object(installer, "stage_package", side_effect=stage_then_edit):
                with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
                    installer.install("portable-project", project=project, force=False, source=SKILL_ROOT)

            self.assertEqual("concurrent edit\n", agents.read_text(encoding="utf-8"))
            self.assertFalse((project / ".agent-skills" / installer.SKILL_NAME).exists())
            self.assertFalse((project / "GEMINI.md").exists())

    @mock.patch("research_contract.fetch_public_https_once")
    @mock.patch("research_contract.ensure_public_http_url")
    def test_public_url_fetch_connects_only_to_validated_addresses(self, ensure_public, fetch_once):
        ensure_public.side_effect = [["93.184.216.34"], ["203.0.113.10"]]
        fetch_once.side_effect = [
            (b"", 302, "https://docs.example.org/final"),
            (b"ok", 200, None),
        ]
        body, final_url, status = rc.fetch_public_https_bytes(
            "https://example.org/start",
            1.0,
            "text/html",
            max_bytes=100,
            retries=0,
        )
        self.assertEqual(b"ok", body)
        self.assertEqual("https://docs.example.org/final", final_url)
        self.assertEqual(200, status)
        self.assertEqual("93.184.216.34", fetch_once.call_args_list[0].args[1])
        self.assertEqual("203.0.113.10", fetch_once.call_args_list[1].args[1])

    @mock.patch("research_contract.socket.getaddrinfo")
    def test_public_url_resolution_rejects_any_private_address(self, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        with self.assertRaisesRegex(ValueError, "private, loopback"):
            rc.resolve_public_addresses("example.org", 443)

    @mock.patch("research_contract.fetch_public_https_once")
    @mock.patch("research_contract.ensure_public_http_url")
    def test_public_url_fetch_rejects_unvalidated_redirect(self, ensure_public, fetch_once):
        ensure_public.side_effect = [["93.184.216.34"], ValueError("private redirect")]
        fetch_once.return_value = (b"", 302, "https://127.0.0.1/private")
        with self.assertRaisesRegex(ValueError, "private redirect"):
            rc.fetch_public_https_bytes(
                "https://example.org/start",
                1.0,
                "text/html",
                max_bytes=100,
                retries=0,
            )

    def test_verified_identity_rejects_wrong_canonical_and_evidence_hosts(self):
        data = valid_contract()
        identity = data["candidates"][0]["source_identity"]
        identity["canonical_url"] = "https://example.org/not-the-doi"
        identity["evidence"] = "https://example.org/fabricated"
        findings = rc.validate_contract(data)
        self.assertTrue(any("canonical_url is inconsistent" in item for item in findings.errors))
        self.assertTrue(any("evidence must use an authoritative host" in item for item in findings.errors))

    def test_official_url_identity_requires_normalized_canonical_url(self):
        data = valid_contract()
        candidate = data["candidates"][0]
        candidate["type"] = "official_document"
        candidate["source_identity"] = {
            "kind": "official_url",
            "value": "https://example.org/report",
            "status": "verified",
            "verified_at": CHECKED_AT,
            "verification_method": "HTTP retrieval",
            "canonical_id": "url:https://EXAMPLE.org:443/report#section",
            "canonical_url": "https://EXAMPLE.org:443/report#section",
            "resolved_title": "Example source",
            "title_match": 1.0,
            "evidence": "HTTP 200",
        }
        candidate["source_snapshot"] = {
            "kind": "dated_access",
            "value": "https://example.org/report",
            "status": "verified",
            "verified_at": CHECKED_AT,
            "canonical_value": "https://example.org/report",
            "evidence": "HTTP 200",
        }
        findings = rc.validate_contract(data)
        self.assertTrue(any("must use normalized HTTPS host" in item for item in findings.errors))

    def test_official_url_snapshot_must_bind_to_verified_identity(self):
        data = valid_contract()
        candidate = data["candidates"][0]
        candidate["type"] = "official_document"
        candidate["source_identity"] = {
            "kind": "official_url",
            "value": "https://example.org/report",
            "status": "verified",
            "verified_at": CHECKED_AT,
            "verification_method": "HTTP retrieval",
            "canonical_id": "url:https://example.org/report",
            "canonical_url": "https://example.org/report",
            "resolved_title": "Example source",
            "title_match": 1.0,
            "evidence": "HTTP 200",
        }
        candidate["source_snapshot"] = {
            "kind": "dated_access",
            "value": "https://example.org/report",
            "status": "verified",
            "verified_at": CHECKED_AT,
            "canonical_value": "https://example.org/other",
            "evidence": "HTTP 204",
        }
        findings = rc.validate_contract(data)
        self.assertTrue(any("canonical_value must match the verified official URL" in item for item in findings.errors))
        self.assertTrue(any("evidence must match the verified official URL evidence" in item for item in findings.errors))

    def test_authoritative_evidence_must_identify_the_same_source(self):
        self.assertTrue(
            rc.evidence_identifies_source(
                "doi",
                "https://api.crossref.org/works/10.1186%2Fs13643-020-01542-z",
                "10.1186/s13643-020-01542-z",
            )
        )
        self.assertFalse(
            rc.evidence_identifies_source(
                "doi",
                "https://api.crossref.org/works/10.9999%2Fwrong",
                "10.1186/s13643-020-01542-z",
            )
        )
        self.assertTrue(
            rc.evidence_identifies_source(
                "arxiv",
                "https://export.arxiv.org/api/query?id_list=1706.03762",
                "1706.03762",
            )
        )
        self.assertFalse(
            rc.evidence_identifies_source(
                "arxiv",
                "https://export.arxiv.org/api/query?id_list=2401.00001",
                "1706.03762",
            )
        )
        self.assertTrue(
            rc.evidence_identifies_source(
                "pmid",
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=12345&retmode=json",
                "12345",
            )
        )
        self.assertFalse(
            rc.evidence_identifies_source(
                "github",
                "https://api.github.com/repos/owner/other",
                "owner/repository",
                "owner/repository",
            )
        )

        data = valid_contract()
        data["candidates"][0]["source_identity"]["evidence"] = "https://api.crossref.org/works/10.9999%2Fwrong"
        data["candidates"][0]["source_snapshot"]["evidence"] = "https://api.crossref.org/works/10.9999%2Fwrong"
        findings = rc.validate_contract(data)
        self.assertTrue(any("must identify the verified DOI" in item for item in findings.errors))

    @mock.patch("research_contract.fetch_json")
    def test_github_verifier_rejects_inconsistent_repository_url(self, fetch_json):
        fetch_json.return_value = (
            {
                "full_name": "owner/repository",
                "html_url": "https://github.com/owner/other",
                "name": "repository",
            },
            "https://api.github.com/repos/owner/repository",
        )
        with self.assertRaisesRegex(ValueError, "inconsistent repository URL"):
            rc.verify_github("owner/repository", 1.0)

    @mock.patch("research_contract.fetch_json")
    def test_doi_canonical_url_percent_encodes_url_delimiters(self, fetch_json):
        fetch_json.return_value = (
            {"message": {"DOI": "10.1234/example?part#fragment", "title": ["Example source"]}},
            "https://api.crossref.org/works/10.1234%2Fexample%3Fpart%23fragment",
        )
        result = rc.verify_doi("10.1234/example?part#fragment", 1.0)
        self.assertEqual("https://doi.org/10.1234/example%3Fpart%23fragment", result["canonical_url"])

    def test_selected_snapshot_evidence_must_bind_to_verified_identity(self):
        data = valid_contract()
        data["candidates"][0]["source_snapshot"]["evidence"] = "https://api.crossref.org/works/10.9999/wrong"
        findings = rc.validate_contract(data)
        self.assertTrue(any("must match the verified bibliographic identity evidence" in item for item in findings.errors))

    def test_expensive_text_fields_have_resource_limits(self):
        data = valid_contract()
        data["candidates"][0]["title"] = "x" * (rc.MAX_TITLE_CHARS + 1)
        data["query_families"][0]["executions"][0]["exact_query"] = "q" * (rc.MAX_QUERY_CHARS + 1)
        data["mechanisms"][0]["positive_test"]["locator"] = "l" * (rc.MAX_LOCATOR_CHARS + 1)
        findings = rc.validate_contract(data)
        self.assertTrue(any("title exceeds" in item for item in findings.errors))
        self.assertTrue(any("exact_query exceeds" in item for item in findings.errors))
        self.assertTrue(any("locator exceeds" in item for item in findings.errors))

    @mock.patch("research_contract.fetch_json")
    def test_doi_verifier_rejects_registry_identifier_mismatch(self, fetch_json):
        fetch_json.side_effect = [
            (
                {"message": {"DOI": "10.9999/wrong", "title": ["Wrong"]}},
                "https://api.crossref.org/works/10.1186%2Fs13643-020-01542-z",
            ),
            (
                {"data": {"id": "10.9999/wrong", "attributes": {"titles": [{"title": "Wrong"}]}}},
                "https://api.datacite.org/dois/10.1186%2Fs13643-020-01542-z",
            ),
        ]
        with self.assertRaisesRegex(ValueError, "identifier mismatch"):
            rc.verify_doi("10.1186/s13643-020-01542-z", 1.0)

    @mock.patch("research_contract.fetch_json")
    def test_datacite_doi_uses_doi_org_canonical_url(self, fetch_json):
        fetch_json.side_effect = [
            ValueError("Crossref unavailable"),
            ({
                "data": {
                    "id": "10.1186/s13643-020-01542-z",
                    "attributes": {
                        "url": "http://publisher.example/item",
                        "titles": [{"title": "Example source"}],
                    },
                },
            }, "https://api.datacite.org/dois/10.1186%2Fs13643-020-01542-z"),
        ]
        result = rc.verify_doi("10.1186/s13643-020-01542-z", 1.0)
        self.assertEqual("https://doi.org/10.1186/s13643-020-01542-z", result["canonical_url"])

    @mock.patch("research_contract.fetch_bytes")
    def test_arxiv_verifier_rejects_returned_identifier_mismatch(self, fetch_bytes):
        fetch_bytes.return_value = (
            b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/9999.99999</id><title>Wrong paper</title></entry></feed>''',
            "https://export.arxiv.org/api/query?id_list=1706.03762",
            200,
        )
        with self.assertRaisesRegex(ValueError, "identifier mismatch"):
            rc.verify_arxiv("1706.03762", 1.0)

    @mock.patch("research_contract.fetch_bytes")
    def test_arxiv_base_identity_accepts_and_records_current_version(self, fetch_bytes):
        fetch_bytes.return_value = (
            b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/1706.03762v7</id><title>Attention Is All You Need</title></entry></feed>''',
            "https://export.arxiv.org/api/query?id_list=1706.03762",
            200,
        )
        result = rc.verify_arxiv("1706.03762", 1.0)
        self.assertEqual("arxiv:1706.03762", result["canonical_id"])
        self.assertEqual("https://arxiv.org/abs/1706.03762", result["canonical_url"])
        self.assertEqual("1706.03762v7", result["metadata"]["resolved_version"])

    @mock.patch("research_contract.fetch_bytes")
    def test_arxiv_explicit_version_requires_exact_returned_version(self, fetch_bytes):
        fetch_bytes.return_value = (
            b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/1706.03762v7</id><title>Attention Is All You Need</title></entry></feed>''',
            "https://export.arxiv.org/api/query?id_list=1706.03762v1",
            200,
        )
        with self.assertRaisesRegex(ValueError, "identifier mismatch"):
            rc.verify_arxiv("1706.03762v1", 1.0)

    def test_strict_json_and_atom_parsers_survive_seeded_random_bytes(self):
        rng = random.Random(2026071604)  # nosec B311
        for _ in range(500):
            raw = bytes(rng.randrange(256) for _ in range(rng.randrange(256)))
            try:
                rc.strict_json_loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                pass
            raw = bytes(rng.randrange(256) for _ in range(rng.randrange(512)))
            try:
                rc.parse_arxiv_atom_entry(raw)
            except ValueError:
                pass


if __name__ == "__main__":
    unittest.main()
