# Host and IDE Portability

## Contents

- [Compatibility model](#compatibility-model)
- [Native Agent Skills hosts](#native-agent-skills-hosts)
- [Instruction-file hosts](#instruction-file-hosts)
- [Standalone CLI](#standalone-cli)
- [Installer targets](#installer-targets)
- [Boundaries](#boundaries)
- [Official host references](#official-host-references)

## Compatibility model

An IDE is the editor; the AI agent or extension determines how reusable instructions are loaded. This package therefore separates three layers:

1. `SKILL.md`, references, and scripts are the portable core.
2. Native Agent Skills hosts load the complete directory from their own skill path.
3. Other hosts receive a small `AGENTS.md` or `GEMINI.md` entry that points to the same portable core.

Do not claim that every IDE automatically discovers `SKILL.md`. An IDE without an AI agent can still run the Python CLI from its terminal.

## Native Agent Skills hosts

The same package can be installed without rewriting `SKILL.md`:

| Host | User location | Project location |
| --- | --- | --- |
| Codex | `~/.codex/skills/<skill>` | Host-dependent workspace skill location |
| Claude Code | `~/.claude/skills/<skill>` | `.claude/skills/<skill>` |
| GitHub Copilot | `~/.copilot/skills/<skill>` or `~/.agents/skills/<skill>` | `.github/skills/<skill>`, `.agents/skills/<skill>`, or `.claude/skills/<skill>` |

Claude Code documents skills as directories containing `SKILL.md` plus optional supporting resources. GitHub Copilot documents the same package shape for its cloud coding agent, code review, Copilot CLI, Copilot app, and VS Code agent mode. Do not extend that native-loading claim to every Copilot IDE integration without checking GitHub's current support matrix.

## Instruction-file hosts

- Cursor CLI reads a root `AGENTS.md` as project-wide Agent instructions; other Cursor surfaces may differ.
- GitHub Copilot instruction-file support varies by surface and file type; use GitHub's current matrix instead of assuming parity.
- Gemini Code Assist supports project or user-scoped `GEMINI.md` context files.

The `portable-project` installer target copies the core into `.agent-skills/<skill>` and upserts one marked instruction block into both `AGENTS.md` and `GEMINI.md`. It preserves unrelated existing content and refuses malformed duplicate markers.

## Standalone CLI

The research-contract CLI requires Python 3.10 or later and uses only the Python standard library. From any IDE terminal:

```bash
python3 /path/to/research-discovery-and-translation-audit/scripts/research_contract.py --help
```

The CLI does not require an AI extension. The substantive literature search still requires the user or an agent to access appropriate databases, repositories, and official metadata services.

## Installer targets

Run from the repository root:

```bash
python3 scripts/install_skill.py --target codex-user
python3 scripts/install_skill.py --target claude-user
python3 scripts/install_skill.py --target copilot-user
python3 scripts/install_skill.py --target agents-user

python3 scripts/install_skill.py --target agents-project --project /path/to/project
python3 scripts/install_skill.py --target claude-project --project /path/to/project
python3 scripts/install_skill.py --target github-project --project /path/to/project
python3 scripts/install_skill.py --target portable-project --project /path/to/project
```

Existing native skill packages are not replaced unless `--force` is supplied. Project instruction files are updated only inside the skill's marked block.

The installer stages and atomically swaps the package, preserves existing instruction-file permissions, and rolls back package/instruction updates if a write fails. It rejects symbolic links, special files, packages larger than 50 MB, and packages with more than 5,000 files. `AGENTS.md` and `GEMINI.md` must be regular UTF-8 files no larger than 5 MB. Package and instruction writes compare bounded file-tree/file states before commit; project-scoped paths are rechecked immediately before commit, the staged package is rehashed, and staging cleanup verifies directory ownership. On POSIX, replaced files and package directories request parent-directory synchronization; an instruction write that committed before synchronization failed is included in transaction rollback. If another process edits the destination during staging or rollback, the installer preserves the changed path and reports the conflict instead of deleting or overwriting it. Destination-state scans stop at 10,000 entries. If installation commits successfully but deletion of the old backup fails, it keeps the new installation and reports the backup path instead of risking data loss.

These checks narrow ordinary local races but do not create a hostile multi-user security boundary. A process with permission to replace project directories can still race between the final path check and the operating-system rename. Install only into project trees whose parent directories are not writable by untrusted users.

## Boundaries

- Native auto-invocation depends on the host's current skill implementation and model behavior.
- `AGENTS.md` and `GEMINI.md` provide instructions, not a universal plugin API.
- Tool names, permissions, browsing, connectors, and subagent behavior differ between hosts.
- The package has been locally tested on macOS. A GitHub Actions matrix is provided for Python 3.10 and 3.13 on macOS, Linux, and Windows, but cross-platform behavior remains unverified until the published workflow succeeds.
- Host documentation can change; refresh compatibility claims before a release.

## Official host references

- [Claude Code skills](https://code.claude.com/docs/en/skills)
- [GitHub Copilot Agent Skills](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills)
- [GitHub Copilot custom-instruction support matrix](https://docs.github.com/en/copilot/reference/custom-instructions-support)
- [Cursor CLI instruction files](https://cursor.com/docs/cli/using)
- [Gemini Code Assist context files](https://developers.google.com/gemini-code-assist/docs/use-agentic-chat-pair-programmer)
