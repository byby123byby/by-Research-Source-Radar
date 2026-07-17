#!/usr/bin/env python3
"""Install the portable skill package into supported agent host locations."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Callable


SKILL_NAME = "research-discovery-and-translation-audit"
PACKAGE_FILE_ENTRIES = ("LICENSE", "SKILL.md", "RELEASE_COMPLETENESS.json")
PACKAGE_DIRECTORY_ENTRIES = ("agents", "references", "scripts")
PACKAGE_ENTRIES = (*PACKAGE_FILE_ENTRIES, *PACKAGE_DIRECTORY_ENTRIES)
USER_TARGETS = {
    "codex-user": Path.home() / ".codex" / "skills" / SKILL_NAME,
    "claude-user": Path.home() / ".claude" / "skills" / SKILL_NAME,
    "copilot-user": Path.home() / ".copilot" / "skills" / SKILL_NAME,
    "agents-user": Path.home() / ".agents" / "skills" / SKILL_NAME,
}
PROJECT_TARGETS = {
    "agents-project": Path(".agents") / "skills" / SKILL_NAME,
    "claude-project": Path(".claude") / "skills" / SKILL_NAME,
    "github-project": Path(".github") / "skills" / SKILL_NAME,
}
MANAGED_BEGIN = f"<!-- {SKILL_NAME}:begin -->"
MANAGED_END = f"<!-- {SKILL_NAME}:end -->"
MAX_PACKAGE_FILES = 5_000
MAX_PACKAGE_BYTES = 50_000_000
MAX_INSTRUCTION_BYTES = 5_000_000
MAX_DESTINATION_STATE_ENTRIES = 10_000
InstructionState = tuple[int, int, int, int, int]
PackageState = str
PathIdentity = tuple[int, int, int]
BackupRecord = tuple[Path, PackageState, PathIdentity]
NO_EXPECTED_STATE = object()


class CommittedWriteSyncError(RuntimeError):
    def __init__(self, path: Path, installed_state: InstructionState, cause: OSError) -> None:
        super().__init__(f"instruction file was replaced, but its parent directory could not be synchronized: {path}")
        self.path = path
        self.installed_state = installed_state
        self.__cause__ = cause


def display_safe_text(value: object) -> str:
    output: list[str] = []
    directional_controls = {
        0x061C,
        0x200E,
        0x200F,
        0x2028,
        0x2029,
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    }
    for character in str(value):
        codepoint = ord(character)
        if codepoint < 0x20 or 0x7F <= codepoint <= 0x9F or codepoint in directional_controls:
            width = 4 if codepoint <= 0xFFFF else 8
            output.append(f"\\u{codepoint:0{width}X}")
        else:
            output.append(character)
    return "".join(output)


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def target_path(target: str, project: Path | None = None) -> Path:
    if target in USER_TARGETS:
        return USER_TARGETS[target].expanduser()
    if target in PROJECT_TARGETS:
        if project is None:
            raise ValueError(f"--project is required for {target}")
        return project.expanduser().resolve() / PROJECT_TARGETS[target]
    if target == "portable-project":
        if project is None:
            raise ValueError("--project is required for portable-project")
        return project.expanduser().resolve() / ".agent-skills" / SKILL_NAME
    raise ValueError(f"unsupported target: {target}")


def path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def path_identity(path: Path) -> PathIdentity | None:
    if not path_present(path):
        return None
    info = path.lstat()
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def remove_owned_staging(path: Path, expected_identity: PathIdentity) -> None:
    current_identity = path_identity(path)
    if current_identity is None:
        return
    if current_identity != expected_identity:
        raise RuntimeError(f"refusing to remove replaced staging path: {path}")
    remove_path(path)
    fsync_directory(path.parent)


def verify_backup_record(backup: BackupRecord) -> Path:
    path, expected_state, expected_identity = backup
    if path_identity(path) != expected_identity or package_path_state(path) != expected_state:
        raise RuntimeError(f"backup changed concurrently: {path}")
    return path


def remove_owned_backup(backup: BackupRecord) -> None:
    path = verify_backup_record(backup)
    remove_path(path)
    fsync_directory(path.parent)


def package_path_state(path: Path) -> PackageState | None:
    if not path_present(path):
        return None
    digest = hashlib.sha256()
    entries = 0

    def add_entry(candidate: Path, relative: str) -> None:
        nonlocal entries
        entries += 1
        if entries > MAX_DESTINATION_STATE_ENTRIES:
            raise RuntimeError(
                f"destination exceeds {MAX_DESTINATION_STATE_ENTRIES} entries while checking concurrent changes: {path}"
            )
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise RuntimeError(f"destination changed while being inspected: {path}: {exc}") from exc
        state = instruction_file_state(info)
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(stat.S_IFMT(info.st_mode)).encode("ascii"))
        digest.update(b"\0")
        digest.update(":".join(str(value) for value in state).encode("ascii"))
        digest.update(b"\n")

    add_entry(path, ".")
    if path.is_dir() and not path.is_symlink():
        def fail_walk(error: OSError) -> None:
            raise RuntimeError(f"destination could not be fully enumerated: {path}: {error}") from error

        try:
            walker = os.walk(path, topdown=True, onerror=fail_walk, followlinks=False)
            for current_root, directory_names, file_names in walker:
                directory_names.sort()
                file_names.sort()
                current = Path(current_root)
                for name in directory_names:
                    candidate = current / name
                    add_entry(candidate, candidate.relative_to(path).as_posix())
                for name in file_names:
                    candidate = current / name
                    add_entry(candidate, candidate.relative_to(path).as_posix())
        except OSError as exc:
            raise RuntimeError(f"destination changed while being enumerated: {path}: {exc}") from exc
    return digest.hexdigest()


def validate_package_source(source: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"skill package root is not a directory: {source}")
    file_count = 0
    total_bytes = 0
    for name in PACKAGE_ENTRIES:
        item = source / name
        if not path_present(item):
            raise FileNotFoundError(f"skill package is missing required entry: {item}")
        if item.is_symlink():
            raise ValueError(f"skill package must not contain symbolic links: {item}")
        if name in PACKAGE_FILE_ENTRIES and not item.is_file():
            raise ValueError(f"skill package entry {name} must be a regular file: {item}")
        if name in PACKAGE_DIRECTORY_ENTRIES and not item.is_dir():
            raise ValueError(f"skill package entry {name} must be a directory: {item}")
        paths = [item]
        if item.is_dir():
            paths.extend(item.rglob("*"))
        for candidate in paths:
            if candidate.is_symlink():
                raise ValueError(f"skill package must not contain symbolic links: {candidate}")
            if not candidate.is_file() and not candidate.is_dir():
                raise ValueError(f"skill package must contain only regular files and directories: {candidate}")
            if candidate.is_file():
                file_count += 1
                total_bytes += candidate.stat().st_size
                if file_count > MAX_PACKAGE_FILES:
                    raise ValueError(f"skill package exceeds {MAX_PACKAGE_FILES} files")
                if total_bytes > MAX_PACKAGE_BYTES:
                    raise ValueError(f"skill package exceeds {MAX_PACKAGE_BYTES} bytes")


def reject_symlinked_parent_components(root: Path, destination: Path) -> None:
    resolved_root = root.expanduser().resolve()
    lexical_destination = Path(os.path.abspath(destination.expanduser()))
    if not lexical_destination.is_relative_to(resolved_root):
        raise ValueError(f"project destination escapes project root: {destination}")
    current = resolved_root
    for component in lexical_destination.relative_to(resolved_root).parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"project destination contains a symbolic-link path component: {current}")


def stage_package(source: Path, destination: Path, *, force: bool) -> Path | None:
    source = source.resolve()
    destination = destination.expanduser()
    if path_present(destination) and destination.resolve() == source:
        return None
    if path_present(destination) and not force:
        raise FileExistsError(f"destination exists: {destination}; rerun with --force to replace it")

    validate_package_source(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}.stage-", dir=str(destination.parent)))
    staging_identity = path_identity(staging)
    if staging_identity is None:
        raise RuntimeError(f"staging directory disappeared after creation: {staging}")
    try:
        for name in PACKAGE_ENTRIES:
            item = source / name
            output = staging / name
            if item.is_dir():
                shutil.copytree(
                    item,
                    output,
                    symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
                )
            else:
                shutil.copy2(item, output)
        validate_package_source(staging)
        return staging
    except Exception:
        remove_owned_staging(staging, staging_identity)
        raise


def commit_staged_package(
    staging: Path,
    destination: Path,
    *,
    expected_state: PackageState | None,
    expected_staging_state: PackageState,
) -> tuple[BackupRecord | None, PackageState]:
    if package_path_state(destination) != expected_state:
        raise RuntimeError(f"destination changed concurrently: {destination}")
    if package_path_state(staging) != expected_staging_state:
        raise RuntimeError(f"staged package changed concurrently: {staging}")
    backup: BackupRecord | None = None
    if path_present(destination):
        if expected_state is None:
            raise RuntimeError(f"destination appeared concurrently: {destination}")
        destination_identity = path_identity(destination)
        if destination_identity is None:
            raise RuntimeError(f"destination disappeared concurrently: {destination}")
        backup_path = destination.with_name(f".{destination.name}.backup-{uuid.uuid4().hex}")
        os.replace(destination, backup_path)
        try:
            if path_identity(backup_path) != destination_identity:
                raise RuntimeError(f"backup changed concurrently: {backup_path}")
            backup_state = package_path_state(backup_path)
            if backup_state is None:
                raise RuntimeError(f"backup disappeared after creation: {backup_path}")
            backup = (backup_path, backup_state, destination_identity)
        except Exception:
            if (
                path_identity(backup_path) == destination_identity
                and not path_present(destination)
            ):
                os.replace(backup_path, destination)
                fsync_directory(destination.parent)
            raise
    try:
        os.replace(staging, destination)
    except Exception:
        if backup is not None and path_present(backup[0]) and not path_present(destination):
            backup_path = verify_backup_record(backup)
            os.replace(backup_path, destination)
            fsync_directory(destination.parent)
        raise
    installed_state = package_path_state(destination)
    if installed_state is None:
        raise RuntimeError(f"installed package disappeared after commit: {destination}")
    try:
        fsync_directory(destination.parent)
    except OSError as sync_error:
        rollback_errors: list[str] = []
        try:
            if package_path_state(destination) != installed_state:
                raise RuntimeError("installed package changed before durability rollback")
            remove_path(destination)
            if backup is not None and path_present(backup[0]):
                backup_path = verify_backup_record(backup)
                os.replace(backup_path, destination)
            fsync_directory(destination.parent)
        except Exception as rollback_error:
            rollback_errors.append(str(rollback_error))
        if rollback_errors:
            raise RuntimeError(
                "package was replaced but directory synchronization failed; "
                f"durability rollback also failed: {'; '.join(rollback_errors)}"
            ) from sync_error
        raise RuntimeError("package commit was rolled back because directory synchronization failed") from sync_error
    return backup, installed_state


def rollback_package(destination: Path, backup: BackupRecord | None, installed_state: PackageState) -> None:
    backup_path = verify_backup_record(backup) if backup is not None else None
    if package_path_state(destination) != installed_state:
        raise RuntimeError(f"installed package changed concurrently during rollback: {destination}")
    remove_path(destination)
    if backup_path is not None:
        os.replace(backup_path, destination)
    fsync_directory(destination.parent)


def copy_package(
    source: Path,
    destination: Path,
    *,
    force: bool,
    precommit: Callable[[], None] | None = None,
) -> bool:
    destination_state = package_path_state(destination)
    staging = stage_package(source, destination, force=force)
    if staging is None:
        return False
    staging_state = package_path_state(staging)
    staging_identity = path_identity(staging)
    if staging_state is None:
        raise RuntimeError(f"staged package disappeared before commit: {staging}")
    if staging_identity is None:
        raise RuntimeError(f"staging directory disappeared before commit: {staging}")
    try:
        if precommit is not None:
            precommit()
        backup, _ = commit_staged_package(
            staging,
            destination,
            expected_state=destination_state,
            expected_staging_state=staging_state,
        )
    except Exception:
        if path_present(staging):
            remove_owned_staging(staging, staging_identity)
        raise
    if backup is not None:
        try:
            remove_owned_backup(backup)
        except Exception as exc:
            raise RuntimeError(
                f"installation committed at {destination}, but old-package cleanup failed at {backup[0]}: {exc}"
            ) from exc
    return True


def instruction_block(relative_skill_path: str) -> str:
    return "\n".join([
        MANAGED_BEGIN,
        "## Research Discovery and Translation Audit",
        "",
        "For research discovery, source verification, mechanism translation, refresh, or reverse-audit tasks,",
        f"read `{relative_skill_path}` and follow it as the governing workflow.",
        "Keep its candidate IDs, source-verification gates, evidence records, unresolved gaps, and bounded claims.",
        "Do not replace it with a generic literature-search response.",
        MANAGED_END,
    ])


def file_mode(path: Path, *, default: int) -> int:
    if path.is_symlink():
        raise ValueError(f"refusing to replace symbolic link: {path}")
    if path.exists():
        if not path.is_file():
            raise ValueError(f"expected a regular file: {path}")
        return stat.S_IMODE(path.stat().st_mode) & 0o777
    return default


def instruction_file_state(info: os.stat_result) -> InstructionState:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
        getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)),
    )


def read_instruction_file(path: Path) -> tuple[str | None, int | None, InstructionState | None]:
    if not path_present(path):
        return None, None, None
    try:
        initial = path.lstat()
    except OSError as exc:
        raise ValueError(f"instruction file could not be inspected: {path}: {exc}") from exc
    if stat.S_ISLNK(initial.st_mode):
        raise ValueError(f"refusing to read symbolic link: {path}")
    if not stat.S_ISREG(initial.st_mode):
        raise ValueError(f"instruction file must be a regular file: {path}")
    if initial.st_size > MAX_INSTRUCTION_BYTES:
        raise ValueError(f"instruction file exceeds {MAX_INSTRUCTION_BYTES} bytes: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"instruction file could not be opened safely: {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"instruction file must be a regular file: {path}")
        if opened.st_size > MAX_INSTRUCTION_BYTES:
            raise ValueError(f"instruction file exceeds {MAX_INSTRUCTION_BYTES} bytes: {path}")
        if instruction_file_state(initial) != instruction_file_state(opened):
            raise ValueError(f"instruction file changed while it was being opened: {path}")
        payload = bytearray()
        while len(payload) <= MAX_INSTRUCTION_BYTES:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_INSTRUCTION_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > MAX_INSTRUCTION_BYTES:
            raise ValueError(f"instruction file exceeds {MAX_INSTRUCTION_BYTES} bytes: {path}")
        if instruction_file_state(opened) != instruction_file_state(os.fstat(descriptor)):
            raise ValueError(f"instruction file changed while it was being read: {path}")
    finally:
        os.close(descriptor)
    try:
        return (
            bytes(payload).decode("utf-8"),
            stat.S_IMODE(opened.st_mode) & 0o777,
            instruction_file_state(opened),
        )
    except UnicodeDecodeError as exc:
        raise UnicodeError(f"instruction file is not valid UTF-8: {path}") from exc


def current_instruction_state(path: Path) -> InstructionState | None:
    if not path_present(path):
        return None
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"refusing to replace symbolic link: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"expected a regular file: {path}")
    return instruction_file_state(info)


def write_text_atomic(
    path: Path,
    text: str,
    *,
    mode: int | None = None,
    expected_state: InstructionState | None | object = NO_EXPECTED_STATE,
) -> InstructionState:
    path.parent.mkdir(parents=True, exist_ok=True)
    current_mode = file_mode(path, default=0o644)
    selected_mode = current_mode if mode is None else mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        try:
            os.chmod(temporary, selected_mode)
            handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_state is not NO_EXPECTED_STATE and current_instruction_state(path) != expected_state:
            raise RuntimeError(f"instruction file changed concurrently: {path}")
        os.replace(temporary, path)
        installed_state = instruction_file_state(path.stat())
        try:
            fsync_directory(path.parent)
        except OSError as exc:
            raise CommittedWriteSyncError(path, installed_state, exc) from exc
        return installed_state
    finally:
        temporary.unlink(missing_ok=True)


def render_managed_text(existing: str, block: str) -> str:
    begin_count = existing.count(MANAGED_BEGIN)
    end_count = existing.count(MANAGED_END)
    if begin_count != end_count or begin_count > 1:
        raise ValueError("unbalanced or duplicate managed instruction markers")
    if begin_count == 1:
        start = existing.index(MANAGED_BEGIN)
        end = existing.index(MANAGED_END, start) + len(MANAGED_END)
        updated = existing[:start].rstrip() + "\n\n" + block + existing[end:]
    else:
        updated = existing.rstrip()
        if updated:
            updated += "\n\n"
        updated += block
    return updated.rstrip() + "\n"


def upsert_managed_block(path: Path, block: str) -> None:
    existing, _, state = read_instruction_file(path)
    write_text_atomic(path, render_managed_text(existing or "", block), expected_state=state)


def restore_managed_file(
    path: Path,
    previous_text: str | None,
    previous_mode: int | None,
    installed_state: InstructionState,
) -> None:
    if previous_text is None:
        if current_instruction_state(path) != installed_state:
            raise RuntimeError(f"instruction file changed concurrently during rollback: {path}")
        remove_path(path)
    else:
        write_text_atomic(path, previous_text, mode=previous_mode, expected_state=installed_state)


def install(target: str, *, project: Path | None, force: bool, source: Path | None = None) -> Path:
    source_root = (source or package_root()).resolve()
    destination = target_path(target, project)
    project_guard: Callable[[], None] | None = None
    if target in PROJECT_TARGETS or target == "portable-project":
        if project is None:
            raise ValueError(f"--project is required for {target}")
        guarded_project = project

        def recheck_project_path() -> None:
            reject_symlinked_parent_components(guarded_project, destination.parent)

        project_guard = recheck_project_path
        project_guard()
    managed_updates: list[tuple[Path, str, str | None, int | None, InstructionState | None]] = []
    if target == "portable-project":
        project_root = project.expanduser().resolve() if project is not None else None
        if project_root is None:
            raise ValueError("--project is required for portable-project")
        relative_skill = f".agent-skills/{SKILL_NAME}/SKILL.md"
        block = instruction_block(relative_skill)
        for path in (project_root / "AGENTS.md", project_root / "GEMINI.md"):
            previous_text, previous_mode, previous_state = read_instruction_file(path)
            managed_updates.append(
                (path, render_managed_text(previous_text or "", block), previous_text, previous_mode, previous_state)
            )

    if not managed_updates:
        copy_package(source_root, destination, force=force, precommit=project_guard)
        return destination

    destination_state = package_path_state(destination)
    staging = stage_package(source_root, destination, force=force)
    staging_state = package_path_state(staging) if staging is not None else None
    staging_identity = path_identity(staging) if staging is not None else None
    if staging is not None and staging_state is None:
        raise RuntimeError(f"staged package disappeared before commit: {staging}")
    if staging is not None and staging_identity is None:
        raise RuntimeError(f"staging directory disappeared before commit: {staging}")
    backup: BackupRecord | None = None
    installed_package_state: PackageState | None = None
    package_committed = False
    written_paths: list[tuple[Path, str | None, int | None, InstructionState]] = []
    try:
        if staging is not None:
            if project_guard is not None:
                project_guard()
            if staging_state is None:
                raise RuntimeError("staged package state was not recorded")
            backup, installed_package_state = commit_staged_package(
                staging,
                destination,
                expected_state=destination_state,
                expected_staging_state=staging_state,
            )
            package_committed = True
        for path, updated, previous_text, previous_mode, previous_state in managed_updates:
            if project is not None:
                reject_symlinked_parent_components(project, path.parent)
            try:
                installed_state = write_text_atomic(path, updated, expected_state=previous_state)
            except CommittedWriteSyncError as write_error:
                written_paths.append((path, previous_text, previous_mode, write_error.installed_state))
                raise
            written_paths.append((path, previous_text, previous_mode, installed_state))
    except Exception as exc:
        rollback_errors: list[str] = []
        for path, previous_text, previous_mode, installed_state in reversed(written_paths):
            try:
                restore_managed_file(path, previous_text, previous_mode, installed_state)
            except Exception as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        try:
            if package_committed:
                if installed_package_state is None:
                    raise RuntimeError("committed package state was not recorded")
                rollback_package(destination, backup, installed_package_state)
            elif staging is not None and path_present(staging):
                if staging_identity is None:
                    raise RuntimeError("staging directory identity was not recorded")
                remove_owned_staging(staging, staging_identity)
        except Exception as rollback_exc:
            rollback_errors.append(f"{destination}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                f"installation failed: {exc}; rollback also failed: {'; '.join(rollback_errors)}"
            ) from exc
        raise
    if backup is not None:
        try:
            remove_owned_backup(backup)
        except Exception as exc:
            raise RuntimeError(
                f"installation committed at {destination}, but old-package cleanup failed at {backup[0]}: {exc}"
            ) from exc
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        required=True,
        choices=sorted((*USER_TARGETS, *PROJECT_TARGETS, "portable-project")),
    )
    parser.add_argument("--project", type=Path, help="project root for project targets")
    parser.add_argument("--force", action="store_true", help="replace an existing installed skill package")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        destination = install(args.target, project=args.project, force=args.force)
    except (FileExistsError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"installation failed: {display_safe_text(exc)}") from exc
    print(f"installed {SKILL_NAME}: {display_safe_text(destination)}")
    print(f"RESEARCH_AUDIT_SKILL_DIR={display_safe_text(destination)}")
    if args.target == "portable-project":
        print("updated managed instruction blocks: AGENTS.md, GEMINI.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
