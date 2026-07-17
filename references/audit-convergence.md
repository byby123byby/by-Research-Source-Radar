# Audit Convergence Protocol

Use this protocol when reviewing the Skill itself, preparing a release, or responding to a request to keep auditing until no actionable finding remains. It defines a finite, reproducible stopping condition; it does not claim that unknown defects are impossible.

## Freeze Before Judging

1. Define the audit root and threat model.
2. Inventory every scoped regular file and compute its SHA-256.
3. Exclude only the generated manifest, version-control metadata, and tool caches.
4. Treat any source, documentation, dependency, test, or configuration change as a new artifact that invalidates the previous clean streak.

## Audit Matrix

The unified runner records:

- package structure, regular-file types, resource bounds, symlinks, and file hashes;
- `RELEASE_COMPLETENESS.json` mappings from stable requirements to public claims, data representation, triggers, behavior, outputs, positive and negative tests, compatibility, documentation, and residual boundaries;
- Python compilation under the recorded runtime;
- unit, adversarial, installer, security, and documentation tests;
- Ruff, Pyflakes, Bandit, and strict mypy when `--strict-tools` is used;
- operating system, Python version, tool versions, skipped surfaces, and residual limitations.

Scientific validity, exhaustive source discovery, future dependency behavior, and platforms not executed in the current environment remain outside a local software audit and must be listed under `uncovered`.

The completeness matrix is mandatory and travels with the installed Skill. Every referenced path and exact marker must resolve inside the frozen artifact and identify a file that remains in the installed package; every stable requirement must be covered, and positive and negative tests must be distinct. The matrix prevents a declared feature from existing only in prose or only in repository documentation, but it cannot prove that maintainers registered every possible requirement. That residual remains explicit in the generated manifest.

## Run To Convergence

From the Skill root:

```bash
python3 scripts/audit_release.py --strict-tools
python3 scripts/audit_release.py --strict-tools
```

The first unchanged clean run produces `CLEAN_ROUND_1`. The second reruns the complete matrix and may produce `PASS_CONVERGED`. The generated `AUDIT_MANIFEST.json` records the artifact hash, audit-profile hash, checks, findings, uncovered surfaces, history, and stop reason.

If the four static-analysis tools are unavailable, omit `--strict-tools` only for a core audit. Missing tools are then recorded as `not_run` and as uncovered; such a run must not be presented as a strict release audit.

## Stop Rule

Stop only when:

- two consecutive complete rounds are clean;
- both rounds use the same artifact hash and audit-profile hash;
- no file changed between the rounds;
- required checks ran rather than being silently skipped;
- the release-completeness check passed with every declared requirement covered;
- the manifest says `PASS_CONVERGED` and explains remaining boundaries.

Do not report “no problems exist.” Report instead:

> For artifact `<sha256>`, two consecutive rounds of the recorded audit matrix passed without modification. No reproducible, actionable finding remains inside that matrix. The manifest lists untested and unknowable surfaces.

`AUDIT_MANIFEST.json` is a machine-readable audit record, not a signed attestation or proof against a malicious maintainer rewriting the package and its manifest.
