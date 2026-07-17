#!/usr/bin/env python3
"""Create, verify, validate, diff, migrate, and render research contracts."""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.parsers.expat as expat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast


CONTRACT_VERSION = 2
GIT_OBJECT_ID_PATTERN = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?")
MAX_CONTRACT_BYTES = 20_000_000
MAX_REPORT_BYTES = 20_000_000
MAX_EVIDENCE_FILE_BYTES = 100_000_000
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 200_000
MAX_PUBLIC_REDIRECTS = 5
MAX_TITLE_CHARS = 5_000
MAX_LOCATOR_CHARS = 10_000
MAX_QUERY_CHARS = 100_000
STABLE_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}")
NO_EXPECTED_FILE_STATE = object()
FileState = tuple[int, int, int, int, int]
BIDI_AND_DIRECTIONAL_CONTROLS = {
    0x061C,
    0x200E,
    0x200F,
    0x2028,
    0x2029,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}

PROFILES = {
    "computing-software",
    "health-clinical",
    "human-subjects-social-science",
    "experimental-science-engineering",
    "education",
    "law-policy",
    "business-management",
    "humanities-languages-culture",
    "arts-design-media",
    "multidisciplinary",
}
MODES = {"landscape", "source-depth", "translate", "refresh", "audit", "full"}

CANDIDATE_STATUSES = {"include", "adapt", "monitor", "exclude", "unresolved"}
REVIEW_DEPTHS = {"discovered", "screened", "deep", "blocked"}
MECHANISM_DECISIONS = {"adopt", "adapt", "represented", "defer", "reject", "unverified"}
IMPLEMENTATION_STATUSES = {"planned", "implemented", "validated", "not_applicable", "blocked"}
FIT_LEVELS = {"high", "medium", "low", "not_applicable", "unknown"}
IDENTITY_KINDS = {"doi", "arxiv", "pmid", "github", "official_url", "other"}
IDENTITY_STATUSES = {"pending", "verified", "failed", "blocked", "not_applicable"}
SNAPSHOT_KINDS = {"commit", "tag", "release", "publication_version", "edition", "dataset_version", "standard_version", "dated_access"}
SNAPSHOT_STATUSES = {"pending", "verified", "failed", "blocked", "not_applicable"}
CANDIDATE_TYPES = {
    "paper",
    "preprint",
    "github_repository",
    "repository",
    "dataset",
    "standard",
    "official_document",
    "model",
    "package",
    "trial_registry",
    "law_policy_source",
    "grey_literature",
    "technical_blog",
    "community_discussion",
    "newsletter",
    "other",
}
TREND_REQUIREMENTS = {"not_requested", "monitor", "required"}
TREND_STATUSES = {"pending", "completed", "not_applicable", "blocked"}
TREND_SIGNAL_TYPES = {
    "repository_velocity",
    "release_activity",
    "package_or_model_adoption",
    "technical_blog_frequency",
    "community_attention",
    "newsletter_or_curated_visibility",
    "benchmark_visibility",
    "search_interest",
    "other",
}
TREND_CLAIM_LABELS = {"emerging", "popular", "fast_growing", "widely_discussed"}
SEED_SOURCE_TYPES = {
    "name",
    "url",
    "screenshot",
    "caption",
    "transcript",
    "paper_title",
    "spoken_description",
    "mixed",
    "other",
}
SEED_RETENTION_MODES = {"redacted", "verbatim", "not_retained"}
EVIDENCE_KINDS = {"file", "url", "command", "manual", "section", "dataset", "log", "note"}
EVIDENCE_STATUSES = {"pending", "observed", "verified", "failed", "blocked", "not_applicable"}
EVIDENCE_RANK = {
    "pending": 0,
    "failed": 0,
    "blocked": 0,
    "not_applicable": 0,
    "observed": 1,
    "verified": 2,
}
ALLOWED_EXCEPTION_CHECKS = {
    "minimum_source_classes",
    "minimum_query_families",
    "minimum_chaining_paths",
    "record_management",
}

MODE_REQUIREMENTS = {
    "landscape": {"sources": 3, "queries": 4, "chains": 2, "lanes": True, "records": True},
    "full": {"sources": 3, "queries": 4, "chains": 2, "lanes": True, "records": True},
    "refresh": {"sources": 2, "queries": 2, "chains": 1, "lanes": True, "records": True},
    "source-depth": {"sources": 1, "queries": 0, "chains": 0, "lanes": False, "records": False},
    "translate": {"sources": 1, "queries": 0, "chains": 0, "lanes": False, "records": False},
    "audit": {"sources": 1, "queries": 0, "chains": 0, "lanes": False, "records": False},
}

UNBOUNDED_PATTERNS = [
    r"\b(all|every)\s+(relevant|latest|available|important|meaningful)\s+(paper|papers|project|projects|source|sources|repository|repositories|work|works)\b",
    r"\b(exhaustive|complete|definitive)\s+(search|coverage|discovery|landscape|map|review)\b",
    r"\b(no|without)\s+(remaining\s+)?(gap|gaps|omission|omissions|blind spot|blind spots)\b",
    r"\bno\s+(meaningful|important|relevant)\s+(work|source|paper|project)s?\s+(was|were|has been|have been)?\s*missed\b",
    r"\bdefinitive\s+map\s+of\s+the\s+field\b",
    r"找到(?:了)?所有(?:最新|相关|重要)?(?:论文|项目|开源项目|来源|工作)",
    r"(?:没有|不存在)(?:任何)?(?:遗漏|盲区|缺口)",
    r"(?:全面|完整|穷尽)(?:检索|搜索|覆盖)(?:了)?(?:所有|全部)?",
    r"覆盖(?:了)?(?:全部|所有)(?:重要|相关|最新)?(?:论文|项目|来源|工作)",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def display_safe_text(value: Any, *, preserve_newlines: bool = False) -> str:
    """Make untrusted text visually explicit in terminals and generated Markdown."""
    output: list[str] = []
    for character in str(value):
        codepoint = ord(character)
        if character == "\n" and preserve_newlines:
            output.append(character)
        elif (
            codepoint < 0x20
            or 0x7F <= codepoint <= 0x9F
            or codepoint in BIDI_AND_DIRECTIONAL_CONTROLS
        ):
            width = 4 if codepoint <= 0xFFFF else 8
            output.append(f"\\u{codepoint:0{width}X}")
        else:
            output.append(character)
    return "".join(output)


def fsync_directory(path: Path) -> None:
    """Persist a directory entry update on POSIX filesystems that support it."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def evidence_template() -> dict[str, Any]:
    return {
        "kind": "note",
        "locator": "",
        "status": "pending",
        "checked_at": "",
        "sha256": "",
        "note": "",
    }


def identity_template() -> dict[str, Any]:
    return {
        "kind": "other",
        "value": "",
        "status": "pending",
        "verified_at": "",
        "verification_method": "",
        "canonical_id": "",
        "canonical_url": "",
        "resolved_title": "",
        "title_match": None,
        "evidence": "",
    }


def snapshot_template() -> dict[str, Any]:
    return {
        "kind": "dated_access",
        "value": "",
        "status": "pending",
        "verified_at": "",
        "canonical_value": "",
        "evidence": "",
    }


def seed_discovery_template() -> dict[str, Any]:
    return {
        "status": "not_applicable",
        "source_type": "",
        "platform": "",
        "source_locator": "",
        "shared_at": "",
        "seed_summary": "",
        "retention": "not_retained",
        "extraction_method": "",
        "extraction_confidence": None,
        "uncertain_variants": [],
        "source_evidence": evidence_template(),
        "mechanism_fingerprint": {
            "problem": "",
            "modalities": [],
            "core_mechanisms": [],
            "runtime_constraints": [],
            "claimed_evidence": [],
            "unresolved_claims": [],
        },
    }


def template(project: str, question: str, profile: str, mode: str = "full") -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "project": project,
        "question": question,
        "profile": profile,
        "mode": mode,
        "created_at": date.today().isoformat(),
        "scope": {
            "cutoff_date": date.today().isoformat(),
            "freshness_requirement": "current_as_of_cutoff",
            "review_type": "exploratory",
            "languages": [],
            "year_range": {"from": None, "to": date.today().year},
            "geography": [],
            "source_types": [],
            "constraints": [],
            "inclusion": [],
            "exclusion": [],
            "trend_requirement": "not_requested",
        },
        "search_lanes": {
            "direct_use": {"searched": False, "summary": ""},
            "mechanism_transfer": {"searched": False, "summary": ""},
        },
        "seed_discovery": seed_discovery_template(),
        "source_classes": [],
        "query_families": [],
        "record_management": {
            "status": "pending",
            "deduplication_method": "",
            "records_identified": 0,
            "duplicates_removed": 0,
            "records_screened": 0,
            "records_deep_reviewed": 0,
            "records_included": 0,
            "exclusion_reason_counts": {},
            "flow_evidence": evidence_template(),
        },
        "search_quality": {
            "strategy_peer_review": {
                "status": "not_applicable",
                "reviewer": "",
                "evidence": evidence_template(),
            },
            "publication_status_check": {
                "status": "pending",
                "checked_at": "",
                "evidence": evidence_template(),
            },
        },
        "trend_discovery": {
            "status": "not_applicable",
            "reason": "No popularity or emerging-topic sweep was requested.",
            "window_days": 90,
            "definition": "",
            "sources": [],
            "signals": [],
            "claims": [],
            "triangulation_rule": "",
            "evidence_policy": "discovery_only",
        },
        "chaining": {
            "backward": {"status": "pending", "evidence": evidence_template()},
            "forward": {"status": "pending", "evidence": evidence_template()},
            "related_projects": {"status": "pending", "evidence": evidence_template()},
            "authors_organizations": {"status": "pending", "evidence": evidence_template()},
            "benchmarks_competitors": {"status": "pending", "evidence": evidence_template()},
            "failures_corrections": {"status": "pending", "evidence": evidence_template()},
        },
        "candidates": [],
        "mechanisms": [],
        "gaps": [],
        "coverage_exceptions": [],
        "stop_rule": {"rule": "", "satisfied": False, "evidence": evidence_template()},
        "coverage_statement": "",
    }


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def normalized_label(value: str) -> str:
    return " ".join(value.split()).casefold()


def validate_string_list(
    value: Any,
    findings: Findings,
    path: str,
    *,
    require_items: bool,
) -> list[str]:
    items = expect_list(value, findings, path)
    if require_items:
        require(bool(items), findings, f"{path} must be a non-empty list")
    for item in items:
        require(nonempty_string(item), findings, f"{path} entries must be non-empty strings")
    return [item for item in items if nonempty_string(item)]


def valid_trend_signal_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and len(value) <= MAX_TITLE_CHARS
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def require(condition: bool, findings: Findings, message: str) -> None:
    if not condition:
        findings.error(message)


def is_choice(value: Any, choices: set[str]) -> bool:
    return isinstance(value, str) and value in choices


def expect_dict(value: Any, findings: Findings, path: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    findings.error(f"{path} must be an object")
    return {}


def expect_list(value: Any, findings: Findings, path: str) -> list[Any]:
    if isinstance(value, list):
        return value
    findings.error(f"{path} must be a list")
    return []


def dict_or_empty(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def valid_date_or_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None
    except ValueError:
        return False


def date_or_datetime_not_in_future(value: Any, *, tolerance_seconds: int = 300) -> bool:
    if not valid_date_or_datetime(value):
        return False
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text) <= date.today()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    return parsed <= datetime.now(timezone.utc) + timedelta(seconds=tolerance_seconds)


def valid_utc_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def utc_datetime_not_in_future(value: Any, *, tolerance_seconds: int = 300) -> bool:
    if not valid_utc_datetime(value):
        return False
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    return parsed <= datetime.now(timezone.utc) + timedelta(seconds=tolerance_seconds)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_state(info: os.stat_result) -> FileState:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
        getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)),
    )


def open_regular_file(path: Path, max_bytes: int, label: str) -> tuple[int, os.stat_result]:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} could not be inspected: {path}: {exc}") from exc
    if stat.S_ISLNK(initial.st_mode):
        raise ValueError(f"{label} must not be a symbolic link: {path}")
    if not stat.S_ISREG(initial.st_mode):
        raise ValueError(f"{label} must be a regular file: {path}")
    if initial.st_size > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} could not be opened safely: {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"{label} must be a regular file: {path}")
        if opened.st_size > max_bytes:
            raise ValueError(f"{label} exceeds {max_bytes} bytes: {path}")
        if file_state(initial) != file_state(opened):
            raise ValueError(f"{label} changed while it was being opened: {path}")
        return descriptor, opened
    except Exception:
        os.close(descriptor)
        raise


def read_regular_bytes_with_state(path: Path, max_bytes: int, label: str) -> tuple[bytes, FileState]:
    descriptor, opened = open_regular_file(path, max_bytes, label)
    try:
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise ValueError(f"{label} exceeds {max_bytes} bytes: {path}")
        final_state = file_state(os.fstat(descriptor))
        if file_state(opened) != final_state:
            raise ValueError(f"{label} changed while it was being read: {path}")
        return payload, final_state
    finally:
        os.close(descriptor)


def read_regular_bytes(path: Path, max_bytes: int, label: str) -> bytes:
    return read_regular_bytes_with_state(path, max_bytes, label)[0]


def sha256_regular_file(path: Path, max_bytes: int, label: str) -> str:
    descriptor, opened = open_regular_file(path, max_bytes, label)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise ValueError(f"{label} exceeds {max_bytes} bytes: {path}")
            digest.update(block)
        if file_state(opened) != file_state(os.fstat(descriptor)):
            raise ValueError(f"{label} changed while it was being hashed: {path}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def bounded_evidence_file(locator: str, base_path: Path) -> Path:
    lexical_base = Path(os.path.abspath(base_path.expanduser()))
    base = base_path.expanduser().resolve()
    requested = Path(locator).expanduser()
    candidate = requested if requested.is_absolute() else lexical_base / requested
    lexical = Path(os.path.abspath(candidate))
    if lexical.is_relative_to(lexical_base):
        lexical_anchor = lexical_base
    elif lexical.is_relative_to(base):
        lexical_anchor = base
    else:
        raise ValueError(f"evidence file must stay within --base and lexically stay within it: {locator}")
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"evidence file does not exist: {locator}") from exc
    if not resolved.is_relative_to(base):
        raise ValueError(f"evidence file must stay within --base: {locator}")
    current = lexical_anchor
    for component in lexical.relative_to(lexical_anchor).parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"evidence file path contains a symbolic link: {locator}")
    if not resolved.is_file():
        raise ValueError(f"evidence locator is not a regular file: {locator}")
    if resolved.stat().st_size > MAX_EVIDENCE_FILE_BYTES:
        raise ValueError(f"evidence file exceeds {MAX_EVIDENCE_FILE_BYTES} bytes: {locator}")
    return resolved


def validate_evidence_ref(
    value: Any,
    findings: Findings,
    path: str,
    *,
    base_path: Path | None,
    minimum_status: str = "observed",
) -> dict[str, Any]:
    evidence = expect_dict(value, findings, path)
    if not evidence:
        return evidence

    kind = evidence.get("kind")
    status = evidence.get("status")
    locator = evidence.get("locator")
    require(is_choice(kind, EVIDENCE_KINDS), findings, f"{path}.kind is invalid")
    require(is_choice(status, EVIDENCE_STATUSES), findings, f"{path}.status is invalid")
    require(nonempty_string(locator), findings, f"{path}.locator must be a non-empty string")
    if isinstance(locator, str):
        require(len(locator) <= MAX_LOCATOR_CHARS, findings, f"{path}.locator exceeds {MAX_LOCATOR_CHARS} characters")
    if is_choice(status, {"observed", "verified"}):
        require(valid_date_or_datetime(evidence.get("checked_at")), findings, f"{path}.checked_at must be an ISO date or timezone-aware date/time")
        require(date_or_datetime_not_in_future(evidence.get("checked_at")), findings, f"{path}.checked_at cannot be in the future")

    required_rank = EVIDENCE_RANK.get(minimum_status, 1)
    actual_rank = EVIDENCE_RANK.get(status, 0) if isinstance(status, str) else 0
    require(actual_rank >= required_rank, findings, f"{path}.status must be at least {minimum_status}")

    if kind == "file" and nonempty(locator):
        expected_hash = str(evidence.get("sha256") or "").lower()
        if actual_rank >= EVIDENCE_RANK["verified"]:
            require(bool(re.fullmatch(r"[0-9a-f]{64}", expected_hash)), findings, f"{path}.sha256 is required for verified file evidence")
        if base_path is not None:
            try:
                file_path = bounded_evidence_file(str(locator), base_path)
            except ValueError as exc:
                findings.error(f"{path}: {exc}")
            else:
                if re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                    try:
                        actual_hash = sha256_regular_file(file_path, MAX_EVIDENCE_FILE_BYTES, "evidence file")
                    except ValueError as exc:
                        findings.error(f"{path}: {exc}")
                    else:
                        require(actual_hash == expected_hash, findings, f"{path}.sha256 does not match the file")

    if kind == "command" and actual_rank >= EVIDENCE_RANK["verified"]:
        result_artifact = evidence.get("result_artifact")
        require(isinstance(result_artifact, dict), findings, f"{path}.result_artifact is required for verified command evidence")
        if isinstance(result_artifact, dict):
            validate_evidence_ref(
                result_artifact,
                findings,
                f"{path}.result_artifact",
                base_path=base_path,
                minimum_status="verified",
            )

    if kind == "manual" and actual_rank >= EVIDENCE_RANK["verified"]:
        require(nonempty_string(evidence.get("checked_by")), findings, f"{path}.checked_by must be a non-empty string for verified manual evidence")

    if is_choice(kind, {"url", "dataset", "section", "log", "note"}) and actual_rank >= EVIDENCE_RANK["verified"]:
        require(nonempty_string(evidence.get("checked_by")), findings, f"{path}.checked_by must be a non-empty string for verified {kind} evidence")
        require(nonempty_string(evidence.get("verification_method")), findings, f"{path}.verification_method must be a non-empty string for verified {kind} evidence")

    return evidence


def exception_names(data: dict[str, Any], findings: Findings) -> set[str]:
    exceptions = expect_list(data.get("coverage_exceptions", []), findings, "coverage_exceptions")
    names: set[str] = set()
    for index, raw in enumerate(exceptions):
        item = expect_dict(raw, findings, f"coverage_exceptions[{index}]")
        check = item.get("check")
        require(is_choice(check, ALLOWED_EXCEPTION_CHECKS), findings, f"coverage_exceptions[{index}].check is unsupported")
        require(nonempty_string(item.get("reason")), findings, f"coverage_exceptions[{index}].reason must be a non-empty string")
        require(nonempty_string(item.get("approved_by")), findings, f"coverage_exceptions[{index}].approved_by must be a non-empty string")
        require(nonempty_string(item.get("impact")), findings, f"coverage_exceptions[{index}].impact must be a non-empty string")
        if is_choice(check, ALLOWED_EXCEPTION_CHECKS) and nonempty_string(item.get("reason")) and nonempty_string(item.get("approved_by")):
            names.add(str(check))
    return names


def source_type_requires(candidate: dict[str, Any]) -> str | None:
    source_type = str(candidate.get("type") or "")
    if source_type in {"paper", "preprint"}:
        return "bibliographic"
    if source_type == "github_repository":
        return "github"
    return None


def credential_free_https_host(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None or parsed.password is not None:
        return ""
    return (parsed.hostname or "").lower()


def doi_canonical_url(value: str) -> str:
    doi = normalize_doi(value)
    return f"https://doi.org/{urllib.parse.quote(doi, safe='/:;()-.')}"


def evidence_identifies_source(kind: str, evidence: Any, value: str, resolved_repository: str = "") -> bool:
    if not isinstance(evidence, str):
        return False
    parsed = urllib.parse.urlparse(evidence)
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
        return False
    host = (parsed.hostname or "").lower()
    path = urllib.parse.unquote(parsed.path)
    if kind == "doi":
        prefixes = {"api.crossref.org": "/works/", "api.datacite.org": "/dois/"}
        prefix = prefixes.get(host)
        return prefix is not None and path.startswith(prefix) and normalize_doi(path[len(prefix):]) == normalize_doi(value)
    if kind == "arxiv":
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        identifiers = query.get("id_list", [])
        return (
            host == "export.arxiv.org"
            and path.rstrip("/") == "/api/query"
            and len(identifiers) == 1
            and normalize_arxiv(identifiers[0]).lower() == normalize_arxiv(value).lower()
        )
    if kind == "pmid":
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        return (
            host == "eutils.ncbi.nlm.nih.gov"
            and path.endswith("/entrez/eutils/esummary.fcgi")
            and query.get("db") == ["pubmed"]
            and query.get("id") == [normalize_pmid(value)]
        )
    if kind == "github":
        parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        return (
            host == "api.github.com"
            and len(parts) == 3
            and parts[0] == "repos"
            and f"{parts[1]}/{parts[2]}".lower() == resolved_repository.lower()
        )
    return False


def github_api_url_identifies_repository(value: Any, repository: str) -> bool:
    if not isinstance(value, str) or credential_free_https_host(value) != "api.github.com":
        return False
    parsed = urllib.parse.urlparse(value)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    return (
        len(parts) >= 3
        and parts[0] == "repos"
        and f"{parts[1]}/{parts[2]}".lower() == repository.lower()
    )


def github_api_url_matches_object_path(value: Any, repository: str, suffix: list[str]) -> bool:
    if not github_api_url_identifies_repository(value, repository):
        return False
    parsed = urllib.parse.urlparse(str(value))
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    return parts[3:] == suffix and not parsed.query and not parsed.fragment


def github_web_url_matches_object_path(value: Any, repository: str, suffix: list[str]) -> bool:
    if not isinstance(value, str) or credential_free_https_host(value) != "github.com":
        return False
    parsed = urllib.parse.urlparse(value)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    repository_parts = repository.split("/", 1)
    return (
        len(repository_parts) == 2
        and len(parts) >= 2
        and "/".join(parts[:2]).lower() == repository.lower()
        and parts[2:] == suffix
        and not parsed.query
        and not parsed.fragment
    )


def validate_source_snapshot(candidate: dict[str, Any], findings: Findings, path: str, selected: bool) -> dict[str, Any]:
    snapshot = expect_dict(candidate.get("source_snapshot"), findings, f"{path}.source_snapshot")
    kind = snapshot.get("kind")
    status = snapshot.get("status")
    require(is_choice(kind, SNAPSHOT_KINDS), findings, f"{path}.source_snapshot.kind is invalid")
    require(is_choice(status, SNAPSHOT_STATUSES), findings, f"{path}.source_snapshot.status is invalid")
    require(nonempty_string(snapshot.get("value")), findings, f"{path}.source_snapshot.value must be a non-empty string")
    if status == "verified":
        for key in ("verified_at", "canonical_value", "evidence"):
            require(nonempty_string(snapshot.get(key)), findings, f"{path}.source_snapshot.{key} must be a non-empty string when verified")
        require(valid_utc_datetime(snapshot.get("verified_at")), findings, f"{path}.source_snapshot.verified_at must be a timezone-aware UTC timestamp")
        require(utc_datetime_not_in_future(snapshot.get("verified_at")), findings, f"{path}.source_snapshot.verified_at cannot be in the future")
    if selected:
        require(status == "verified", findings, f"{path} cannot be selected until source snapshot is verified")
        requirement = source_type_requires(candidate)
        if requirement == "github":
            require(is_choice(kind, {"commit", "tag", "release"}), findings, f"{path} GitHub source must pin a verified commit, tag, or release")
            require(
                bool(GIT_OBJECT_ID_PATTERN.fullmatch(str(snapshot.get("canonical_value") or ""))),
                findings,
                f"{path}.source_snapshot.canonical_value must be a canonical Git object ID",
            )
            snapshot_host = credential_free_https_host(snapshot.get("evidence"))
            repository = normalize_github(str(dict_or_empty(candidate.get("source_identity")).get("resolved_title") or ""))
            snapshot_path = urllib.parse.urlparse(str(snapshot.get("evidence") or "")).path.lower()
            require(
                snapshot_host in {"github.com", "api.github.com"}
                and bool(repository)
                and f"/{repository.lower()}/" in snapshot_path,
                findings,
                f"{path}.source_snapshot.evidence must identify the verified GitHub repository",
            )
            evidence = snapshot.get("evidence")
            canonical_value = str(snapshot.get("canonical_value") or "").lower()
            requested_value = str(snapshot.get("value") or "")
            if kind == "commit":
                evidence_matches_snapshot = github_web_url_matches_object_path(
                    evidence,
                    repository,
                    ["commit", canonical_value],
                )
            elif kind == "tag":
                evidence_matches_snapshot = github_api_url_matches_object_path(
                    evidence,
                    repository,
                    ["git", "refs", "tags", requested_value],
                )
            else:
                evidence_matches_snapshot = github_web_url_matches_object_path(
                    evidence,
                    repository,
                    ["releases", "tag", requested_value],
                ) or github_api_url_matches_object_path(
                    evidence,
                    repository,
                    ["releases", "tags", requested_value],
                )
            require(
                evidence_matches_snapshot,
                findings,
                f"{path}.source_snapshot.evidence must identify the recorded GitHub {kind} revision",
            )
        elif requirement == "bibliographic":
            require(kind == "publication_version", findings, f"{path} paper source must pin a verified publication_version")
            identity = dict_or_empty(candidate.get("source_identity"))
            require(
                snapshot.get("evidence") == identity.get("evidence"),
                findings,
                f"{path}.source_snapshot.evidence must match the verified bibliographic identity evidence",
            )
        else:
            identity = dict_or_empty(candidate.get("source_identity"))
            if identity.get("kind") == "official_url":
                require(
                    kind == "dated_access",
                    findings,
                    f"{path} official URL source must use a verified dated_access snapshot",
                )
                require(
                    snapshot.get("canonical_value") == identity.get("canonical_url"),
                    findings,
                    f"{path}.source_snapshot.canonical_value must match the verified official URL",
                )
                require(
                    snapshot.get("evidence") == identity.get("evidence"),
                    findings,
                    f"{path}.source_snapshot.evidence must match the verified official URL evidence",
                )
    return snapshot


def validate_candidate_identity(candidate: dict[str, Any], findings: Findings, path: str, selected: bool) -> str:
    identity = expect_dict(candidate.get("source_identity"), findings, f"{path}.source_identity")
    kind = identity.get("kind")
    status = identity.get("status")
    require(is_choice(kind, IDENTITY_KINDS), findings, f"{path}.source_identity.kind is invalid")
    require(is_choice(status, IDENTITY_STATUSES), findings, f"{path}.source_identity.status is invalid")
    require(nonempty_string(identity.get("value")), findings, f"{path}.source_identity.value must be a non-empty string")

    if status == "verified":
        for key in ("verified_at", "verification_method", "canonical_id", "canonical_url", "resolved_title", "evidence"):
            require(nonempty_string(identity.get(key)), findings, f"{path}.source_identity.{key} must be a non-empty string when verified")
        if isinstance(identity.get("resolved_title"), str):
            require(
                len(identity["resolved_title"]) <= MAX_TITLE_CHARS,
                findings,
                f"{path}.source_identity.resolved_title exceeds {MAX_TITLE_CHARS} characters",
            )
        require(valid_utc_datetime(identity.get("verified_at")), findings, f"{path}.source_identity.verified_at must be a timezone-aware UTC timestamp")
        require(utc_datetime_not_in_future(identity.get("verified_at")), findings, f"{path}.source_identity.verified_at cannot be in the future")
        title_match = identity.get("title_match")
        require(isinstance(title_match, (int, float)) and not isinstance(title_match, bool), findings, f"{path}.source_identity.title_match must be numeric")
        if isinstance(title_match, (int, float)) and not isinstance(title_match, bool):
            require(0 <= title_match <= 1, findings, f"{path}.source_identity.title_match must be between 0 and 1")
            aliases = identity_title_aliases(identity)
            recomputed_match = title_similarity(str(candidate.get("title") or ""), aliases)
            require(
                math.isclose(float(title_match), recomputed_match, abs_tol=0.0001),
                findings,
                f"{path}.source_identity.title_match is inconsistent with the recorded authoritative title",
            )
            require(
                normalized_text(str(candidate.get("title") or "")) in {
                    normalized_text(alias) for alias in aliases if normalized_text(alias)
                },
                findings,
                f"{path}.source_identity title does not exactly match verified metadata after normalization",
            )
        require(
            "title_match_override" not in identity,
            findings,
            f"{path}.source_identity.title_match_override is unsupported; record the authoritative title instead",
        )
        canonical_id = str(identity.get("canonical_id") or "")
        value = str(identity.get("value") or "")
        if kind == "doi":
            expected_id = f"doi:{normalize_doi(value)}"
            require(canonical_id == expected_id, findings, f"{path}.source_identity.canonical_id is inconsistent with its DOI value")
        elif kind == "arxiv":
            expected_id = f"arxiv:{normalize_arxiv(value).lower()}"
            require(canonical_id == expected_id, findings, f"{path}.source_identity.canonical_id is inconsistent with its arXiv value")
        elif kind == "pmid":
            expected_id = f"pmid:{normalize_pmid(value)}"
            require(canonical_id == expected_id, findings, f"{path}.source_identity.canonical_id is inconsistent with its PMID value")
        elif kind == "github":
            resolved_repository = normalize_github(str(identity.get("resolved_title") or ""))
            require(
                bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", resolved_repository))
                and canonical_id == f"github:{resolved_repository.lower()}",
                findings,
                f"{path}.source_identity.canonical_id is inconsistent with its resolved GitHub identity",
            )
        if is_choice(kind, {"doi", "arxiv", "pmid", "github", "official_url"}):
            canonical_url_text = str(identity.get("canonical_url") or "")
            canonical_url = urllib.parse.urlparse(canonical_url_text)
            require(
                canonical_url.scheme == "https"
                and bool(canonical_url.netloc)
                and canonical_url.username is None
                and canonical_url.password is None,
                findings,
                f"{path}.source_identity.canonical_url must be credential-free HTTPS",
            )
            evidence_host = credential_free_https_host(identity.get("evidence"))
            if kind == "doi":
                require(
                    canonical_url_text.rstrip("/") == doi_canonical_url(value),
                    findings,
                    f"{path}.source_identity.canonical_url is inconsistent with its DOI identity",
                )
                require(
                    evidence_host in {"api.crossref.org", "api.datacite.org"},
                    findings,
                    f"{path}.source_identity.evidence must use an authoritative host for DOI verification",
                )
                require(
                    evidence_identifies_source("doi", identity.get("evidence"), value),
                    findings,
                    f"{path}.source_identity.evidence must identify the verified DOI",
                )
            elif kind == "arxiv":
                require(
                    (canonical_url.hostname or "").lower() == "arxiv.org"
                    and normalize_arxiv(canonical_url_text).lower() == normalize_arxiv(value).lower(),
                    findings,
                    f"{path}.source_identity.canonical_url is inconsistent with its arXiv identity",
                )
                require(
                    evidence_host == "export.arxiv.org",
                    findings,
                    f"{path}.source_identity.evidence must use an authoritative host for arXiv verification",
                )
                require(
                    evidence_identifies_source("arxiv", identity.get("evidence"), value),
                    findings,
                    f"{path}.source_identity.evidence must identify the verified arXiv record",
                )
            elif kind == "pmid":
                require(
                    canonical_url_text.rstrip("/") == f"https://pubmed.ncbi.nlm.nih.gov/{normalize_pmid(value)}",
                    findings,
                    f"{path}.source_identity.canonical_url is inconsistent with its PMID identity",
                )
                require(
                    evidence_host == "eutils.ncbi.nlm.nih.gov",
                    findings,
                    f"{path}.source_identity.evidence must use an authoritative host for PMID verification",
                )
                require(
                    evidence_identifies_source("pmid", identity.get("evidence"), value),
                    findings,
                    f"{path}.source_identity.evidence must identify the verified PMID",
                )
            elif kind == "github":
                require(
                    (canonical_url.hostname or "").lower() == "github.com"
                    and normalize_github(canonical_url_text).lower() == resolved_repository.lower(),
                    findings,
                    f"{path}.source_identity.canonical_url is inconsistent with its GitHub identity",
                )
                require(
                    evidence_host == "api.github.com",
                    findings,
                    f"{path}.source_identity.evidence must use an authoritative host for GitHub verification",
                )
                require(
                    evidence_identifies_source("github", identity.get("evidence"), value, resolved_repository),
                    findings,
                    f"{path}.source_identity.evidence must identify the verified GitHub repository",
                )
            elif kind == "official_url":
                try:
                    normalized_official_url = canonical_public_https_url(canonical_url_text)
                except ValueError:
                    normalized_official_url = ""
                require(
                    canonical_url_text == normalized_official_url,
                    findings,
                    f"{path}.source_identity.canonical_url must use normalized HTTPS host, port, path, and no fragment",
                )
                require(
                    str(identity.get("canonical_id") or "") == f"url:{canonical_url_text.rstrip('/')}",
                    findings,
                    f"{path}.source_identity.canonical_id is inconsistent with its official URL",
                )

    if selected:
        require(status == "verified", findings, f"{path} cannot be selected until source identity is verified")
        required = source_type_requires(candidate)
        if required == "bibliographic":
            require(is_choice(kind, {"doi", "arxiv", "pmid"}), findings, f"{path} paper identity must use DOI, arXiv, or PMID metadata")
        elif required == "github":
            require(kind == "github", findings, f"{path} GitHub repository must be verified through the GitHub API")

    return str(identity.get("canonical_id") or "") if status == "verified" else ""


def validate_seed_discovery(
    value: Any,
    findings: Findings,
    *,
    base_path: Path | None,
    user_seed_present: bool,
) -> None:
    if value is None and not user_seed_present:
        return
    seed = expect_dict(value, findings, "seed_discovery")
    status = seed.get("status")
    require(is_choice(status, {"not_applicable", "recorded"}), findings, "seed_discovery.status is invalid")
    if user_seed_present:
        require(status == "recorded", findings, "user_seed discovery routes require recorded seed_discovery provenance")
    if status != "recorded":
        return

    require(user_seed_present, findings, "seed_discovery.status=recorded requires a candidate discovered_via user_seed")
    require(is_choice(seed.get("source_type"), SEED_SOURCE_TYPES), findings, "seed_discovery.source_type is invalid")
    for key in ("platform", "seed_summary", "extraction_method"):
        require(nonempty_string(seed.get(key)), findings, f"seed_discovery.{key} must be a non-empty string")
    summary = seed.get("seed_summary")
    if isinstance(summary, str):
        require(len(summary) <= MAX_TITLE_CHARS, findings, f"seed_discovery.seed_summary exceeds {MAX_TITLE_CHARS} characters")
    source_locator = seed.get("source_locator")
    require(isinstance(source_locator, str), findings, "seed_discovery.source_locator must be a string")
    if isinstance(source_locator, str):
        require(len(source_locator) <= MAX_LOCATOR_CHARS, findings, f"seed_discovery.source_locator exceeds {MAX_LOCATOR_CHARS} characters")
    require(valid_date_or_datetime(seed.get("shared_at")), findings, "seed_discovery.shared_at must be an ISO date or timezone-aware date/time")
    require(date_or_datetime_not_in_future(seed.get("shared_at")), findings, "seed_discovery.shared_at cannot be in the future")
    require(is_choice(seed.get("retention"), SEED_RETENTION_MODES), findings, "seed_discovery.retention is invalid")
    confidence = seed.get("extraction_confidence")
    require(
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and math.isfinite(confidence)
        and 0.0 <= confidence <= 1.0,
        findings,
        "seed_discovery.extraction_confidence must be a finite number between 0 and 1",
    )
    validate_string_list(
        seed.get("uncertain_variants"),
        findings,
        "seed_discovery.uncertain_variants",
        require_items=False,
    )
    validate_evidence_ref(
        seed.get("source_evidence"),
        findings,
        "seed_discovery.source_evidence",
        base_path=base_path,
        minimum_status="observed",
    )
    fingerprint = expect_dict(seed.get("mechanism_fingerprint"), findings, "seed_discovery.mechanism_fingerprint")
    require(
        nonempty_string(fingerprint.get("problem")),
        findings,
        "seed_discovery.mechanism_fingerprint.problem must be a non-empty string",
    )
    validate_string_list(
        fingerprint.get("core_mechanisms"),
        findings,
        "seed_discovery.mechanism_fingerprint.core_mechanisms",
        require_items=True,
    )
    for key in ("modalities", "runtime_constraints", "claimed_evidence", "unresolved_claims"):
        validate_string_list(
            fingerprint.get(key),
            findings,
            f"seed_discovery.mechanism_fingerprint.{key}",
            require_items=False,
        )


def validate_contract(data: Any, base_path: Path | None = None) -> Findings:
    f = Findings()
    if not isinstance(data, dict):
        f.error("contract root must be a JSON object")
        return f

    exceptions = exception_names(data, f)
    require(data.get("contract_version") == CONTRACT_VERSION, f, f"contract_version must be {CONTRACT_VERSION}; run migrate for v1 contracts")
    require(nonempty_string(data.get("project")), f, "project must be a non-empty string")
    require(nonempty_string(data.get("question")), f, "question must be a non-empty string")
    require(is_choice(data.get("profile"), PROFILES), f, "profile is missing or unsupported")
    mode = data.get("mode")
    require(is_choice(mode, MODES), f, "mode is missing or unsupported")
    requirements = MODE_REQUIREMENTS.get(str(mode), MODE_REQUIREMENTS["full"])
    require(valid_date_or_datetime(data.get("created_at")), f, "created_at must be an ISO date or timezone-aware date/time")
    require(date_or_datetime_not_in_future(data.get("created_at")), f, "created_at cannot be in the future")

    scope = expect_dict(data.get("scope"), f, "scope")
    for key in ("cutoff_date", "freshness_requirement", "review_type"):
        require(nonempty_string(scope.get(key)), f, f"scope.{key} must be a non-empty string")
    if scope.get("cutoff_date"):
        require(valid_date_or_datetime(scope.get("cutoff_date")), f, "scope.cutoff_date must be an ISO date or timezone-aware date/time")
        require(date_or_datetime_not_in_future(scope.get("cutoff_date")), f, "scope.cutoff_date cannot be in the future")
    for key in ("languages", "source_types", "constraints", "inclusion", "exclusion"):
        validate_string_list(scope.get(key), f, f"scope.{key}", require_items=True)
    validate_string_list(scope.get("geography", []), f, "scope.geography", require_items=False)
    year_range = expect_dict(scope.get("year_range"), f, "scope.year_range")
    year_from = year_range.get("from")
    year_to = year_range.get("to")
    require(year_from is None or (isinstance(year_from, int) and not isinstance(year_from, bool)), f, "scope.year_range.from must be an integer or null")
    require(year_to is None or (isinstance(year_to, int) and not isinstance(year_to, bool)), f, "scope.year_range.to must be an integer or null")
    if isinstance(year_from, int) and not isinstance(year_from, bool):
        require(1 <= year_from <= 9999, f, "scope.year_range.from must be between 1 and 9999")
    if isinstance(year_to, int) and not isinstance(year_to, bool):
        require(1 <= year_to <= 9999, f, "scope.year_range.to must be between 1 and 9999")
    if isinstance(year_from, int) and not isinstance(year_from, bool) and isinstance(year_to, int) and not isinstance(year_to, bool):
        require(year_from <= year_to, f, "scope.year_range.from cannot exceed scope.year_range.to")
    cutoff_value = scope.get("cutoff_date")
    if valid_date_or_datetime(cutoff_value) and isinstance(year_to, int) and not isinstance(year_to, bool):
        require(year_to <= int(str(cutoff_value)[:4]), f, "scope.year_range.to cannot exceed the cutoff year")
    review_type = scope.get("review_type")
    require(is_choice(review_type, {"exploratory", "scoping", "systematic"}), f, "scope.review_type is invalid")
    trend_requirement = scope.get("trend_requirement", "not_requested")
    require(is_choice(trend_requirement, TREND_REQUIREMENTS), f, "scope.trend_requirement is invalid")

    lanes = expect_dict(data.get("search_lanes", {}), f, "search_lanes")
    for lane in ("direct_use", "mechanism_transfer"):
        lane_data = expect_dict(lanes.get(lane, {}), f, f"search_lanes.{lane}")
        if requirements["lanes"]:
            require(lane_data.get("searched") is True, f, f"search lane {lane} was not completed")
            require(nonempty_string(lane_data.get("summary")), f, f"search lane {lane} needs a non-empty string summary")

    sources = expect_list(data.get("source_classes"), f, "source_classes")
    minimum_sources = int(requirements["sources"])
    registered_source_names: set[str] = set()
    normalized_source_names: set[str] = set()
    searched_source_names: set[str] = set()
    for index, raw in enumerate(sources):
        prefix = f"source_classes[{index}]"
        source = expect_dict(raw, f, prefix)
        source_name = source.get("name")
        require(nonempty_string(source_name), f, f"{prefix}.name is required and must be a non-empty string")
        if isinstance(source_name, str) and source_name.strip():
            source_key = normalized_label(source_name)
            if source_key in normalized_source_names:
                f.error(f"duplicate source-class name: {source_name}")
            normalized_source_names.add(source_key)
            registered_source_names.add(source_name)
        status = source.get("status")
        require(is_choice(status, {"searched", "unavailable", "not_applicable"}), f, f"{prefix}.status is invalid")
        if status == "searched":
            if isinstance(source_name, str):
                searched_source_names.add(source_name)
            require(valid_date_or_datetime(source.get("searched_at")), f, f"{prefix}.searched_at must be an ISO date or timezone-aware date/time")
            require(date_or_datetime_not_in_future(source.get("searched_at")), f, f"{prefix}.searched_at cannot be in the future")
            require(nonempty_string(source.get("interface")), f, f"{prefix}.interface must be a non-empty string")
        else:
            require(nonempty_string(source.get("reason")), f, f"{prefix}.reason must be a non-empty string when not searched")
    if len(searched_source_names) < minimum_sources and "minimum_source_classes" not in exceptions:
        f.error(f"mode {mode} requires at least {minimum_sources} searched source class(es) or an approved minimum_source_classes exception")

    families = expect_list(data.get("query_families"), f, "query_families")
    minimum_queries = int(requirements["queries"])
    query_ids: set[str] = set()
    normalized_query_ids: set[str] = set()
    query_concepts: set[str] = set()
    query_lanes: set[str] = set()
    for index, raw in enumerate(families):
        prefix = f"query_families[{index}]"
        family = expect_dict(raw, f, prefix)
        query_id = family.get("id")
        require(nonempty_string(query_id), f, f"{prefix}.id is required and must be a non-empty string")
        if isinstance(query_id, str) and query_id.strip():
            require(bool(STABLE_ID_PATTERN.fullmatch(query_id)), f, f"{prefix}.id must be a stable identifier")
            normalized_query_id = query_id.casefold()
            if normalized_query_id in normalized_query_ids:
                f.error(f"duplicate query-family id: {query_id}")
            normalized_query_ids.add(normalized_query_id)
            query_ids.add(query_id)
        concept = family.get("concept")
        require(nonempty_string(concept), f, f"{prefix}.concept must be a non-empty string")
        if isinstance(concept, str) and concept.strip():
            concept_key = normalized_label(concept)
            if concept_key in query_concepts:
                f.error(f"duplicate query-family concept: {concept}")
            query_concepts.add(concept_key)
        lanes_for_family = expect_list(family.get("lanes"), f, f"{prefix}.lanes")
        require(bool(lanes_for_family), f, f"{prefix}.lanes is required")
        for lane in lanes_for_family:
            require(is_choice(lane, {"direct_use", "mechanism_transfer"}), f, f"{prefix}.lanes contains unsupported value {lane!r}")
            if isinstance(lane, str) and lane in {"direct_use", "mechanism_transfer"}:
                query_lanes.add(lane)
        executions = expect_list(family.get("executions"), f, f"{prefix}.executions")
        require(bool(executions), f, f"{prefix}.executions must record each exact search")
        total_results = 0
        for execution_index, raw_execution in enumerate(executions):
            execution_path = f"{prefix}.executions[{execution_index}]"
            execution = expect_dict(raw_execution, f, execution_path)
            source_name = execution.get("source")
            require(isinstance(source_name, str) and source_name in searched_source_names, f, f"{execution_path}.source must reference a searched source class")
            for key in ("interface", "exact_query"):
                require(nonempty_string(execution.get(key)), f, f"{execution_path}.{key} must be a non-empty string")
            if isinstance(execution.get("exact_query"), str):
                require(
                    len(execution["exact_query"]) <= MAX_QUERY_CHARS,
                    f,
                    f"{execution_path}.exact_query exceeds {MAX_QUERY_CHARS} characters",
                )
            require(valid_date_or_datetime(execution.get("executed_at")), f, f"{execution_path}.executed_at must be an ISO date or timezone-aware date/time")
            require(date_or_datetime_not_in_future(execution.get("executed_at")), f, f"{execution_path}.executed_at cannot be in the future")
            validate_string_list(execution.get("filters"), f, f"{execution_path}.filters", require_items=False)
            validate_string_list(execution.get("limits"), f, f"{execution_path}.limits", require_items=False)
            count = execution.get("results_count")
            require(isinstance(count, int) and not isinstance(count, bool), f, f"{execution_path}.results_count must be an integer")
            if isinstance(count, int) and not isinstance(count, bool):
                require(count >= 0, f, f"{execution_path}.results_count cannot be negative")
                total_results += max(0, count)
            validate_evidence_ref(
                execution.get("result_evidence"),
                f,
                f"{execution_path}.result_evidence",
                base_path=base_path,
                minimum_status="observed",
            )
        candidates_added = family.get("candidates_added")
        require(isinstance(candidates_added, int) and not isinstance(candidates_added, bool), f, f"{prefix}.candidates_added must be an integer")
        if isinstance(candidates_added, int) and not isinstance(candidates_added, bool):
            require(0 <= candidates_added <= total_results, f, f"{prefix}.candidates_added must be between 0 and total execution results")
    if len(query_concepts) < minimum_queries and "minimum_query_families" not in exceptions:
        f.error(f"mode {mode} requires at least {minimum_queries} distinct query family/families or an approved minimum_query_families exception")
    if requirements["lanes"]:
        for lane in ("direct_use", "mechanism_transfer"):
            require(lane in query_lanes, f, f"no query family is assigned to search lane {lane}")

    records = expect_dict(data.get("record_management"), f, "record_management")
    records_required = bool(requirements["records"] and "record_management" not in exceptions)
    if records_required:
        require(records.get("status") == "completed", f, "record_management.status must be completed")
    if records.get("status") == "completed":
        require(nonempty_string(records.get("deduplication_method")), f, "record_management.deduplication_method must be a non-empty string")
        count_fields = (
            "records_identified",
            "duplicates_removed",
            "records_screened",
            "records_deep_reviewed",
            "records_included",
        )
        counts: dict[str, int] = {}
        for key in count_fields:
            value = records.get(key)
            require(isinstance(value, int) and not isinstance(value, bool) and value >= 0, f, f"record_management.{key} must be a non-negative integer")
            if isinstance(value, int) and not isinstance(value, bool):
                counts[key] = value
        if len(counts) == len(count_fields):
            available = counts["records_identified"] - counts["duplicates_removed"]
            require(available >= 0, f, "record_management.duplicates_removed exceeds records_identified")
            require(counts["records_screened"] <= available, f, "record_management.records_screened exceeds deduplicated records")
            require(counts["records_deep_reviewed"] <= counts["records_screened"], f, "record_management.records_deep_reviewed exceeds screened records")
            require(counts["records_included"] <= counts["records_deep_reviewed"], f, "record_management.records_included exceeds deep-reviewed records")
        exclusion_counts = records.get("exclusion_reason_counts")
        require(isinstance(exclusion_counts, dict), f, "record_management.exclusion_reason_counts must be an object")
        if isinstance(exclusion_counts, dict):
            for reason, count in exclusion_counts.items():
                require(nonempty_string(reason), f, "record_management.exclusion_reason_counts keys must be non-empty strings")
                require(
                    isinstance(count, int) and not isinstance(count, bool) and count >= 0,
                    f,
                    f"record_management.exclusion_reason_counts[{reason!r}] must be a non-negative integer",
                )
        validate_evidence_ref(
            records.get("flow_evidence"),
            f,
            "record_management.flow_evidence",
            base_path=base_path,
            minimum_status="observed",
        )
    elif records and not is_choice(records.get("status"), {"pending", "completed", "not_applicable"}):
        f.error("record_management.status is invalid")

    search_quality = expect_dict(data.get("search_quality"), f, "search_quality")
    peer_review = expect_dict(search_quality.get("strategy_peer_review"), f, "search_quality.strategy_peer_review")
    peer_status = peer_review.get("status")
    require(is_choice(peer_status, {"completed", "pending", "not_applicable", "blocked"}), f, "search_quality.strategy_peer_review.status is invalid")
    if peer_status == "completed":
        require(nonempty_string(peer_review.get("reviewer")), f, "search_quality.strategy_peer_review.reviewer must be a non-empty string")
        validate_evidence_ref(
            peer_review.get("evidence"),
            f,
            "search_quality.strategy_peer_review.evidence",
            base_path=base_path,
        )
    if is_choice(review_type, {"scoping", "systematic"}):
        require(peer_status == "completed", f, f"{review_type} searches require recorded search-strategy peer review")

    publication_check = expect_dict(search_quality.get("publication_status_check"), f, "search_quality.publication_status_check")
    publication_status = publication_check.get("status")
    require(is_choice(publication_status, {"completed", "pending", "not_applicable", "blocked"}), f, "search_quality.publication_status_check.status is invalid")
    if publication_status == "completed":
        require(valid_date_or_datetime(publication_check.get("checked_at")), f, "search_quality.publication_status_check.checked_at must be an ISO date or timezone-aware date/time")
        require(date_or_datetime_not_in_future(publication_check.get("checked_at")), f, "search_quality.publication_status_check.checked_at cannot be in the future")
        validate_evidence_ref(
            publication_check.get("evidence"),
            f,
            "search_quality.publication_status_check.evidence",
            base_path=base_path,
        )

    chaining = expect_dict(data.get("chaining"), f, "chaining")
    completed_chains = 0
    completed_chain_names: set[str] = set()
    chain_names = ("backward", "forward", "related_projects", "authors_organizations", "benchmarks_competitors", "failures_corrections")
    for name in chain_names:
        item = expect_dict(chaining.get(name), f, f"chaining.{name}")
        status = item.get("status")
        require(is_choice(status, {"completed", "unavailable", "not_applicable", "pending"}), f, f"chaining.{name}.status is invalid")
        if status == "completed":
            completed_chains += 1
            completed_chain_names.add(name)
            validate_evidence_ref(item.get("evidence"), f, f"chaining.{name}.evidence", base_path=base_path)
        elif is_choice(status, {"unavailable", "not_applicable"}):
            require(nonempty_string(item.get("reason")), f, f"chaining.{name}.reason must be a non-empty string")
    minimum_chains = int(requirements["chains"])
    if completed_chains < minimum_chains and "minimum_chaining_paths" not in exceptions:
        f.error(f"mode {mode} requires at least {minimum_chains} completed chaining path(s) or an approved minimum_chaining_paths exception")

    candidates = expect_list(data.get("candidates"), f, "candidates")
    require(bool(candidates), f, "candidate ledger is empty")
    candidate_ids: set[str] = set()
    normalized_candidate_ids: set[str] = set()
    candidate_status_by_id: dict[str, str] = {}
    canonical_ids: dict[str, str] = {}
    candidate_trend_routes: list[tuple[int, str, str]] = []
    user_seed_present = False
    for index, raw in enumerate(candidates):
        prefix = f"candidates[{index}]"
        candidate = expect_dict(raw, f, prefix)
        candidate_id = candidate.get("id")
        require(nonempty_string(candidate_id), f, f"{prefix}.id is required and must be a non-empty string")
        if isinstance(candidate_id, str) and candidate_id.strip():
            require(bool(STABLE_ID_PATTERN.fullmatch(candidate_id)), f, f"{prefix}.id must be a stable identifier")
            normalized_candidate_id = candidate_id.casefold()
            if normalized_candidate_id in normalized_candidate_ids:
                f.error(f"duplicate candidate id: {candidate_id}")
            normalized_candidate_ids.add(normalized_candidate_id)
            candidate_ids.add(candidate_id)
        for key in ("title", "authority", "rationale"):
            require(nonempty_string(candidate.get(key)), f, f"{prefix}.{key} must be a non-empty string")
        if isinstance(candidate.get("title"), str):
            require(len(candidate["title"]) <= MAX_TITLE_CHARS, f, f"{prefix}.title exceeds {MAX_TITLE_CHARS} characters")
        require(is_choice(candidate.get("type"), CANDIDATE_TYPES), f, f"{prefix}.type is unsupported; use a controlled v2 candidate type")
        discovered_via = expect_list(candidate.get("discovered_via"), f, f"{prefix}.discovered_via")
        require(bool(discovered_via), f, f"{prefix}.discovered_via must be a non-empty list")
        for route in discovered_via:
            require(isinstance(route, str), f, f"{prefix}.discovered_via entries must be strings")
            if not isinstance(route, str):
                continue
            valid_route = route == "user_seed" or route in query_ids or route.startswith("trend:") or (
                route.startswith("chaining:") and route.split(":", 1)[1] in completed_chain_names
            )
            require(valid_route, f, f"{prefix}.discovered_via contains unknown route {route!r}")
            if route == "user_seed":
                user_seed_present = True
            if route.startswith("chaining:") and route.split(":", 1)[1] not in completed_chain_names:
                f.error(f"{prefix}.discovered_via must reference a completed chaining path: {route!r}")
            if route.startswith("trend:"):
                candidate_trend_routes.append((index, str(candidate_id), route.split(":", 1)[1]))
        require(is_choice(candidate.get("direct_use_fit"), FIT_LEVELS), f, f"{prefix}.direct_use_fit is invalid")
        require(is_choice(candidate.get("mechanism_fit"), FIT_LEVELS), f, f"{prefix}.mechanism_fit is invalid")
        status = candidate.get("status")
        require(is_choice(status, CANDIDATE_STATUSES), f, f"{prefix}.status is invalid")
        review_depth = candidate.get("review_depth")
        require(is_choice(review_depth, REVIEW_DEPTHS), f, f"{prefix}.review_depth is invalid")
        if isinstance(candidate_id, str) and isinstance(status, str) and status in CANDIDATE_STATUSES:
            candidate_status_by_id[candidate_id] = status
        selected = is_choice(status, {"include", "adapt"})
        identity_required = is_choice(status, {"include", "adapt", "monitor"})
        canonical_id = validate_candidate_identity(candidate, f, prefix, identity_required)
        snapshot = validate_source_snapshot(candidate, f, prefix, selected)
        if selected and source_type_requires(candidate) == "bibliographic":
            require(
                snapshot.get("canonical_value") == canonical_id,
                f,
                f"{prefix}.source_snapshot.canonical_value must match the verified bibliographic canonical_id",
            )
        if canonical_id:
            if canonical_id in canonical_ids:
                f.error(f"{prefix} duplicates canonical source identity used by {canonical_ids[canonical_id]}")
            canonical_ids[canonical_id] = str(candidate_id)
        if review_depth == "deep":
            reviewed = expect_list(candidate.get("reviewed"), f, f"{prefix}.reviewed")
            require(bool(reviewed), f, f"{prefix}.reviewed is required for deep review")
            for reviewed_index, reviewed_item in enumerate(reviewed):
                validate_evidence_ref(
                    reviewed_item,
                    f,
                    f"{prefix}.reviewed[{reviewed_index}]",
                    base_path=base_path,
                    minimum_status="observed",
                )
            validate_string_list(candidate.get("not_reviewed"), f, f"{prefix}.not_reviewed", require_items=False)
            validate_string_list(candidate.get("open_questions"), f, f"{prefix}.open_questions", require_items=False)
        if selected and review_depth != "deep":
            f.error(f"{prefix} cannot be {status} without deep review")

    validate_seed_discovery(
        data.get("seed_discovery"),
        f,
        base_path=base_path,
        user_seed_present=user_seed_present,
    )

    raw_trend = data.get("trend_discovery")
    if raw_trend is None and trend_requirement == "not_requested":
        trend = {"status": "not_applicable", "reason": "Legacy v2 contract without trend monitoring."}
    else:
        trend = expect_dict(raw_trend, f, "trend_discovery")
    trend_status = trend.get("status")
    require(is_choice(trend_status, TREND_STATUSES), f, "trend_discovery.status is invalid")
    if trend_requirement == "required":
        require(trend_status == "completed", f, "scope.trend_requirement=required needs completed trend_discovery")
    elif trend_requirement == "monitor" and trend_status != "completed":
        f.warning("trend monitoring was requested but trend_discovery is not completed")
    if is_choice(trend_status, {"not_applicable", "blocked"}):
        require(nonempty_string(trend.get("reason")), f, "trend_discovery.reason is required when not completed")
    if trend_status == "completed":
        window_days = trend.get("window_days")
        require(
            isinstance(window_days, int) and not isinstance(window_days, bool) and 1 <= window_days <= 3660,
            f,
            "trend_discovery.window_days must be an integer between 1 and 3660",
        )
        require(nonempty_string(trend.get("definition")), f, "trend_discovery.definition must define what popularity means")
        require(
            trend.get("evidence_policy") == "discovery_only",
            f,
            "trend_discovery.evidence_policy must be discovery_only; popularity is not evidence quality",
        )
        require(nonempty_string(trend.get("triangulation_rule")), f, "trend_discovery.triangulation_rule is required")
        trend_sources = expect_list(trend.get("sources"), f, "trend_discovery.sources")
        require(len(trend_sources) >= 2, f, "completed trend discovery requires at least two independent sources")
        trend_source_names: set[str] = set()
        normalized_trend_source_names: set[str] = set()
        trend_source_groups: dict[str, str] = {}
        normalized_trend_groups: set[str] = set()
        for source_index, raw_source in enumerate(trend_sources):
            source_path = f"trend_discovery.sources[{source_index}]"
            source = expect_dict(raw_source, f, source_path)
            source_name = source.get("name")
            require(nonempty_string(source_name), f, f"{source_path}.name must be a non-empty string")
            if isinstance(source_name, str) and source_name.strip():
                normalized_name = normalized_label(source_name)
                if normalized_name in normalized_trend_source_names:
                    f.error(f"duplicate trend source name: {source_name}")
                normalized_trend_source_names.add(normalized_name)
                trend_source_names.add(source_name)
            for key in ("interface", "exact_query"):
                require(nonempty_string(source.get(key)), f, f"{source_path}.{key} must be a non-empty string")
            independence_group = source.get("independence_group")
            require(nonempty_string(independence_group), f, f"{source_path}.independence_group must be a non-empty string")
            if isinstance(source_name, str) and isinstance(independence_group, str) and independence_group.strip():
                normalized_group = normalized_label(independence_group)
                trend_source_groups[source_name] = normalized_group
                normalized_trend_groups.add(normalized_group)
            require(valid_date_or_datetime(source.get("searched_at")), f, f"{source_path}.searched_at must be an ISO date or timezone-aware date/time")
            require(date_or_datetime_not_in_future(source.get("searched_at")), f, f"{source_path}.searched_at cannot be in the future")
            result_count = source.get("results_count")
            require(
                isinstance(result_count, int) and not isinstance(result_count, bool) and result_count >= 0,
                f,
                f"{source_path}.results_count must be a non-negative integer",
            )
            validate_evidence_ref(
                source.get("result_evidence"),
                f,
                f"{source_path}.result_evidence",
                base_path=base_path,
                minimum_status="observed",
            )
        require(len(normalized_trend_groups) >= 2, f, "completed trend discovery requires at least two independent source groups")
        trend_signals = expect_list(trend.get("signals"), f, "trend_discovery.signals")
        require(bool(trend_signals), f, "completed trend discovery requires at least one recorded signal")
        normalized_signal_ids: set[str] = set()
        signal_candidates: dict[str, str] = {}
        signal_groups: dict[str, str] = {}
        for signal_index, raw_signal in enumerate(trend_signals):
            signal_path = f"trend_discovery.signals[{signal_index}]"
            signal = expect_dict(raw_signal, f, signal_path)
            signal_id = signal.get("id")
            require(nonempty_string(signal_id), f, f"{signal_path}.id must be a non-empty string")
            if isinstance(signal_id, str) and signal_id.strip():
                require(bool(STABLE_ID_PATTERN.fullmatch(signal_id)), f, f"{signal_path}.id must be a stable identifier")
                normalized_signal_id = signal_id.casefold()
                if normalized_signal_id in normalized_signal_ids:
                    f.error(f"duplicate trend signal id: {signal_id}")
                normalized_signal_ids.add(normalized_signal_id)
                if isinstance(signal.get("candidate_id"), str):
                    signal_candidates[signal_id] = signal["candidate_id"]
                if isinstance(signal.get("source"), str) and signal["source"] in trend_source_groups:
                    signal_groups[signal_id] = trend_source_groups[signal["source"]]
            signal_source = signal.get("source")
            require(
                isinstance(signal_source, str) and signal_source in trend_source_names,
                f,
                f"{signal_path}.source must reference a trend source",
            )
            signal_candidate = signal.get("candidate_id")
            require(
                isinstance(signal_candidate, str) and signal_candidate in candidate_ids,
                f,
                f"{signal_path}.candidate_id must reference a candidate",
            )
            require(is_choice(signal.get("signal_type"), TREND_SIGNAL_TYPES), f, f"{signal_path}.signal_type is invalid")
            require(
                valid_trend_signal_value(signal.get("value")),
                f,
                f"{signal_path}.value must be a non-empty bounded string or finite number",
            )
            require(valid_date_or_datetime(signal.get("observed_at")), f, f"{signal_path}.observed_at must be an ISO date or timezone-aware date/time")
            require(date_or_datetime_not_in_future(signal.get("observed_at")), f, f"{signal_path}.observed_at cannot be in the future")
            validate_evidence_ref(
                signal.get("evidence"),
                f,
                f"{signal_path}.evidence",
                base_path=base_path,
                minimum_status="observed",
            )
        for candidate_index, candidate_id, signal_id in candidate_trend_routes:
            require(
                signal_candidates.get(signal_id) == candidate_id,
                f,
                f"candidates[{candidate_index}].discovered_via trend:{signal_id} must reference a completed signal for that candidate",
            )
        trend_claims = expect_list(trend.get("claims"), f, "trend_discovery.claims")
        normalized_claim_ids: set[str] = set()
        for claim_index, raw_claim in enumerate(trend_claims):
            claim_path = f"trend_discovery.claims[{claim_index}]"
            claim = expect_dict(raw_claim, f, claim_path)
            claim_id = claim.get("id")
            require(nonempty_string(claim_id), f, f"{claim_path}.id must be a non-empty string")
            if isinstance(claim_id, str) and claim_id.strip():
                require(bool(STABLE_ID_PATTERN.fullmatch(claim_id)), f, f"{claim_path}.id must be a stable identifier")
                normalized_claim_id = claim_id.casefold()
                if normalized_claim_id in normalized_claim_ids:
                    f.error(f"duplicate trend claim id: {claim_id}")
                normalized_claim_ids.add(normalized_claim_id)
            claim_candidate = claim.get("candidate_id")
            require(
                isinstance(claim_candidate, str) and claim_candidate in candidate_ids,
                f,
                f"{claim_path}.candidate_id must reference a candidate",
            )
            require(is_choice(claim.get("label"), TREND_CLAIM_LABELS), f, f"{claim_path}.label is invalid")
            require(nonempty_string(claim.get("boundary")), f, f"{claim_path}.boundary must be a non-empty string")
            signal_ids = validate_string_list(claim.get("signal_ids"), f, f"{claim_path}.signal_ids", require_items=True)
            require(len(set(signal_ids)) >= 2, f, f"{claim_path} requires at least two distinct signals")
            groups: set[str] = set()
            for signal_id in signal_ids:
                require(signal_id in signal_candidates, f, f"{claim_path}.signal_ids contains an unknown signal {signal_id!r}")
                require(
                    signal_candidates.get(str(signal_id)) == claim_candidate,
                    f,
                    f"{claim_path}.signal_ids must all reference the claimed candidate",
                )
                group = signal_groups.get(str(signal_id))
                if group:
                    groups.add(group)
            require(len(groups) >= 2, f, f"{claim_path} requires signals from at least two independent source groups")
    elif candidate_trend_routes:
        f.error("candidate trend discovery routes require completed trend_discovery")

    if records.get("status") == "completed":
        selected_count = sum(1 for item in candidates if isinstance(item, dict) and is_choice(item.get("status"), {"include", "adapt"}))
        require(records.get("records_included") == selected_count, f, "record_management.records_included must equal the number of include/adapt candidates")

    mechanisms = expect_list(data.get("mechanisms"), f, "mechanisms")
    require(bool(mechanisms), f, "mechanism/claim ledger is empty")
    mechanism_ids: set[str] = set()
    normalized_mechanism_ids: set[str] = set()
    for index, raw in enumerate(mechanisms):
        prefix = f"mechanisms[{index}]"
        mechanism = expect_dict(raw, f, prefix)
        mechanism_id = mechanism.get("id")
        require(nonempty_string(mechanism_id), f, f"{prefix}.id is required and must be a non-empty string")
        if isinstance(mechanism_id, str) and mechanism_id.strip():
            require(bool(STABLE_ID_PATTERN.fullmatch(mechanism_id)), f, f"{prefix}.id must be a stable identifier")
            normalized_mechanism_id = mechanism_id.casefold()
            if normalized_mechanism_id in normalized_mechanism_ids:
                f.error(f"duplicate mechanism id: {mechanism_id}")
            normalized_mechanism_ids.add(normalized_mechanism_id)
            mechanism_ids.add(mechanism_id)
        source_id = mechanism.get("source_id")
        require(isinstance(source_id, str) and source_id in candidate_ids, f, f"{prefix}.source_id does not match a candidate")
        for key in ("statement", "evidence_strength", "applicability", "claim_boundary"):
            require(nonempty_string(mechanism.get(key)), f, f"{prefix}.{key} must be a non-empty string")
        validate_evidence_ref(
            mechanism.get("evidence_location"),
            f,
            f"{prefix}.evidence_location",
            base_path=base_path,
            minimum_status="observed",
        )
        decision = mechanism.get("decision")
        implementation_status = mechanism.get("implementation_status")
        require(is_choice(decision, MECHANISM_DECISIONS), f, f"{prefix}.decision is invalid")
        require(is_choice(implementation_status, IMPLEMENTATION_STATUSES), f, f"{prefix}.implementation_status is invalid")
        if is_choice(decision, {"defer", "reject", "unverified"}):
            require(
                not is_choice(implementation_status, {"implemented", "validated"}),
                f,
                f"{prefix} decision {decision} cannot be implemented or validated",
            )
        if is_choice(decision, {"adopt", "adapt"}) or is_choice(implementation_status, {"implemented", "validated"}):
            require(
                isinstance(source_id, str) and candidate_status_by_id.get(source_id) in {"include", "adapt"},
                f,
                f"{prefix} cannot support adopted or implemented work from an unselected source",
            )
        if is_choice(decision, {"adopt", "adapt"}):
            require(nonempty_string(mechanism.get("translation")), f, f"{prefix}.translation must be a non-empty string")
        if is_choice(implementation_status, {"implemented", "validated"}):
            require(nonempty_string(mechanism.get("decision_effect")), f, f"{prefix}.decision_effect must be a non-empty string")
            minimum_status = "verified" if implementation_status == "validated" else "observed"
            for key in ("artifact", "positive_test", "failure_test", "audit_evidence"):
                validate_evidence_ref(
                    mechanism.get(key),
                    f,
                    f"{prefix}.{key}",
                    base_path=base_path,
                    minimum_status=minimum_status,
                )
        if is_choice(decision, {"defer", "reject", "unverified"}):
            require(nonempty_string(mechanism.get("rationale")), f, f"{prefix}.rationale must be a non-empty string")
    mechanism_source_ids = {
        item.get("source_id")
        for item in mechanisms
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    for index, raw in enumerate(candidates):
        if not isinstance(raw, dict):
            continue
        if is_choice(raw.get("status"), {"include", "adapt"}):
            candidate_id = raw.get("id")
            require(
                isinstance(candidate_id, str) and candidate_id in mechanism_source_ids,
                f,
                f"candidates[{index}] is selected but has no atomic mechanism/claim mapping",
            )

    gaps = expect_list(data.get("gaps", []), f, "gaps")
    for index, raw in enumerate(gaps):
        prefix = f"gaps[{index}]"
        gap = expect_dict(raw, f, prefix)
        for key in ("type", "detail", "impact", "mitigation", "status"):
            require(nonempty_string(gap.get(key)), f, f"{prefix}.{key} must be a non-empty string")

    stop = expect_dict(data.get("stop_rule"), f, "stop_rule")
    require(nonempty_string(stop.get("rule")), f, "stop_rule.rule must be a non-empty string")
    require(stop.get("satisfied") is True, f, "stop_rule is not satisfied")
    validate_evidence_ref(stop.get("evidence"), f, "stop_rule.evidence", base_path=base_path)

    statement = data.get("coverage_statement", "")
    require(nonempty_string(statement), f, "coverage_statement must be a string and is required")
    if isinstance(statement, str) and statement:
        scan_unbounded_text(statement, f, "coverage_statement")
        require(
            bool(re.search(r"(not proof|not exhaustive|cannot guarantee|不能证明|不能保证|并非穷尽|不是.*所有)", statement, re.I)),
            f,
            "coverage_statement must explicitly state that discovery completeness is not proven",
        )

    unresolved = [item for item in candidates if isinstance(item, dict) and item.get("status") == "unresolved"]
    if unresolved:
        f.warning(f"{len(unresolved)} candidate(s) remain unresolved and must be reported")
    unverified = [item for item in mechanisms if isinstance(item, dict) and item.get("decision") == "unverified"]
    if unverified:
        f.warning(f"{len(unverified)} mechanism(s) remain unverified and must be reported")
    if not gaps:
        f.warning("gap register is empty; confirm this is credible and do not claim no blind spots")
    selected_papers = [
        item for item in candidates
        if isinstance(item, dict)
        and is_choice(item.get("status"), {"include", "adapt"})
        and source_type_requires(item) == "bibliographic"
    ]
    if selected_papers and publication_status != "completed":
        f.warning("selected papers have not completed a recorded correction/retraction/publication-status check")
    return f


def scan_unbounded_text(text: str, findings: Findings, label: str) -> None:
    for pattern in UNBOUNDED_PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            prefix = text[max(0, match.start() - 120):match.start()].lower()
            if re.search(
                r"(?:not proof(?: that)?|cannot prove(?: that)?|does not prove(?: that)?|"
                r"do not claim(?: that)?|cannot guarantee(?: that)?|不能证明|不能保证|并非|不是|不得声称)\s*$",
                prefix,
            ):
                continue
            findings.error(f"{label} contains an unbounded completeness claim: {match.group(0)!r}")


def reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def strict_json_loads(payload: str) -> Any:
    return json.loads(
        payload,
        parse_constant=reject_json_constant,
        object_pairs_hook=unique_json_object,
    )


def enforce_json_complexity(value: Any, label: str) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ValueError(f"{label} exceeds {MAX_JSON_NODES} JSON nodes")
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"{label} exceeds JSON nesting depth {MAX_JSON_DEPTH}")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def load_json_with_state(path: Path) -> tuple[dict[str, Any], FileState]:
    try:
        raw, state = read_regular_bytes_with_state(path, MAX_CONTRACT_BYTES, "contract")
        data = strict_json_loads(raw.decode("utf-8"))
        enforce_json_complexity(data, "contract")
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"contract is not valid UTF-8: {display_safe_text(path)}: {display_safe_text(exc)}"
        ) from exc
    except (ValueError, RecursionError) as exc:
        raise SystemExit(f"invalid JSON in {display_safe_text(path)}: {display_safe_text(exc)}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"contract root must be a JSON object: {display_safe_text(path)}")
    return data, state


def load_json(path: Path) -> dict[str, Any]:
    return load_json_with_state(path)[0]


def current_regular_file_state(path: Path) -> FileState | None:
    if not path.exists() and not path.is_symlink():
        return None
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise OSError(f"refusing to atomically replace symbolic link: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"expected a regular output file: {path}")
    return file_state(info)


def write_text_atomic(
    path: Path,
    payload: str,
    *,
    default_mode: int,
    expected_state: FileState | None | object = NO_EXPECTED_FILE_STATE,
) -> FileState:
    path.parent.mkdir(parents=True, exist_ok=True)
    current_state = current_regular_file_state(path)
    if current_state is not None:
        current_info = path.lstat()
        if file_state(current_info) != current_state:
            raise RuntimeError(f"output file changed concurrently: {path}")
        mode = stat.S_IMODE(current_info.st_mode) & 0o777
    else:
        mode = default_mode
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        try:
            os.chmod(temporary, mode)
            handle = os.fdopen(descriptor, "w", encoding="utf-8")
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_state is not NO_EXPECTED_FILE_STATE and current_regular_file_state(path) != expected_state:
            raise RuntimeError(f"output file changed concurrently: {path}")
        os.replace(temporary, path)
        try:
            fsync_directory(path.parent)
        except OSError as exc:
            raise RuntimeError(
                f"output was replaced, but its parent directory could not be synchronized: {path}"
            ) from exc
        return cast(FileState, current_regular_file_state(path))
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(
    path: Path,
    data: dict[str, Any],
    *,
    expected_state: FileState | None | object = NO_EXPECTED_FILE_STATE,
) -> FileState:
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(payload.encode("utf-8")) > MAX_CONTRACT_BYTES:
        raise ValueError(f"contract exceeds {MAX_CONTRACT_BYTES} bytes: {path}")
    return write_text_atomic(path, payload, default_mode=0o600, expected_state=expected_state)


def request_headers(url: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json, application/atom+xml, text/html;q=0.9, */*;q=0.5",
        "User-Agent": "research-discovery-and-translation-audit/2.0",
    }
    token = os.environ.get("GITHUB_TOKEN")
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if token and parsed.scheme == "https" and hostname == "api.github.com":
        if any(ord(character) < 32 or ord(character) == 127 for character in token):
            raise ValueError("GITHUB_TOKEN contains invalid control characters")
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


class SafeMetadataRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, public_only: bool, allowed_hosts: set[str] | None = None) -> None:
        super().__init__()
        self.public_only = public_only
        self.allowed_hosts = {host.lower() for host in allowed_hosts} if allowed_hosts is not None else None

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if self.public_only:
            ensure_public_http_url(newurl)
        if self.allowed_hosts is not None:
            parsed_redirect = urllib.parse.urlparse(newurl)
            redirect_host = (parsed_redirect.hostname or "").lower()
            if (
                parsed_redirect.scheme != "https"
                or parsed_redirect.username is not None
                or parsed_redirect.password is not None
                or redirect_host not in self.allowed_hosts
            ):
                raise ValueError("metadata redirect left its authoritative HTTPS host")
        elif not self.public_only and not credential_free_https_host(newurl):
            raise ValueError("metadata redirect must remain on credential-free HTTPS")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old_host = (urllib.parse.urlparse(req.full_url).hostname or "").lower()
        parsed_new = urllib.parse.urlparse(newurl)
        new_host = (parsed_new.hostname or "").lower()
        if old_host != new_host or new_host != "api.github.com" or parsed_new.scheme != "https":
            redirected.remove_header("Authorization")
            redirected.remove_header("X-GitHub-Api-Version")
        return redirected


def fetch_bytes(
    url: str,
    timeout: float,
    accept: str | None = None,
    *,
    max_bytes: int = 2_000_000,
    retries: int = 2,
    public_only: bool = False,
    allowed_hosts: set[str] | None = None,
) -> tuple[bytes, str, int]:
    if public_only:
        return fetch_public_https_bytes(url, timeout, accept, max_bytes=max_bytes, retries=retries)
    initial_host = credential_free_https_host(url)
    if not initial_host:
        raise ValueError("metadata request must use a credential-free HTTPS URL")
    if allowed_hosts is not None:
        normalized_allowed_hosts = {host.lower() for host in allowed_hosts}
        if initial_host not in normalized_allowed_hosts:
            raise ValueError("metadata request does not use an allowed authoritative HTTPS host")
    headers = request_headers(url)
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - URL passed the HTTPS gate above.
    opener = urllib.request.build_opener(
        SafeMetadataRedirectHandler(public_only=public_only, allowed_hosts=allowed_hosts)
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with opener.open(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ValueError(f"metadata response exceeds {max_bytes} bytes: {url}")
                return body, response.geturl(), int(response.status)
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                raise ValueError(f"HTTP {exc.code} for {url}") from exc
            last_error = exc
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= retries:
                reason = getattr(exc, "reason", str(exc))
                raise ValueError(f"network error for {url}: {reason}") from exc
        time.sleep(0.25 * (2 ** attempt))
    raise ValueError(f"network error for {url}: {last_error}")


def fetch_json(
    url: str,
    timeout: float,
    *,
    allowed_hosts: set[str] | None = None,
) -> tuple[dict[str, Any], str]:
    body, final_url, _ = fetch_bytes(url, timeout, "application/json", allowed_hosts=allowed_hosts)
    try:
        data = strict_json_loads(body.decode("utf-8"))
        enforce_json_complexity(data, "metadata response")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"invalid JSON metadata from {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"metadata response from {url} is not an object")
    return data, final_url


def normalized_text(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def title_similarity(candidate_title: str, resolved_titles: list[str]) -> float:
    left = normalized_text(candidate_title)
    if not left:
        return 0.0
    best = 0.0
    for title in resolved_titles:
        right = normalized_text(title)
        if not right:
            continue
        score = difflib.SequenceMatcher(None, left, right).ratio()
        if left in right or right in left:
            score = max(score, min(len(left), len(right)) / max(len(left), len(right)))
        best = max(best, score)
    return round(best, 4)


def identity_title_aliases(identity: dict[str, Any]) -> list[str]:
    metadata = dict_or_empty(identity.get("metadata"))
    return [
        str(identity.get("resolved_title") or ""),
        str(metadata.get("name") or ""),
    ]


def normalize_doi(value: str) -> str:
    normalized = value.strip()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized, flags=re.I)
    normalized = re.sub(r"^doi:\s*", "", normalized, flags=re.I)
    return urllib.parse.unquote(normalized).strip().lower()


def normalize_arxiv(value: str) -> str:
    normalized = value.strip()
    normalized = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", normalized, flags=re.I)
    normalized = normalized.removesuffix(".pdf")
    normalized = re.sub(r"^arxiv:\s*", "", normalized, flags=re.I)
    return normalized.strip()


def normalize_pmid(value: str) -> str:
    normalized = value.strip()
    direct = re.fullmatch(r"(?:PMID:\s*)?(\d+)", normalized, re.I)
    if direct:
        return direct.group(1)
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme.lower() == "https" and (parsed.hostname or "").lower() == "pubmed.ncbi.nlm.nih.gov":
        match = re.fullmatch(r"/(\d+)/?", parsed.path)
        if match and not parsed.username and not parsed.password:
            return match.group(1)
    return ""


def normalize_github(value: str) -> str:
    normalized = re.sub(r"^git\+", "", value.strip(), flags=re.I)
    ssh_match = re.fullmatch(r"(?:git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?/?", normalized, re.I)
    if ssh_match:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}"

    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme or parsed.netloc:
        if parsed.username is not None or parsed.password is not None:
            return normalized
        if parsed.scheme.lower() not in {"http", "https"} or (parsed.hostname or "").lower() != "github.com":
            return normalized
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return "/".join(parts)
        return f"{parts[0]}/{parts[1].removesuffix('.git')}"

    normalized = normalized.strip("/").removesuffix(".git")
    return normalized


def verify_doi(value: str, timeout: float) -> dict[str, Any]:
    doi = normalize_doi(value)
    if not re.match(r"^10\.\d{4,9}/\S+$", doi):
        raise ValueError("invalid DOI syntax")
    crossref_url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
    try:
        data, final_url = fetch_json(crossref_url, timeout, allowed_hosts={"api.crossref.org"})
        if not evidence_identifies_source("doi", final_url, doi):
            raise ValueError("Crossref final URL does not identify the requested DOI")
        message = dict_or_empty(data.get("message"))
        if normalize_doi(str(message.get("DOI") or "")) != doi:
            raise ValueError("Crossref DOI identifier mismatch")
        titles = message.get("title")
        title = str(titles[0]) if isinstance(titles, list) and titles and isinstance(titles[0], str) else ""
        if not title:
            raise ValueError("Crossref record has no title")
        return {
            "canonical_id": f"doi:{doi}",
            "canonical_url": doi_canonical_url(doi),
            "resolved_title": title,
            "verification_method": "Crossref REST API",
            "evidence": crossref_url,
            "metadata": {"publisher": message.get("publisher"), "type": message.get("type")},
        }
    except ValueError:
        datacite_url = f"https://api.datacite.org/dois/{urllib.parse.quote(doi, safe='')}"
        data, final_url = fetch_json(datacite_url, timeout, allowed_hosts={"api.datacite.org"})
        if not evidence_identifies_source("doi", final_url, doi):
            raise ValueError("DataCite final URL does not identify the requested DOI")
        data_record = dict_or_empty(data.get("data"))
        if normalize_doi(str(data_record.get("id") or "")) != doi:
            raise ValueError("DataCite DOI identifier mismatch")
        attributes = dict_or_empty(data_record.get("attributes"))
        titles = attributes.get("titles")
        first_title = dict_or_empty(titles[0]) if isinstance(titles, list) and titles else {}
        title = str(first_title.get("title")) if nonempty_string(first_title.get("title")) else ""
        if not title:
            raise ValueError("DOI was not found with usable metadata in Crossref or DataCite")
        return {
            "canonical_id": f"doi:{doi}",
            "canonical_url": doi_canonical_url(doi),
            "resolved_title": title,
            "verification_method": "DataCite REST API",
            "evidence": datacite_url,
            "metadata": {"publisher": attributes.get("publisher"), "types": attributes.get("types")},
        }


def verify_arxiv(value: str, timeout: float) -> dict[str, Any]:
    arxiv_id = normalize_arxiv(value)
    if not re.match(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$", arxiv_id, re.I):
        raise ValueError("invalid arXiv identifier syntax")
    query_url = f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_id)}"
    body, final_url, _ = fetch_bytes(
        query_url,
        timeout,
        "application/atom+xml",
        allowed_hosts={"export.arxiv.org"},
    )
    if not evidence_identifies_source("arxiv", final_url, arxiv_id):
        raise ValueError("arXiv final URL does not identify the requested record")
    upper_body = body.upper()
    if b"<!DOCTYPE" in upper_body or b"<!ENTITY" in upper_body:
        raise ValueError("unsafe DTD or entity declaration in arXiv metadata response")
    title, canonical_url = parse_arxiv_atom_entry(body)
    if not title and not canonical_url:
        raise ValueError("arXiv identifier was not found")
    title = " ".join(title.split())
    returned_id = normalize_arxiv(canonical_url)
    requested_base = re.sub(r"v\d+$", "", arxiv_id, flags=re.I)
    returned_base = re.sub(r"v\d+$", "", returned_id, flags=re.I)
    requested_version = re.search(r"v\d+$", arxiv_id, flags=re.I)
    if returned_base.lower() != requested_base.lower() or (
        requested_version is not None and returned_id.lower() != arxiv_id.lower()
    ):
        raise ValueError("arXiv identifier mismatch")
    canonical_url = f"https://arxiv.org/abs/{arxiv_id}"
    if not title:
        raise ValueError("arXiv record has no title")
    return {
        "canonical_id": f"arxiv:{arxiv_id.lower()}",
        "canonical_url": canonical_url,
        "resolved_title": title,
        "verification_method": "arXiv API",
        "evidence": query_url,
        "metadata": {"resolved_version": returned_id},
    }


def parse_arxiv_atom_entry(body: bytes) -> tuple[str, str]:
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.ExternalEntityRefHandler = lambda *_args: 0
    entry_depth = 0
    finished_entry = False
    active_field = ""
    buffers: dict[str, list[str]] = {"title": [], "id": []}

    def local_name(name: str) -> str:
        return name.rsplit("}", 1)[-1]

    def start(name: str, _attributes: dict[str, str]) -> None:
        nonlocal entry_depth, active_field
        if finished_entry:
            return
        local = local_name(name)
        if local == "entry":
            entry_depth += 1
        elif entry_depth == 1 and local in buffers:
            active_field = local

    def text(data: str) -> None:
        if active_field and not finished_entry:
            buffers[active_field].append(data)

    def end(name: str) -> None:
        nonlocal entry_depth, finished_entry, active_field
        if finished_entry:
            return
        local = local_name(name)
        if active_field == local:
            active_field = ""
        if local == "entry" and entry_depth == 1:
            entry_depth = 0
            finished_entry = True

    parser.StartElementHandler = start
    parser.CharacterDataHandler = text
    parser.EndElementHandler = end
    try:
        parser.Parse(body, True)
    except expat.ExpatError as exc:
        raise ValueError("invalid arXiv metadata response") from exc
    return "".join(buffers["title"]), "".join(buffers["id"]).strip()


def verify_pmid(value: str, timeout: float) -> dict[str, Any]:
    pmid = normalize_pmid(value)
    if not pmid:
        raise ValueError("invalid PMID syntax")
    query_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "id": pmid, "retmode": "json"}
    )
    data, final_url = fetch_json(query_url, timeout, allowed_hosts={"eutils.ncbi.nlm.nih.gov"})
    if not evidence_identifies_source("pmid", final_url, pmid):
        raise ValueError("NCBI final URL does not identify the requested PMID")
    record = dict_or_empty(dict_or_empty(data.get("result")).get(pmid))
    if not isinstance(record, dict) or not record.get("title"):
        raise ValueError("PMID was not found")
    return {
        "canonical_id": f"pmid:{pmid}",
        "canonical_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "resolved_title": str(record["title"]),
        "verification_method": "NCBI E-utilities",
        "evidence": query_url,
        "metadata": {"source": record.get("source"), "pubdate": record.get("pubdate")},
    }


def verify_github(value: str, timeout: float) -> dict[str, Any]:
    repository = normalize_github(value)
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repository):
        raise ValueError("GitHub identity must be owner/repository or a GitHub repository URL")
    api_url = f"https://api.github.com/repos/{repository}"
    data, final_url = fetch_json(api_url, timeout, allowed_hosts={"api.github.com"})
    full_name = data.get("full_name")
    html_url = data.get("html_url")
    if not isinstance(full_name, str) or not full_name.strip() or not isinstance(html_url, str) or not html_url.strip():
        raise ValueError("GitHub API response is missing repository identity")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
        raise ValueError("GitHub API response contains an invalid repository identity")
    if normalize_github(html_url).lower() != full_name.lower() or credential_free_https_host(html_url) != "github.com":
        raise ValueError("GitHub API response contains an inconsistent repository URL")
    if not (
        github_api_url_identifies_repository(final_url, repository)
        or github_api_url_identifies_repository(final_url, full_name)
    ):
        raise ValueError("GitHub final URL does not identify the requested or resolved repository")
    canonical_api_url = "https://api.github.com/repos/" + "/".join(
        urllib.parse.quote(part, safe="") for part in full_name.split("/", 1)
    )
    license_info = dict_or_empty(data.get("license"))
    return {
        "canonical_id": f"github:{full_name.lower()}",
        "canonical_url": html_url,
        "resolved_title": full_name,
        "verification_method": "GitHub REST API",
        "evidence": canonical_api_url,
        "metadata": {
            "name": data.get("name"),
            "description": data.get("description"),
            "archived": data.get("archived"),
            "fork": data.get("fork"),
            "visibility": data.get("visibility"),
            "default_branch": data.get("default_branch"),
            "pushed_at": data.get("pushed_at"),
            "license_spdx": license_info.get("spdx_id"),
        },
    }


def resolve_github_tag_ref(repository: str, tag: str, timeout: float) -> tuple[str, str]:
    quoted_repository = "/".join(urllib.parse.quote(part, safe="") for part in repository.split("/", 1))
    api_url = f"https://api.github.com/repos/{quoted_repository}/git/ref/tags/{urllib.parse.quote(tag, safe='')}"
    data, final_url = fetch_json(api_url, timeout, allowed_hosts={"api.github.com"})
    if not github_api_url_matches_object_path(final_url, repository, ["git", "ref", "tags", tag]):
        raise ValueError("GitHub tag final URL does not identify the requested tag")
    if data.get("ref") != f"refs/tags/{tag}":
        raise ValueError("GitHub tag metadata does not identify the requested tag")
    target = dict_or_empty(data.get("object"))
    canonical_value = str(target.get("sha") or "").lower()
    if not GIT_OBJECT_ID_PATTERN.fullmatch(canonical_value):
        raise ValueError("GitHub tag metadata did not contain a canonical Git object ID")
    return canonical_value, str(data.get("url") or api_url)


def verify_github_snapshot(repository: str, snapshot: dict[str, Any], timeout: float) -> dict[str, Any]:
    kind = snapshot.get("kind")
    value = str(snapshot.get("value") or "").strip()
    if not is_choice(kind, {"commit", "tag", "release"}) or not value:
        raise ValueError("GitHub snapshot must provide a commit, tag, or release value")
    quoted_repository = "/".join(urllib.parse.quote(part, safe="") for part in repository.split("/", 1))
    if kind == "commit":
        api_url = f"https://api.github.com/repos/{quoted_repository}/commits/{urllib.parse.quote(value, safe='')}"
        body, final_url, _ = fetch_bytes(
            api_url,
            timeout,
            "application/vnd.github.sha",
            max_bytes=4096,
            allowed_hosts={"api.github.com"},
        )
        if not github_api_url_matches_object_path(final_url, repository, ["commits", value]):
            raise ValueError("GitHub commit final URL does not identify the requested revision")
        response_text = body.decode("utf-8", errors="replace").strip()
        if GIT_OBJECT_ID_PATTERN.fullmatch(response_text):
            canonical_value = response_text.lower()
        else:
            try:
                response_data = json.loads(response_text)
            except json.JSONDecodeError as exc:
                raise ValueError("GitHub commit metadata did not contain a canonical SHA") from exc
            canonical_value = str(response_data.get("sha") or "").lower() if isinstance(response_data, dict) else ""
        if not GIT_OBJECT_ID_PATTERN.fullmatch(canonical_value):
            raise ValueError("GitHub commit metadata did not contain a canonical Git object ID")
        if GIT_OBJECT_ID_PATTERN.fullmatch(value) and canonical_value != value.lower():
            raise ValueError("GitHub commit metadata does not match the requested object ID")
        evidence = f"https://github.com/{repository}/commit/{canonical_value}"
    elif kind == "release":
        api_url = f"https://api.github.com/repos/{quoted_repository}/releases/tags/{urllib.parse.quote(value, safe='')}"
        data, final_url = fetch_json(api_url, timeout, allowed_hosts={"api.github.com"})
        if not github_api_url_matches_object_path(final_url, repository, ["releases", "tags", value]):
            raise ValueError("GitHub release final URL does not identify the requested release")
        release_tag = data.get("tag_name")
        if not isinstance(release_tag, str) or release_tag != value:
            raise ValueError("GitHub release metadata does not identify the requested release")
        canonical_value, _ = resolve_github_tag_ref(repository, release_tag, timeout)
        evidence = str(data.get("html_url") or api_url)
    else:
        canonical_value, evidence = resolve_github_tag_ref(repository, value, timeout)
    return {
        "kind": kind,
        "value": canonical_value if kind == "commit" else value,
        "requested_value": value,
        "status": "verified",
        "verified_at": now_iso(),
        "canonical_value": canonical_value,
        "evidence": evidence,
    }


def verify_candidate_snapshot(
    candidate: dict[str, Any],
    verified_identity: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    raw_snapshot = candidate.get("source_snapshot")
    snapshot = copy.deepcopy(raw_snapshot) if isinstance(raw_snapshot, dict) else snapshot_template()
    if verified_identity.get("status") != "verified":
        return {
            **snapshot,
            "status": "blocked",
            "verified_at": now_iso(),
            "canonical_value": "",
            "evidence": "source identity must verify before snapshot verification",
        }
    identity_kind = verified_identity.get("kind")
    if identity_kind == "github":
        repository = str(verified_identity.get("canonical_id") or "").removeprefix("github:")
        try:
            return verify_github_snapshot(repository, snapshot, timeout)
        except ValueError as exc:
            return {
                **snapshot,
                "status": "failed",
                "verified_at": now_iso(),
                "canonical_value": "",
                "evidence": str(exc),
            }
    if is_choice(identity_kind, {"doi", "arxiv", "pmid"}):
        canonical_id = str(verified_identity.get("canonical_id") or "")
        return {
            "kind": "publication_version",
            "value": str(snapshot.get("value") or canonical_id),
            "status": "verified",
            "verified_at": now_iso(),
            "canonical_value": canonical_id,
            "evidence": str(verified_identity.get("evidence") or verified_identity.get("canonical_url") or ""),
        }
    return {
        "kind": str(snapshot.get("kind") or "dated_access"),
        "value": str(snapshot.get("value") or verified_identity.get("canonical_url") or ""),
        "status": "verified",
        "verified_at": now_iso(),
        "canonical_value": str(verified_identity.get("canonical_url") or ""),
        "evidence": str(verified_identity.get("evidence") or ""),
    }


def ensure_public_http_url(value: str) -> list[str]:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("official_url must be an HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("official_url must not contain embedded credentials")
    hostname = parsed.hostname or ""
    if hostname.lower() == "localhost" or hostname.lower().endswith(".local"):
        raise ValueError("official_url cannot target localhost or local-network names")
    return resolve_public_addresses(hostname, parsed.port or 443)


def canonical_public_https_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        canonical_host = f"[{hostname.lower()}]"
    else:
        canonical_host = hostname.encode("idna").decode("ascii").lower()
    port = parsed.port
    netloc = canonical_host if port in {None, 443} else f"{canonical_host}:{port}"
    return urllib.parse.urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))


def resolve_public_addresses(hostname: str, port: int) -> list[str]:
    try:
        addresses = sorted({str(item[4][0]) for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)})
    except socket.gaierror as exc:
        raise ValueError(f"official_url host could not be resolved: {hostname}") from exc
    if not addresses:
        raise ValueError(f"official_url host could not be resolved: {hostname}")
    for address in addresses:
        parsed_address = ipaddress.ip_address(address)
        if not parsed_address.is_global:
            raise ValueError("official_url cannot target private, loopback, link-local, or reserved addresses")
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float) -> None:
        self.ssl_context = ssl.create_default_context()
        super().__init__(hostname, port=port, timeout=timeout, context=self.ssl_context)
        self.pinned_address = address

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self.pinned_address, self.port),
            self.timeout,
        )
        self.sock = self.ssl_context.wrap_socket(raw_socket, server_hostname=self.host)


def fetch_public_https_once(
    url: str,
    address: str,
    timeout: float,
    accept: str | None,
    max_bytes: int,
) -> tuple[bytes, int, str | None]:
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname or ""
    port = parsed.port or 443
    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    headers = request_headers(url)
    headers["Connection"] = "close"
    if accept:
        headers["Accept"] = accept
    connection = PinnedHTTPSConnection(hostname, address, port, timeout)
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"metadata response exceeds {max_bytes} bytes: {url}")
        return body, int(response.status), response.getheader("Location")
    finally:
        connection.close()


def fetch_public_https_bytes(
    url: str,
    timeout: float,
    accept: str | None,
    *,
    max_bytes: int,
    retries: int,
) -> tuple[bytes, str, int]:
    current_url = url
    for _redirect in range(MAX_PUBLIC_REDIRECTS + 1):
        addresses = ensure_public_http_url(current_url)
        last_error: Exception | None = None
        response: tuple[bytes, int, str | None] | None = None
        for attempt in range(retries + 1):
            for address in addresses:
                try:
                    response = fetch_public_https_once(current_url, address, timeout, accept, max_bytes)
                    break
                except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError) as exc:
                    last_error = exc
            if response is not None:
                if response[1] in {429, 500, 502, 503, 504} and attempt < retries:
                    last_error = ValueError(f"HTTP {response[1]} for {current_url}")
                    response = None
                    time.sleep(0.25 * (2 ** attempt))
                    continue
                break
            if attempt < retries:
                time.sleep(0.25 * (2 ** attempt))
        if response is None:
            raise ValueError(f"network error for {current_url}: {last_error}") from last_error
        body, status, location = response
        if status in {301, 302, 303, 307, 308}:
            if not location:
                raise ValueError(f"HTTP {status} redirect had no Location header for {current_url}")
            current_url = urllib.parse.urljoin(current_url, location)
            continue
        if status < 200 or status >= 300:
            raise ValueError(f"HTTP {status} for {current_url}")
        return body, current_url, status
    raise ValueError(f"too many redirects for {url}")


def verify_official_url(value: str, timeout: float) -> dict[str, Any]:
    ensure_public_http_url(value)
    body, final_url, status = fetch_bytes(
        value,
        timeout,
        "text/html, application/json;q=0.9, */*;q=0.5",
        max_bytes=500_000,
        public_only=True,
    )
    canonical_url = canonical_public_https_url(final_url)
    ensure_public_http_url(canonical_url)
    sample = body[:200_000].decode("utf-8", errors="ignore")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", sample, re.I | re.S)
    final_host = urllib.parse.urlparse(canonical_url).hostname or ""
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    if not title:
        title = final_host
    return {
        "canonical_id": f"url:{canonical_url.rstrip('/')}",
        "canonical_url": canonical_url,
        "resolved_title": title,
        "verification_method": "HTTP retrieval",
        "evidence": f"HTTP {status}",
        "metadata": {"http_status": status},
    }


def verify_candidate_source(
    candidate: dict[str, Any],
    timeout: float,
    *,
    allow_official_url: bool = False,
) -> dict[str, Any]:
    identity = candidate.get("source_identity")
    if not isinstance(identity, dict):
        return {**identity_template(), "status": "failed", "evidence": "source_identity is missing"}
    kind = identity.get("kind")
    value = str(identity.get("value") or "")
    result = copy.deepcopy(identity)
    result["verified_at"] = now_iso()
    try:
        candidate_title = str(candidate.get("title") or "")
        if len(candidate_title) > MAX_TITLE_CHARS:
            raise ValueError(f"candidate title exceeds {MAX_TITLE_CHARS} characters")
        if kind == "doi":
            verified = verify_doi(value, timeout)
            aliases = [verified["resolved_title"]]
        elif kind == "arxiv":
            verified = verify_arxiv(value, timeout)
            aliases = [verified["resolved_title"]]
        elif kind == "pmid":
            verified = verify_pmid(value, timeout)
            aliases = [verified["resolved_title"]]
        elif kind == "github":
            verified = verify_github(value, timeout)
            aliases = [verified["resolved_title"], str(verified.get("metadata", {}).get("name") or "")]
        elif kind == "official_url":
            if not allow_official_url:
                raise ValueError("automatic official_url retrieval is disabled; rerun with explicit --allow-official-url")
            verified = verify_official_url(value, timeout)
            aliases = [verified["resolved_title"]]
        else:
            raise ValueError("unsupported identity kind for automatic verification")
        if any(len(alias) > MAX_TITLE_CHARS for alias in aliases):
            raise ValueError(f"authoritative title exceeds {MAX_TITLE_CHARS} characters")
        score = title_similarity(candidate_title, aliases)
        result.update(verified)
        result["title_match"] = score
        normalized_candidate = normalized_text(candidate_title)
        normalized_aliases = {normalized_text(alias) for alias in aliases if normalized_text(alias)}
        if not normalized_candidate or normalized_candidate not in normalized_aliases:
            result["status"] = "failed"
            result["evidence"] = f"{verified['evidence']}; title mismatch score={score:.4f}"
        else:
            result["status"] = "verified"
    except ValueError as exc:
        result.update({
            "status": "failed",
            "verification_method": str(kind or "unknown"),
            "canonical_id": "",
            "canonical_url": "",
            "resolved_title": "",
            "title_match": 0.0,
            "evidence": str(exc),
        })
    return result


def command_init(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser()
    output_state = current_regular_file_state(output)
    if output_state is not None and not args.force:
        raise SystemExit(f"refusing to overwrite existing contract: {display_safe_text(output)}; use --force")
    write_json_atomic(
        output,
        template(args.project, args.question, args.profile, args.mode),
        expected_state=output_state,
    )
    print(f"initialized research contract v{CONTRACT_VERSION}: {display_safe_text(output)}")
    return 0


def command_verify_sources(args: argparse.Namespace) -> int:
    contract_path = Path(args.contract).expanduser()
    data, contract_state = load_json_with_state(contract_path)
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        raise SystemExit("candidates must be a list before source verification")
    failures = 0
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            print(f"FAILED candidates[{index}]: candidate is not an object")
            failures += 1
            continue
        result = verify_candidate_source(candidate, args.timeout, allow_official_url=args.allow_official_url)
        candidate["source_identity"] = result
        snapshot_result = verify_candidate_snapshot(candidate, result, args.timeout)
        candidate["source_snapshot"] = snapshot_result
        candidate_id = candidate.get("id") or f"index-{index}"
        print(
            f"{display_safe_text(str(result.get('status')).upper())} {display_safe_text(candidate_id)}: "
            f"{display_safe_text(result.get('canonical_id') or result.get('evidence'))}"
        )
        print(
            f"{display_safe_text(str(snapshot_result.get('status')).upper())} "
            f"{display_safe_text(candidate_id)} snapshot: "
            f"{display_safe_text(snapshot_result.get('canonical_value') or snapshot_result.get('evidence'))}"
        )
        candidate_status = candidate.get("status")
        if is_choice(candidate_status, {"include", "adapt", "monitor"}) and result.get("status") != "verified":
            failures += 1
        elif is_choice(candidate_status, {"include", "adapt"}) and snapshot_result.get("status") != "verified":
            failures += 1
    if args.write:
        write_json_atomic(contract_path, data, expected_state=contract_state)
        print(f"updated source identity records: {display_safe_text(contract_path)}")
    else:
        print("dry run: use --write to store verification metadata")
    return 1 if failures else 0


def read_report_text(path: Path, findings: Findings) -> str:
    if path.suffix.lower() not in {".md", ".txt", ".tex", ".json", ".rst"}:
        findings.error(f"report must be extracted to UTF-8 text before semantic scanning: {path}")
        return ""
    try:
        return read_regular_bytes(path, MAX_REPORT_BYTES, "report").decode("utf-8")
    except UnicodeDecodeError:
        findings.error(f"report is not UTF-8 text: {path}")
    except (OSError, ValueError) as exc:
        if "exceeds" in str(exc):
            findings.error(f"report exceeds {MAX_REPORT_BYTES} bytes and was not scanned: {path}")
        else:
            findings.error(f"report could not be read: {path}: {exc}")
    return ""


def print_findings(findings: Findings) -> None:
    for message in findings.errors:
        print(f"ERROR: {display_safe_text(message)}")
    for message in findings.warnings:
        print(f"WARNING: {display_safe_text(message)}")
    print(f"research contract audit: {len(findings.errors)} error(s), {len(findings.warnings)} warning(s)")


def validate_online_sources(
    data: dict[str, Any],
    findings: Findings,
    *,
    timeout: float,
    allow_official_url: bool,
) -> None:
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        findings.error("online verification requires a candidate list")
        return
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or not is_choice(candidate.get("status"), {"include", "adapt", "monitor"}):
            continue
        prefix = f"candidates[{index}]"
        stored_identity = dict_or_empty(candidate.get("source_identity"))
        fresh_identity = verify_candidate_source(
            candidate,
            timeout,
            allow_official_url=allow_official_url,
        )
        if fresh_identity.get("status") != "verified":
            findings.error(f"{prefix} failed live source-identity verification: {fresh_identity.get('evidence')}")
            continue
        for key in ("canonical_id", "canonical_url", "resolved_title"):
            require(
                stored_identity.get(key) == fresh_identity.get(key),
                findings,
                f"{prefix}.source_identity.{key} differs from current authoritative metadata",
            )
        if is_choice(candidate.get("status"), {"include", "adapt"}):
            stored_snapshot = dict_or_empty(candidate.get("source_snapshot"))
            fresh_snapshot = verify_candidate_snapshot(candidate, fresh_identity, timeout)
            if fresh_snapshot.get("status") != "verified":
                findings.error(f"{prefix} failed live source-snapshot verification: {fresh_snapshot.get('evidence')}")
                continue
            require(
                stored_snapshot.get("canonical_value") == fresh_snapshot.get("canonical_value"),
                findings,
                f"{prefix}.source_snapshot.canonical_value differs from current authoritative metadata",
            )


def command_validate(args: argparse.Namespace) -> int:
    contract_path = Path(args.contract).expanduser()
    base_path = Path(args.base).expanduser().resolve() if args.base else Path.cwd()
    data = load_json(contract_path)
    findings = validate_contract(data, base_path=base_path)
    if args.report:
        report_path = Path(args.report).expanduser()
        report_text = read_report_text(report_path, findings)
        if report_text:
            scan_unbounded_text(report_text, findings, str(report_path))
    if args.online:
        validate_online_sources(
            data,
            findings,
            timeout=args.timeout,
            allow_official_url=args.allow_official_url,
        )
    print_findings(findings)
    if findings.errors:
        return 1
    if args.online:
        print(
            "ONLINE_IDENTITY_PASS: recorded structure and evidence gates passed, and selected source identities "
            "were re-resolved through authoritative services; scientific validity and exhaustive discovery are not proven"
        )
    else:
        print(
            "SCHEMA_PASS: recorded structure and internal evidence gates passed; stored source metadata was not "
            "re-resolved, and scientific validity and exhaustive discovery are not proven"
        )
    return 0


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def keyed_changes(old_items: Any, new_items: Any, key: str = "id") -> dict[str, Any]:
    old_list = old_items if isinstance(old_items, list) else []
    new_list = new_items if isinstance(new_items, list) else []
    old_map = {
        item[key]: item
        for item in old_list
        if isinstance(item, dict) and isinstance(item.get(key), str) and item[key].strip()
    }
    new_map = {
        item[key]: item
        for item in new_list
        if isinstance(item, dict) and isinstance(item.get(key), str) and item[key].strip()
    }
    changed = []
    for item_id in sorted(old_map.keys() & new_map.keys()):
        before = old_map[item_id]
        after = new_map[item_id]
        fields = sorted(field for field in before.keys() | after.keys() if before.get(field) != after.get(field))
        if fields:
            changed.append({"id": item_id, "fields": fields})
    return {
        "added": sorted(new_map.keys() - old_map.keys()),
        "removed": sorted(old_map.keys() - new_map.keys()),
        "changed": changed,
    }


def contract_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    section_names = (
        "scope",
        "search_lanes",
        "seed_discovery",
        "source_classes",
        "query_families",
        "record_management",
        "search_quality",
        "trend_discovery",
        "chaining",
        "gaps",
        "coverage_exceptions",
        "stop_rule",
        "coverage_statement",
    )
    section_changes = [
        {
            "section": name,
            "old_sha256": canonical_hash(old.get(name)),
            "new_sha256": canonical_hash(new.get(name)),
        }
        for name in section_names
        if old.get(name) != new.get(name)
    ]
    return {
        "old_cutoff": (old.get("scope") or {}).get("cutoff_date") if isinstance(old.get("scope"), dict) else None,
        "new_cutoff": (new.get("scope") or {}).get("cutoff_date") if isinstance(new.get("scope"), dict) else None,
        "sections_changed": section_changes,
        "candidates": keyed_changes(old.get("candidates"), new.get("candidates")),
        "mechanisms": keyed_changes(old.get("mechanisms"), new.get("mechanisms")),
    }


def command_diff(args: argparse.Namespace) -> int:
    old = load_json(Path(args.old_contract).expanduser())
    new = load_json(Path(args.new_contract).expanduser())
    if not args.allow_invalid:
        old_findings = validate_contract(old, base_path=Path.cwd())
        new_findings = validate_contract(new, base_path=Path.cwd())
        if old_findings.errors or new_findings.errors:
            print("ERROR: diff inputs must validate; use --allow-invalid only for migration diagnostics")
            print(f"old errors={len(old_findings.errors)}, new errors={len(new_findings.errors)}")
            return 1
    print(display_safe_text(json.dumps(contract_diff(old, new), ensure_ascii=False, indent=2), preserve_newlines=True))
    return 0


def legacy_evidence(value: Any, kind: str = "note") -> dict[str, Any]:
    return {
        "kind": kind,
        "locator": str(value or "legacy v1 value requires verification"),
        "status": "pending",
        "checked_at": "",
        "sha256": "",
        "note": "Migrated from v1; verify before use.",
    }


def infer_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    locator = str(candidate.get("source_locator") or candidate.get("url") or "")
    identity = identity_template()
    if re.search(r"(?:doi\.org/|^doi:|^10\.\d{4,9}/)", locator, re.I):
        identity.update({"kind": "doi", "value": normalize_doi(locator)})
    elif re.search(r"arxiv\.org/|^arxiv:", locator, re.I):
        identity.update({"kind": "arxiv", "value": normalize_arxiv(locator)})
    elif re.search(r"github\.com/", locator, re.I):
        identity.update({"kind": "github", "value": normalize_github(locator)})
    elif locator.startswith(("http://", "https://")):
        identity.update({"kind": "official_url", "value": locator})
    else:
        identity.update({"kind": "other", "value": locator})
    return identity


def migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("contract_version") == CONTRACT_VERSION:
        return copy.deepcopy(data)
    if data.get("contract_version") != 1:
        raise ValueError("only v1 contracts can be migrated to v2")
    for key in ("source_classes", "query_families", "candidates", "mechanisms"):
        if key in data and not isinstance(data.get(key), list):
            raise ValueError(f"malformed v1 contract: {key} must be a list")
    for key in ("scope", "chaining", "stop_rule"):
        if key in data and not isinstance(data.get(key), dict):
            raise ValueError(f"malformed v1 contract: {key} must be an object")
    migrated = copy.deepcopy(data)
    migrated["contract_version"] = CONTRACT_VERSION
    migrated["migration_notes"] = [
        "Migrated from v1. Source identities and evidence references remain pending until explicitly verified.",
        "Legacy aggregate query counts were preserved but must be replaced by per-execution evidence.",
    ]
    for source in migrated.get("source_classes", []):
        if isinstance(source, dict):
            source.setdefault("interface", "legacy interface requires confirmation")
    for family in migrated.get("query_families", []):
        if not isinstance(family, dict) or "executions" in family:
            continue
        queries = list_or_empty(family.get("queries"))
        sources = list_or_empty(family.get("sources"))
        source = str(sources[0]) if sources else ""
        total = family.get("results_count") if isinstance(family.get("results_count"), int) else 0
        family["executions"] = [
            {
                "source": source,
                "interface": "legacy interface requires confirmation",
                "exact_query": str(query),
                "executed_at": "",
                "filters": [],
                "limits": [],
                "results_count": total if index == 0 else 0,
                "result_evidence": legacy_evidence("legacy search result snapshot is unavailable"),
            }
            for index, query in enumerate(queries)
        ]
    migrated.setdefault("record_management", template("", "", "computing-software")["record_management"])
    migrated.setdefault("search_quality", template("", "", "computing-software")["search_quality"])
    migrated.setdefault("seed_discovery", seed_discovery_template())
    migrated.setdefault("scope", {}).setdefault("review_type", "exploratory")
    migrated.setdefault("scope", {}).setdefault("trend_requirement", "not_requested")
    migrated.setdefault("trend_discovery", template("", "", "computing-software")["trend_discovery"])
    for chain in dict_or_empty(migrated.get("chaining")).values():
        if isinstance(chain, dict) and not isinstance(chain.get("evidence"), dict):
            chain["evidence"] = legacy_evidence(chain.get("evidence"))
    for candidate in migrated.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate.setdefault("source_identity", infer_identity(candidate))
        if not isinstance(candidate.get("source_snapshot"), dict):
            legacy_snapshot = snapshot_template()
            legacy_snapshot["value"] = str(candidate.get("source_snapshot") or "legacy snapshot requires verification")
            candidate["source_snapshot"] = legacy_snapshot
        reviewed = candidate.get("reviewed")
        if isinstance(reviewed, list):
            candidate["reviewed"] = [item if isinstance(item, dict) else legacy_evidence(item, "section") for item in reviewed]
    for mechanism in migrated.get("mechanisms", []):
        if not isinstance(mechanism, dict):
            continue
        for key, kind in (
            ("evidence_location", "section"),
            ("artifact", "file"),
            ("positive_test", "note"),
            ("failure_test", "note"),
            ("audit_evidence", "log"),
        ):
            if key in mechanism and not isinstance(mechanism.get(key), dict):
                mechanism[key] = legacy_evidence(mechanism.get(key), kind)
    stop = migrated.get("stop_rule")
    if isinstance(stop, dict) and not isinstance(stop.get("evidence"), dict):
        stop["evidence"] = legacy_evidence(stop.get("evidence"))
    return migrated


def command_migrate(args: argparse.Namespace) -> int:
    input_path = Path(args.input).expanduser()
    output_path = Path(args.output).expanduser()
    output_state = current_regular_file_state(output_path)
    if output_state is not None and not args.force:
        raise SystemExit(
            f"refusing to overwrite existing contract: {display_safe_text(output_path)}; use --force"
        )
    try:
        migrated = migrate_v1_to_v2(load_json(input_path))
    except ValueError as exc:
        raise SystemExit(display_safe_text(exc)) from exc
    write_json_atomic(output_path, migrated, expected_state=output_state)
    print(f"migrated research contract to v{CONTRACT_VERSION}: {display_safe_text(output_path)}")
    print("source identity and evidence fields remain pending until reverified")
    return 0


def markdown_cell(value: Any) -> str:
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    elif isinstance(value, dict):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            value = repr(value)
    text = display_safe_text(value or "").replace("\n", " ").replace("\r", " ")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for character in ("\\", "`", "*", "_", "[", "]", "|"):
        text = text.replace(character, f"\\{character}")
    return text


def evidence_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return "missing"
    return f"{value.get('status', '')}: {value.get('kind', '')} {value.get('locator', '')}".strip()


def list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def render_contract(data: dict[str, Any]) -> str:
    scope = dict_or_empty(data.get("scope"))
    lanes = dict_or_empty(data.get("search_lanes"))
    lines = [
        f"# {markdown_cell(data.get('project', 'Research'))} discovery and translation audit",
        "",
        "## 1. Question and scope",
        "",
        f"- Question: {markdown_cell(data.get('question', ''))}",
        f"- Mode: {markdown_cell(data.get('mode', ''))}",
        f"- Primary profile: {markdown_cell(data.get('profile', ''))}",
        f"- Created: {markdown_cell(data.get('created_at', ''))}",
        f"- Cutoff: {markdown_cell(scope.get('cutoff_date', ''))}",
        f"- Freshness requirement: {markdown_cell(scope.get('freshness_requirement', ''))}",
        f"- Review type: {markdown_cell(scope.get('review_type', ''))}",
        f"- Languages: {markdown_cell(scope.get('languages', []))}",
        f"- Geography: {markdown_cell(scope.get('geography', []))}",
        f"- Source types: {markdown_cell(scope.get('source_types', []))}",
        f"- Constraints: {markdown_cell(scope.get('constraints', []))}",
        f"- Inclusion rules: {markdown_cell(scope.get('inclusion', []))}",
        f"- Exclusion rules: {markdown_cell(scope.get('exclusion', []))}",
        "",
        "## 2. Coverage and search execution",
        "",
        "### Search lanes",
        "",
    ]
    for lane_name in ("direct_use", "mechanism_transfer"):
        lane = dict_or_empty(lanes.get(lane_name))
        lines.append(
            f"- **{markdown_cell(lane_name)}**: searched={markdown_cell(lane.get('searched'))}; "
            f"{markdown_cell(lane.get('summary'))}"
        )

    seed = dict_or_empty(data.get("seed_discovery"))
    fingerprint = dict_or_empty(seed.get("mechanism_fingerprint"))
    lines.extend([
        "",
        "### User-shared seed provenance",
        "",
        f"- Status/type/platform: {markdown_cell(seed.get('status'))}; {markdown_cell(seed.get('source_type'))}; {markdown_cell(seed.get('platform'))}",
        f"- Shared/source: {markdown_cell(seed.get('shared_at'))}; {markdown_cell(seed.get('source_locator'))}",
        f"- Privacy-minimized summary: {markdown_cell(seed.get('seed_summary'))}",
        f"- Retention/extraction/confidence: {markdown_cell(seed.get('retention'))}; {markdown_cell(seed.get('extraction_method'))}; {markdown_cell(seed.get('extraction_confidence'))}",
        f"- Uncertain variants: {markdown_cell(seed.get('uncertain_variants'))}",
        f"- Source evidence: {markdown_cell(evidence_summary(seed.get('source_evidence')))}",
        f"- Mechanism fingerprint: problem={markdown_cell(fingerprint.get('problem'))}; modalities={markdown_cell(fingerprint.get('modalities'))}; core={markdown_cell(fingerprint.get('core_mechanisms'))}; runtime constraints={markdown_cell(fingerprint.get('runtime_constraints'))}; claimed evidence={markdown_cell(fingerprint.get('claimed_evidence'))}; unresolved claims={markdown_cell(fingerprint.get('unresolved_claims'))}",
    ])

    lines.extend([
        "",
        "### Source classes",
        "",
        "| Source class | Status | Interface/date | Reason |",
        "|---|---|---|---|",
    ])
    for source in list_or_empty(data.get("source_classes")):
        if not isinstance(source, dict):
            continue
        interface_date = " ".join(
            part for part in (str(source.get("interface") or ""), str(source.get("searched_at") or "")) if part
        )
        values = [source.get("name"), source.get("status"), interface_date, source.get("reason")]
        lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")

    lines.extend([
        "",
        "### Search execution",
        "",
        "| Family | Concept | Lanes | Source | Exact query | Executed | Results | Evidence |",
        "|---|---|---|---|---|---|---:|---|",
    ])
    for family in list_or_empty(data.get("query_families")):
        if not isinstance(family, dict):
            continue
        for execution in list_or_empty(family.get("executions")):
            if not isinstance(execution, dict):
                continue
            values = [
                family.get("id"),
                family.get("concept"),
                family.get("lanes"),
                execution.get("source"),
                execution.get("exact_query"),
                execution.get("executed_at"),
                execution.get("results_count"),
                evidence_summary(execution.get("result_evidence")),
            ]
            lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")
    records = dict_or_empty(data.get("record_management"))
    lines.extend([
        "",
        "### Record management",
        "",
        f"- Status: {markdown_cell(records.get('status'))}",
        f"- Deduplication: {markdown_cell(records.get('deduplication_method'))}",
        f"- Flow: identified {markdown_cell(records.get('records_identified'))}; duplicates removed {markdown_cell(records.get('duplicates_removed'))}; screened {markdown_cell(records.get('records_screened'))}; deep reviewed {markdown_cell(records.get('records_deep_reviewed'))}; included {markdown_cell(records.get('records_included'))}.",
        f"- Exclusion reasons: {markdown_cell(records.get('exclusion_reason_counts'))}",
        f"- Flow evidence: {markdown_cell(evidence_summary(records.get('flow_evidence')))}",
        "",
        "### Search quality",
        "",
    ])
    search_quality = dict_or_empty(data.get("search_quality"))
    peer_review = dict_or_empty(search_quality.get("strategy_peer_review"))
    publication_check = dict_or_empty(search_quality.get("publication_status_check"))
    lines.extend([
        f"- Strategy peer review: {markdown_cell(peer_review.get('status'))}; reviewer={markdown_cell(peer_review.get('reviewer'))}; evidence={markdown_cell(evidence_summary(peer_review.get('evidence')))}",
        f"- Publication-status check: {markdown_cell(publication_check.get('status'))}; checked={markdown_cell(publication_check.get('checked_at'))}; evidence={markdown_cell(evidence_summary(publication_check.get('evidence')))}",
        "",
        "### Chaining",
        "",
        "| Path | Status | Evidence/reason |",
        "|---|---|---|",
    ])
    chaining = dict_or_empty(data.get("chaining"))
    for name in ("backward", "forward", "related_projects", "authors_organizations", "benchmarks_competitors", "failures_corrections"):
        item = dict_or_empty(chaining.get(name))
        detail = evidence_summary(item.get("evidence")) if item.get("status") == "completed" else item.get("reason")
        lines.append("| " + " | ".join(markdown_cell(value) for value in (name, item.get("status"), detail)) + " |")

    trend = dict_or_empty(data.get("trend_discovery"))
    lines.extend([
        "",
        "### Emerging and popular-source signals",
        "",
        f"- Requirement: {markdown_cell(scope.get('trend_requirement', 'not_requested'))}",
        f"- Status/window: {markdown_cell(trend.get('status'))}; {markdown_cell(trend.get('window_days'))} days",
        f"- Operational definition: {markdown_cell(trend.get('definition'))}",
        f"- Evidence policy: {markdown_cell(trend.get('evidence_policy'))}",
        f"- Triangulation: {markdown_cell(trend.get('triangulation_rule'))}",
        f"- Reason if not run: {markdown_cell(trend.get('reason'))}",
        "",
        "| Trend source | Independent group | Interface | Exact query/feed | Searched | Results | Evidence |",
        "|---|---|---|---|---|---:|---|",
    ])
    for source in list_or_empty(trend.get("sources")):
        if not isinstance(source, dict):
            continue
        values = [
            source.get("name"),
            source.get("independence_group"),
            source.get("interface"),
            source.get("exact_query"),
            source.get("searched_at"),
            source.get("results_count"),
            evidence_summary(source.get("result_evidence")),
        ]
        lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")
    lines.extend([
        "",
        "| Signal | Candidate | Source | Type | Value | Observed | Evidence |",
        "|---|---|---|---|---|---|---|",
    ])
    for signal in list_or_empty(trend.get("signals")):
        if not isinstance(signal, dict):
            continue
        values = [
            signal.get("id"),
            signal.get("candidate_id"),
            signal.get("source"),
            signal.get("signal_type"),
            signal.get("value"),
            signal.get("observed_at"),
            evidence_summary(signal.get("evidence")),
        ]
        lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")
    lines.extend([
        "",
        "| Trend claim | Candidate | Label | Supporting signals | Boundary |",
        "|---|---|---|---|---|",
    ])
    for claim in list_or_empty(trend.get("claims")):
        if not isinstance(claim, dict):
            continue
        values = [
            claim.get("id"),
            claim.get("candidate_id"),
            claim.get("label"),
            claim.get("signal_ids"),
            claim.get("boundary"),
        ]
        lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")

    lines.extend([
        "",
        "## 3. Candidate appraisal",
        "",
        "| ID | Candidate | Type | Decision | Discovered via | Authority | Direct fit | Mechanism fit | Identity | Snapshot | Review depth |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ])
    candidate_rationales: list[str] = []
    for candidate in list_or_empty(data.get("candidates")):
        if not isinstance(candidate, dict):
            continue
        identity = dict_or_empty(candidate.get("source_identity"))
        snapshot = dict_or_empty(candidate.get("source_snapshot"))
        identity_text = f"{identity.get('status', '')}: {identity.get('canonical_id') or identity.get('value') or ''}"
        snapshot_text = f"{snapshot.get('status', '')}: {snapshot.get('kind', '')} {snapshot.get('canonical_value') or snapshot.get('value') or ''}"
        values = [
            candidate.get("id"),
            candidate.get("title"),
            candidate.get("type"),
            candidate.get("status"),
            candidate.get("discovered_via"),
            candidate.get("authority"),
            candidate.get("direct_use_fit"),
            candidate.get("mechanism_fit"),
            identity_text,
            snapshot_text,
            candidate.get("review_depth"),
        ]
        lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")
        candidate_rationales.append(
            f"- **{markdown_cell(candidate.get('id'))} rationale:** {markdown_cell(candidate.get('rationale'))}"
        )

    lines.extend(["", "### Candidate rationale", ""])
    lines.extend(candidate_rationales or ["- None recorded."])

    lines.extend(["", "## 4. Source-depth limits", ""])
    candidates = [item for item in list_or_empty(data.get("candidates")) if isinstance(item, dict)]
    if candidates:
        for candidate in candidates:
            identity = dict_or_empty(candidate.get("source_identity"))
            snapshot = dict_or_empty(candidate.get("source_snapshot"))
            reviewed = [evidence_summary(item) for item in list_or_empty(candidate.get("reviewed"))]
            not_reviewed = list_or_empty(candidate.get("not_reviewed"))
            open_questions = list_or_empty(candidate.get("open_questions"))
            lines.extend([
                f"- **{markdown_cell(candidate.get('id'))}: {markdown_cell(candidate.get('title'))}**",
                f"  - Identity verification: status={markdown_cell(identity.get('status'))}; method={markdown_cell(identity.get('verification_method'))}; evidence={markdown_cell(identity.get('evidence'))}",
                f"  - Snapshot verification: status={markdown_cell(snapshot.get('status'))}; evidence={markdown_cell(snapshot.get('evidence'))}",
                f"  - Repository/source metadata: {markdown_cell(identity.get('metadata', {}))}",
                f"  - Reviewed: {markdown_cell(reviewed)}",
                f"  - Not reviewed: {markdown_cell(not_reviewed)}",
                f"  - Open questions: {markdown_cell(open_questions)}",
            ])
    else:
        lines.append("- No candidate rows were recorded.")

    lines.extend([
        "",
        "## 5. Atomic mechanism and translation matrix",
        "",
        "| ID | Source | Statement | Evidence | Strength/applicability | Decision | Translation | Status | Claim boundary |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for mechanism in list_or_empty(data.get("mechanisms")):
        if not isinstance(mechanism, dict):
            continue
        values = [
            mechanism.get("id"),
            mechanism.get("source_id"),
            mechanism.get("statement"),
            evidence_summary(mechanism.get("evidence_location")),
            f"{mechanism.get('evidence_strength', '')}; {mechanism.get('applicability', '')}",
            mechanism.get("decision"),
            mechanism.get("translation"),
            mechanism.get("implementation_status"),
            mechanism.get("claim_boundary"),
        ]
        lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")

    lines.extend([
        "",
        "## 6. Implemented evidence and tests",
        "",
        "| Mechanism | Decision effect | Artifact | Positive/failure evidence | Audit evidence |",
        "|---|---|---|---|---|",
    ])
    implemented = [
        item for item in list_or_empty(data.get("mechanisms"))
        if isinstance(item, dict) and is_choice(item.get("implementation_status"), {"implemented", "validated"})
    ]
    if implemented:
        for mechanism in implemented:
            values = [
                mechanism.get("id"),
                mechanism.get("decision_effect"),
                evidence_summary(mechanism.get("artifact")),
                f"{evidence_summary(mechanism.get('positive_test'))}; {evidence_summary(mechanism.get('failure_test'))}",
                evidence_summary(mechanism.get("audit_evidence")),
            ]
            lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")
    else:
        lines.append("| None recorded |  |  |  |  |")

    lines.extend(["", "## 7. Deferred, rejected, unresolved, and unread items", ""])
    bounded_items: list[str] = []
    for candidate in candidates:
        if is_choice(candidate.get("status"), {"monitor", "exclude", "unresolved"}):
            bounded_items.append(
                f"Candidate {candidate.get('id')} is {candidate.get('status')}: {candidate.get('rationale', '')}"
            )
        if list_or_empty(candidate.get("not_reviewed")):
            bounded_items.append(f"Candidate {candidate.get('id')} not reviewed: {candidate.get('not_reviewed')}")
        if list_or_empty(candidate.get("open_questions")):
            bounded_items.append(f"Candidate {candidate.get('id')} open questions: {candidate.get('open_questions')}")
    for mechanism in list_or_empty(data.get("mechanisms")):
        if isinstance(mechanism, dict) and is_choice(mechanism.get("decision"), {"defer", "reject", "unverified"}):
            bounded_items.append(
                f"Mechanism {mechanism.get('id')} is {mechanism.get('decision')}: {mechanism.get('rationale', '')}"
            )
    if bounded_items:
        lines.extend(f"- {markdown_cell(item)}" for item in bounded_items)
    else:
        lines.append("- None recorded; confirm this is credible before reporting completion.")

    lines.extend(["", "## 8. Residual risks and refresh trigger", ""])
    gaps = [gap for gap in list_or_empty(data.get("gaps")) if isinstance(gap, dict)]
    if gaps:
        for gap in gaps:
            lines.append(
                f"- **{markdown_cell(gap.get('type', 'gap'))}**: {markdown_cell(gap.get('detail'))} "
                f"Impact: {markdown_cell(gap.get('impact'))} Mitigation: {markdown_cell(gap.get('mitigation'))} "
                f"Status: {markdown_cell(gap.get('status'))}."
            )
    else:
        lines.append("- No gap rows were recorded. This must not be interpreted as proof that no gaps exist.")
    exceptions = [item for item in list_or_empty(data.get("coverage_exceptions")) if isinstance(item, dict)]
    lines.extend(["", "### Coverage exceptions", ""])
    if exceptions:
        for item in exceptions:
            lines.append(
                f"- **{markdown_cell(item.get('check'))}**: {markdown_cell(item.get('reason'))} "
                f"Approved by {markdown_cell(item.get('approved_by'))}. Impact: {markdown_cell(item.get('impact'))}"
            )
    else:
        lines.append("- None recorded.")
    stop = dict_or_empty(data.get("stop_rule"))
    lines.extend([
        "",
        "### Stop and refresh",
        "",
        f"- Rule: {markdown_cell(stop.get('rule'))}",
        f"- Satisfied: {markdown_cell(stop.get('satisfied'))}",
        f"- Evidence: {markdown_cell(evidence_summary(stop.get('evidence')))}",
        f"- Refresh trigger: rerun when the freshness requirement `{markdown_cell(scope.get('freshness_requirement'))}` is no longer met, the cutoff `{markdown_cell(scope.get('cutoff_date'))}` becomes stale, or a recorded gap/central source changes.",
        "",
        "## 9. Bounded coverage statement",
        "",
        markdown_cell(data.get("coverage_statement")),
    ])
    lines.append("")
    return "\n".join(lines)


def command_render(args: argparse.Namespace) -> int:
    contract_path = Path(args.contract).expanduser()
    data = load_json(contract_path)
    findings = validate_contract(data, base_path=Path(args.base).expanduser().resolve() if args.base else Path.cwd())
    if findings.errors and not args.allow_invalid:
        print_findings(findings)
        print("ERROR: refusing to render an invalid contract; use --allow-invalid only for drafting")
        return 1
    output = Path(args.output).expanduser()
    output_state = current_regular_file_state(output)
    if output_state is not None and not args.force:
        raise SystemExit(f"refusing to overwrite existing report: {display_safe_text(output)}; use --force")
    write_text_atomic(
        output,
        render_contract(data),
        default_mode=0o644,
        expected_state=output_state,
    )
    print(f"rendered contract report: {display_safe_text(output)}")
    return 0


def positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a v2 research contract template")
    init_parser.add_argument("--output", required=True)
    init_parser.add_argument("--project", required=True)
    init_parser.add_argument("--question", required=True)
    init_parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    init_parser.add_argument("--mode", choices=sorted(MODES), default="full")
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(func=command_init)

    validate_parser = subparsers.add_parser("validate", help="validate a populated research contract")
    validate_parser.add_argument("contract")
    validate_parser.add_argument("--base", help="base directory for relative evidence files; defaults to cwd")
    validate_parser.add_argument("--report", help="UTF-8 Markdown/text/LaTeX report to scan")
    validate_parser.add_argument("--online", action="store_true", help="recheck selected source identities and snapshots against authoritative services")
    validate_parser.add_argument("--timeout", type=positive_float, default=12.0)
    validate_parser.add_argument("--allow-official-url", action="store_true")
    validate_parser.set_defaults(func=command_validate)

    verify_parser = subparsers.add_parser("verify-sources", help="verify DOI/arXiv/PMID/GitHub identities using official metadata services")
    verify_parser.add_argument("contract")
    verify_parser.add_argument("--timeout", type=positive_float, default=12.0)
    verify_parser.add_argument("--write", action="store_true")
    verify_parser.add_argument(
        "--allow-official-url",
        action="store_true",
        help="allow bounded retrieval of public official URLs; DOI/arXiv/PMID/GitHub verification does not need this",
    )
    verify_parser.set_defaults(func=command_verify_sources)

    migrate_parser = subparsers.add_parser("migrate", help="migrate a v1 contract to v2 without trusting legacy evidence")
    migrate_parser.add_argument("--input", required=True)
    migrate_parser.add_argument("--output", required=True)
    migrate_parser.add_argument("--force", action="store_true")
    migrate_parser.set_defaults(func=command_migrate)

    diff_parser = subparsers.add_parser("diff", help="compare previous and refreshed research contracts")
    diff_parser.add_argument("old_contract")
    diff_parser.add_argument("new_contract")
    diff_parser.add_argument("--allow-invalid", action="store_true")
    diff_parser.set_defaults(func=command_diff)

    render_parser = subparsers.add_parser("render", help="render a deterministic Markdown report from a contract")
    render_parser.add_argument("contract")
    render_parser.add_argument("--output", required=True)
    render_parser.add_argument("--base")
    render_parser.add_argument("--allow-invalid", action="store_true")
    render_parser.add_argument("--force", action="store_true", help="replace an existing report")
    render_parser.set_defaults(func=command_render)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result: object = args.func(args)
        if type(result) is not int:
            raise TypeError("command handler returned a non-integer exit status")
        return result
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"operation failed: {display_safe_text(exc)}") from exc


if __name__ == "__main__":
    sys.exit(main())
