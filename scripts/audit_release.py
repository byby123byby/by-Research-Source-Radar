#!/usr/bin/env python3
"""Run a bounded release audit and record convergence in AUDIT_MANIFEST.json."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import stat
import subprocess  # nosec B404
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import research_contract as contract


MANIFEST_VERSION = 1
PROTOCOL_VERSION = 2
DEFAULT_MANIFEST_NAME = "AUDIT_MANIFEST.json"
DEFAULT_COMPLETENESS_NAME = "RELEASE_COMPLETENESS.json"
COMPLETENESS_SCHEMA_VERSION = 1
REQUIRED_CLEAN_ROUNDS = 2
MAX_AUDIT_FILES = 10_000
MAX_AUDIT_BYTES = 100_000_000
MAX_AUDIT_FILE_BYTES = 20_000_000
MAX_TOOL_BYTES = 500_000_000
MAX_HISTORY = 10
MAX_COMPLETENESS_REQUIREMENTS = 100
MAX_COMPLETENESS_CAPABILITIES = 100
MAX_COMPLETENESS_MARKER_CHARS = 1_000
INSTALLABLE_LOCATOR_ROOTS = frozenset(
    {"LICENSE", "SKILL.md", "RELEASE_COMPLETENESS.json", "agents", "references", "scripts"}
)
OPTIONAL_TOOLS = ("ruff", "pyflakes", "bandit", "mypy")
CACHE_PARTS = {".git", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
CACHE_NAMES = {".coverage", ".DS_Store"}


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    argv: tuple[str, ...]
    required: bool
    category: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256_bytes(payload)


def path_is_excluded(relative: Path, manifest_relative: Path | None) -> bool:
    if manifest_relative is not None and relative == manifest_relative:
        return True
    if any(part in CACHE_PARTS for part in relative.parts):
        return True
    return relative.name in CACHE_NAMES or relative.suffix == ".pyc"


def read_regular_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"audit scope contains a non-regular file: {path}")
        if before.st_size > MAX_AUDIT_FILE_BYTES:
            raise ValueError(f"audit file exceeds {MAX_AUDIT_FILE_BYTES} bytes: {path}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        before_state = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        after_state = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if before_state != after_state or len(payload) != before.st_size:
            raise RuntimeError(f"audit file changed while being read: {path}")
        return payload
    finally:
        os.close(descriptor)


def completeness_locator_text(
    root: Path,
    value: Any,
    path: str,
    errors: list[str],
    cache: dict[str, str],
    *,
    require_test: bool = False,
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object with path and marker")
        return
    if set(value) != {"path", "marker"}:
        errors.append(f"{path} must contain exactly path and marker")
    relative_text = value.get("path")
    marker = value.get("marker")
    if not isinstance(relative_text, str) or not relative_text.strip():
        errors.append(f"{path}.path must be a non-empty relative path")
        return
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        errors.append(f"{path}.path must stay inside the audit root")
        return
    if not relative.parts or relative.parts[0] not in INSTALLABLE_LOCATOR_ROOTS:
        errors.append(f"{path}.path must identify an installed Skill package entry")
        return
    if not isinstance(marker, str) or not marker.strip():
        errors.append(f"{path}.marker must be a non-empty string")
        return
    if len(marker) > MAX_COMPLETENESS_MARKER_CHARS:
        errors.append(f"{path}.marker exceeds {MAX_COMPLETENESS_MARKER_CHARS} characters")
        return
    if require_test and (not relative.name.startswith("test_") or not marker.startswith("def test_")):
        errors.append(f"{path} must reference a test_*.py file and def test_ selector")
    try:
        resolved = contract.bounded_evidence_file(relative_text, root)
    except ValueError as exc:
        errors.append(f"{path}: {exc}")
        return
    relative_key = relative.as_posix()
    content = cache.get(relative_key)
    if content is None:
        try:
            content = read_regular_bytes(resolved).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            errors.append(f"{path}.path must identify UTF-8 text: {relative_text}")
            return
        cache[relative_key] = content
    if marker not in content:
        errors.append(f"{path}.marker was not found in {relative_text}: {marker!r}")


def validate_release_completeness(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    matrix_path = root / DEFAULT_COMPLETENESS_NAME
    try:
        data = contract.load_json(matrix_path)
    except SystemExit as exc:
        return {
            "id": "release-completeness",
            "category": "traceability",
            "required": True,
            "status": "failed",
            "reason": str(exc),
            "errors": [str(exc)],
        }
    if not isinstance(data, dict):
        errors.append("release completeness root must be an object")
        data = {}
    if data.get("schema_version") != COMPLETENESS_SCHEMA_VERSION:
        errors.append(f"schema_version must be {COMPLETENESS_SCHEMA_VERSION}")
    if data.get("artifact") != "research-discovery-and-translation-audit":
        errors.append("artifact must identify research-discovery-and-translation-audit")

    cache: dict[str, str] = {}
    requirements = data.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append("requirements must be a non-empty list")
        requirements = []
    if len(requirements) > MAX_COMPLETENESS_REQUIREMENTS:
        errors.append(f"requirements exceeds {MAX_COMPLETENESS_REQUIREMENTS} entries")
    requirement_ids: set[str] = set()
    for index, raw in enumerate(requirements):
        item_path = f"requirements[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{item_path} must be an object")
            continue
        if set(raw) != {"id", "locator"}:
            errors.append(f"{item_path} must contain exactly id and locator")
        requirement_id = raw.get("id")
        if not isinstance(requirement_id, str) or not contract.STABLE_ID_PATTERN.fullmatch(requirement_id):
            errors.append(f"{item_path}.id must be a stable identifier")
        elif requirement_id in requirement_ids:
            errors.append(f"duplicate requirement id: {requirement_id}")
        else:
            requirement_ids.add(requirement_id)
        completeness_locator_text(root, raw.get("locator"), f"{item_path}.locator", errors, cache)

    required_locator_fields = (
        "public_claims",
        "data_representation",
        "triggers",
        "behaviors",
        "outputs",
        "positive_tests",
        "negative_tests",
        "migration_compatibility",
        "documentation",
    )
    expected_capability_fields = {
        "id",
        "requirement_ids",
        *required_locator_fields,
        "residual_boundaries",
    }
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        errors.append("capabilities must be a non-empty list")
        capabilities = []
    if len(capabilities) > MAX_COMPLETENESS_CAPABILITIES:
        errors.append(f"capabilities exceeds {MAX_COMPLETENESS_CAPABILITIES} entries")
    capability_ids: set[str] = set()
    covered_requirements: set[str] = set()
    for index, raw in enumerate(capabilities):
        item_path = f"capabilities[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{item_path} must be an object")
            continue
        if set(raw) != expected_capability_fields:
            errors.append(f"{item_path} fields do not match the required completeness schema")
        capability_id = raw.get("id")
        if not isinstance(capability_id, str) or not contract.STABLE_ID_PATTERN.fullmatch(capability_id):
            errors.append(f"{item_path}.id must be a stable identifier")
        elif capability_id in capability_ids:
            errors.append(f"duplicate capability id: {capability_id}")
        else:
            capability_ids.add(capability_id)
        linked_requirements = raw.get("requirement_ids")
        if not isinstance(linked_requirements, list) or not linked_requirements:
            errors.append(f"{item_path}.requirement_ids must be a non-empty list")
        else:
            seen_links: set[str] = set()
            for requirement_id in linked_requirements:
                if not isinstance(requirement_id, str):
                    errors.append(f"{item_path}.requirement_ids entries must be strings")
                elif requirement_id in seen_links:
                    errors.append(f"{item_path}.requirement_ids contains duplicate {requirement_id}")
                elif requirement_id not in requirement_ids:
                    errors.append(f"{item_path}.requirement_ids contains unknown {requirement_id}")
                else:
                    seen_links.add(requirement_id)
                    covered_requirements.add(requirement_id)
        for field in required_locator_fields:
            locators = raw.get(field)
            if not isinstance(locators, list) or not locators:
                errors.append(f"{item_path}.{field} must be a non-empty list")
                continue
            for locator_index, locator in enumerate(locators):
                completeness_locator_text(
                    root,
                    locator,
                    f"{item_path}.{field}[{locator_index}]",
                    errors,
                    cache,
                    require_test=field in {"positive_tests", "negative_tests"},
                )
        positive_raw = raw.get("positive_tests")
        negative_raw = raw.get("negative_tests")
        positive_items = positive_raw if isinstance(positive_raw, list) else []
        negative_items = negative_raw if isinstance(negative_raw, list) else []
        positive_keys = {
            (item.get("path"), item.get("marker"))
            for item in positive_items
            if isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and isinstance(item.get("marker"), str)
        }
        negative_keys = {
            (item.get("path"), item.get("marker"))
            for item in negative_items
            if isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and isinstance(item.get("marker"), str)
        }
        if positive_keys & negative_keys:
            errors.append(f"{item_path} must use distinct positive and negative tests")
        boundaries = raw.get("residual_boundaries")
        if not isinstance(boundaries, list) or not boundaries:
            errors.append(f"{item_path}.residual_boundaries must be a non-empty list")
        elif any(not isinstance(item, str) or not item.strip() for item in boundaries):
            errors.append(f"{item_path}.residual_boundaries entries must be non-empty strings")

    missing_requirements = sorted(requirement_ids - covered_requirements)
    if missing_requirements:
        errors.append(f"requirements are not covered by a capability: {', '.join(missing_requirements)}")
    return {
        "id": "release-completeness",
        "category": "traceability",
        "required": True,
        "status": "failed" if errors else "passed",
        "requirements": len(requirement_ids),
        "capabilities": len(capability_ids),
        "covered_requirements": len(covered_requirements),
        "matrix_sha256": canonical_sha256(data),
        "errors": errors,
        "reason": errors[0] if errors else "all declared requirements have complete source-to-outcome mappings",
    }


def hash_regular_file(path: Path, *, max_bytes: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise ValueError(f"tool executable is not a bounded regular file: {path}")
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"tool executable exceeds {max_bytes} bytes: {path}")
            digest.update(chunk)
        after = os.fstat(descriptor)
        before_state = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        after_state = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if before_state != after_state or total != before.st_size:
            raise RuntimeError(f"tool executable changed while being hashed: {path}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def collect_artifact(root: Path, manifest_path: Path) -> dict[str, Any]:
    lexical_root = Path(os.path.abspath(root.expanduser()))
    if lexical_root.is_symlink() or not lexical_root.is_dir():
        raise ValueError(f"audit root must be a real directory: {lexical_root}")
    root = lexical_root.resolve()
    try:
        manifest_relative = manifest_path.expanduser().resolve().relative_to(root)
    except ValueError:
        manifest_relative = None

    files: list[dict[str, Any]] = []
    total_bytes = 0

    def walk_error(error: OSError) -> None:
        raise RuntimeError(f"audit scope could not be enumerated: {error}") from error

    for current_root, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=walk_error,
        followlinks=False,
    ):
        current = Path(current_root)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            candidate = current / name
            relative = candidate.relative_to(root)
            if path_is_excluded(relative, manifest_relative):
                continue
            if candidate.is_symlink():
                raise ValueError(f"audit scope must not contain symbolic links: {relative.as_posix()}")
            if not candidate.is_dir():
                raise ValueError(f"audit scope contains an unsupported entry: {relative.as_posix()}")
            kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(file_names):
            candidate = current / name
            relative = candidate.relative_to(root)
            if path_is_excluded(relative, manifest_relative):
                continue
            if candidate.is_symlink():
                raise ValueError(f"audit scope must not contain symbolic links: {relative.as_posix()}")
            if not candidate.is_file():
                raise ValueError(f"audit scope contains an unsupported entry: {relative.as_posix()}")
            payload = read_regular_bytes(candidate)
            total_bytes += len(payload)
            if len(files) + 1 > MAX_AUDIT_FILES:
                raise ValueError(f"audit scope exceeds {MAX_AUDIT_FILES} files")
            if total_bytes > MAX_AUDIT_BYTES:
                raise ValueError(f"audit scope exceeds {MAX_AUDIT_BYTES} bytes")
            files.append({
                "path": relative.as_posix(),
                "size": len(payload),
                "sha256": sha256_bytes(payload),
            })

    if not files:
        raise ValueError("audit scope contains no files")
    artifact_sha256 = canonical_sha256(files)
    return {
        "root": ".",
        "artifact_sha256": artifact_sha256,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "excluded": [
            DEFAULT_MANIFEST_NAME,
            ".git/",
            "Python/tool caches",
            "*.pyc",
        ],
    }


def probe_tool_version(argv: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(  # nosec B603
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "available-version-unknown"
    return concise_output(completed.stdout) or "available-version-unknown"


def tool_info(module_name: str) -> dict[str, str] | None:
    if importlib.util.find_spec(module_name) is not None:
        distribution_name = "pyflakes" if module_name == "pyflakes" else module_name
        try:
            version = importlib.metadata.version(distribution_name)
        except importlib.metadata.PackageNotFoundError:
            version = probe_tool_version((sys.executable, "-m", module_name, "--version"))
        executable = str(Path(sys.executable).resolve())
        return {
            "mode": "module",
            "executable": executable,
            "executable_sha256": hash_regular_file(Path(executable), max_bytes=MAX_TOOL_BYTES),
            "version": version,
        }
    executable_candidate = shutil.which(module_name)
    if executable_candidate is None:
        return None
    resolved = str(Path(executable_candidate).resolve())
    return {
        "mode": "binary",
        "executable": resolved,
        "executable_sha256": hash_regular_file(Path(resolved), max_bytes=MAX_TOOL_BYTES),
        "version": probe_tool_version((resolved, "--version")),
    }


def tool_inventory() -> dict[str, dict[str, str] | None]:
    return {name: tool_info(name) for name in OPTIONAL_TOOLS}


def tool_prefix(name: str, tools: dict[str, dict[str, str] | None]) -> tuple[str, ...]:
    info = tools.get(name)
    if info is None:
        return (sys.executable, "-m", name)
    if info.get("mode") == "module":
        return (sys.executable, "-m", name)
    return (info["executable"],)


def build_check_specs(
    root: Path,
    *,
    strict_tools: bool,
    tools: dict[str, dict[str, str] | None],
) -> list[CheckSpec]:
    python = sys.executable
    scripts = root / "scripts"
    tests = tuple(sorted(str(path) for path in scripts.glob("test_*.py")))
    production = tuple(
        str(path)
        for path in sorted(scripts.glob("*.py"))
        if not path.name.startswith("test_")
    )
    specs = [
        CheckSpec("compile", (python, "-m", "compileall", "-q", "-f", str(scripts)), True, "runtime"),
        CheckSpec(
            "unit-adversarial-security-tests",
            (python, "-m", "unittest", "discover", "-s", str(scripts), "-p", "test_*.py"),
            True,
            "behavior",
        ),
    ]
    optional_commands = {
        "ruff": (*tool_prefix("ruff", tools), "check", str(root)),
        "pyflakes": (*tool_prefix("pyflakes", tools), *production, *tests),
        "bandit": (*tool_prefix("bandit", tools), "-q", "-r", str(scripts), "-x", ",".join(tests)),
        "mypy": (*tool_prefix("mypy", tools), "--strict", *production),
    }
    for name in OPTIONAL_TOOLS:
        required = strict_tools
        argv = optional_commands[name]
        if tools.get(name) is None:
            specs.append(CheckSpec(name, argv, required, "static-analysis"))
        else:
            specs.append(CheckSpec(name, argv, True, "static-analysis"))
    return specs


def concise_output(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:1000] if lines else ""


def run_check(
    spec: CheckSpec,
    *,
    root: Path,
    timeout: float,
    tools: dict[str, dict[str, str] | None],
) -> dict[str, Any]:
    tool_name = spec.check_id if spec.check_id in OPTIONAL_TOOLS else None
    if tool_name is not None and tools.get(tool_name) is None:
        return {
            "id": spec.check_id,
            "category": spec.category,
            "required": spec.required,
            "status": "failed" if spec.required else "not_run",
            "reason": "tool is unavailable as a current-Python module or PATH executable",
        }

    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="research-audit-pycache-") as cache:
        environment["PYTHONPYCACHEPREFIX"] = cache
        started = time.monotonic()
        try:
            completed = subprocess.run(  # nosec B603
                spec.argv,
                cwd=root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            output = completed.stdout
            return {
                "id": spec.check_id,
                "category": spec.category,
                "required": spec.required,
                "status": "passed" if completed.returncode == 0 else "failed",
                "returncode": completed.returncode,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "output_sha256": sha256_bytes(output.encode("utf-8")),
                "summary": concise_output(output),
                "failure_tail": output[-8000:] if completed.returncode else "",
            }
        except subprocess.TimeoutExpired as exc:
            combined = ""
            if isinstance(exc.stdout, bytes):
                combined = exc.stdout.decode("utf-8", errors="replace")
            elif isinstance(exc.stdout, str):
                combined = exc.stdout
            return {
                "id": spec.check_id,
                "category": spec.category,
                "required": spec.required,
                "status": "failed",
                "reason": f"timed out after {timeout:g} seconds",
                "duration_ms": int((time.monotonic() - started) * 1000),
                "failure_tail": combined[-8000:],
            }


def audit_matrix() -> list[dict[str, str]]:
    return [
        {"area": "package-integrity", "evidence": "bounded file inventory, hashes, type and symlink checks"},
        {"area": "runtime", "evidence": "clean compile under the recorded Python runtime"},
        {"area": "behavior-security", "evidence": "unit, adversarial, discovery-contract, installer, and security regression suite"},
        {"area": "source-to-outcome-completeness", "evidence": "machine matrix binds requirements, claims, data, triggers, behavior, outputs, tests, compatibility, documentation, and residual boundaries"},
        {"area": "static-analysis", "evidence": "Ruff, Pyflakes, Bandit, and strict mypy when required"},
        {"area": "documentation", "evidence": "automated link, anchor, bilingual-parity, and claim-boundary tests"},
        {"area": "retrieval-effectiveness-framework", "evidence": "paired condition isolation, frozen tasks, blind pooling, strict scoring inputs, and bounded claims"},
        {"area": "convergence", "evidence": "two consecutive clean rounds over the same artifact and profile hash"},
    ]


def profile_hash(
    artifact_sha256: str,
    *,
    strict_tools: bool,
    tools: dict[str, dict[str, str] | None],
    check_ids: list[str],
) -> str:
    profile = {
        "protocol_version": PROTOCOL_VERSION,
        "artifact_sha256": artifact_sha256,
        "strict_tools": strict_tools,
        "platform": platform.platform(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_implementation": platform.python_implementation(),
        "tools": tools,
        "check_ids": check_ids,
    }
    return canonical_sha256(profile)


def load_previous_manifest(
    path: Path,
    *,
    reset: bool,
) -> tuple[dict[str, Any] | None, contract.FileState | None]:
    if not path.exists() and not path.is_symlink():
        return None, None
    if path.is_symlink():
        raise ValueError(f"audit manifest must not be a symbolic link: {path}")
    if reset:
        state = contract.current_regular_file_state(path)
        if state is None:
            raise ValueError(f"audit manifest disappeared while resetting history: {path}")
        return None, state
    try:
        previous, state = contract.load_json_with_state(path)
    except SystemExit as exc:
        raise ValueError(f"existing audit manifest is invalid; use --reset-history: {exc}") from exc
    if previous.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError("existing audit manifest has an unsupported version; use --reset-history")
    return previous, state


def next_clean_streak(previous: dict[str, Any] | None, current_profile_hash: str, *, clean: bool) -> int:
    if not clean:
        return 0
    if previous is None or previous.get("profile_sha256") != current_profile_hash:
        return 1
    prior_streak = previous.get("clean_streak")
    if not isinstance(prior_streak, int) or isinstance(prior_streak, bool) or prior_streak < 1:
        return 1
    if previous.get("result") not in {"CLEAN_ROUND_1", "PASS_CONVERGED"}:
        return 1
    return min(prior_streak + 1, REQUIRED_CLEAN_ROUNDS)


def prior_history(previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    if previous is None:
        return []
    value = previous.get("history")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)][-(MAX_HISTORY - 1):]


def run_audit(args: argparse.Namespace) -> int:
    root = Path(os.path.abspath(Path(args.root).expanduser()))
    manifest_path = Path(args.manifest).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest_path = Path(os.path.abspath(manifest_path))

    previous, manifest_state = load_previous_manifest(manifest_path, reset=args.reset_history)
    artifact = collect_artifact(root, manifest_path)
    tools = tool_inventory()
    specs = build_check_specs(root, strict_tools=args.strict_tools, tools=tools)
    completeness_check = validate_release_completeness(root)
    current_profile_hash = profile_hash(
        artifact["artifact_sha256"],
        strict_tools=args.strict_tools,
        tools=tools,
        check_ids=["release-completeness", *(spec.check_id for spec in specs)],
    )
    checks = [
        completeness_check,
        *(run_check(spec, root=root, timeout=args.timeout, tools=tools) for spec in specs),
    ]
    errors = [
        f"{check['id']}: {check.get('reason') or check.get('summary') or 'check failed'}"
        for check in checks
        if check.get("status") == "failed"
    ]
    artifact_after = collect_artifact(root, manifest_path)
    if artifact_after["artifact_sha256"] != artifact["artifact_sha256"]:
        errors.append("artifact changed while the audit was running")
    if tool_inventory() != tools:
        errors.append("static-analysis tool identity changed while the audit was running")
    not_run = [str(check["id"]) for check in checks if check.get("status") == "not_run"]
    clean = not errors
    streak = next_clean_streak(previous, current_profile_hash, clean=clean)
    if not clean:
        result = "FAIL"
    elif streak >= REQUIRED_CLEAN_ROUNDS:
        result = "PASS_CONVERGED"
    else:
        result = "CLEAN_ROUND_1"

    history = prior_history(previous)
    history.append({
        "audited_at": utc_now(),
        "profile_sha256": current_profile_hash,
        "artifact_sha256": artifact["artifact_sha256"],
        "clean": clean,
        "result": result,
    })
    uncovered = [
        "Unknown defects outside the declared audit matrix",
        "Platforms not executed in this audit environment",
        "Live third-party service behavior and future dependency changes",
        "Scientific validity and exhaustive discovery",
        "Seed-to-neighbor discovery recall, precision, and user benefit",
        "Retrieval effectiveness until the frozen A/B trials are completed and independently judged",
        "The completeness matrix cannot prove that its declared requirement inventory is exhaustive",
    ]
    if not_run:
        uncovered.append(f"Optional static analyzers not run: {', '.join(not_run)}")

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "skill": "research-discovery-and-translation-audit",
        "audited_at": utc_now(),
        "result": result,
        "clean_streak": streak,
        "required_clean_rounds": REQUIRED_CLEAN_ROUNDS,
        "profile_sha256": current_profile_hash,
        "artifact": artifact,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "strict_tools": args.strict_tools,
            "tool_versions": tools,
        },
        "audit_matrix": audit_matrix(),
        "checks": checks,
        "findings": {"errors": errors, "warnings": []},
        "uncovered": uncovered,
        "stop_reason": (
            "Two consecutive clean rounds used the same frozen artifact and audit profile."
            if result == "PASS_CONVERGED"
            else "A second unchanged clean round is required before convergence."
            if result == "CLEAN_ROUND_1"
            else "One or more required checks failed."
        ),
        "history": history[-MAX_HISTORY:],
        "claim_boundary": (
            "PASS_CONVERGED means the recorded checks passed twice without an artifact or profile change. "
            "It is not proof that no unknown defect exists."
        ),
    }
    contract.write_json_atomic(manifest_path, manifest, expected_state=manifest_state)
    saved = contract.load_json(manifest_path)
    if saved.get("profile_sha256") != current_profile_hash or saved.get("result") != result:
        raise RuntimeError("audit manifest verification failed after write")
    print(result)
    print(f"artifact_sha256={artifact['artifact_sha256']}")
    print(f"clean_streak={streak}/{REQUIRED_CLEAN_ROUNDS}")
    print(f"manifest={manifest_path}")
    for error in errors:
        print(f"ERROR: {error}")
    for check_id in not_run:
        print(f"NOT_RUN: {check_id}")
    return 0 if clean else 1


def positive_timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not parsed > 0 or parsed == float("inf"):
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST_NAME)
    parser.add_argument("--timeout", type=positive_timeout, default=180.0)
    parser.add_argument(
        "--strict-tools",
        action="store_true",
        help="require Ruff, Pyflakes, Bandit, and mypy instead of recording missing tools as uncovered",
    )
    parser.add_argument("--reset-history", action="store_true")
    parser.set_defaults(func=run_audit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
