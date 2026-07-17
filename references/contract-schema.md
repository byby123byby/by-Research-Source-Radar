# Research Contract Schema v2

The bundled script creates and validates a JSON contract using only the Python standard library. Validation is a recorded-evidence gate, not proof of scientific validity or exhaustive discovery.

## Contents

- [Core fields](#core-fields)
- [User-shared seed provenance](#user-shared-seed-provenance)
- [Exact query execution](#exact-query-execution)
- [Authoritative source identity](#authoritative-source-identity)
- [Verified source snapshots](#verified-source-snapshots)
- [Structured evidence references](#structured-evidence-references)
- [Candidate status and review depth](#candidate-status-and-review-depth)
- [Mechanism decisions](#mechanism-decisions)
- [Mode-specific floors](#mode-specific-floors)
- [Commands and semantics](#commands-and-semantics)

## Core fields

- `contract_version`: must be `2`.
- `project`, `question`, `profile`, `mode`: research identity and workflow mode.
- `created_at`: a non-future ISO date or timezone-aware timestamp.
- `scope`: non-future cutoff date, freshness requirement, review type (`exploratory`, `scoping`, or `systematic`), languages, internally consistent year range, geography, source types, constraints, inclusion, and exclusion rules.
- `scope.trend_requirement`: `not_requested`, `monitor`, or `required`; a required sweep must complete `trend_discovery`.
- `search_lanes`: separate `direct_use` and `mechanism_transfer` searches when required by mode.
- `seed_discovery`: privacy-minimized provenance and mechanism fingerprint for content supplied directly by a user; required whenever a candidate uses `discovered_via: ["user_seed"]`.
- `source_classes`: databases, registries, repository hosts, official sources, or explicitly unavailable classes.
- `query_families`: conceptually independent searches containing exact per-source executions.
- `record_management`: record counts, deduplication method, exclusion counts, and flow evidence.
- `search_quality`: search-strategy peer review and publication/correction/retraction status checks.
- `trend_discovery`: optional time-bounded popularity/emergence sweep with independent sources, exact queries, candidate-linked signals, triangulation, and a fixed `discovery_only` evidence policy.
- `chaining`: backward, forward, related-project, author/organization, benchmark/competitor, and failure/correction paths.
- `candidates`: source ledger with controlled artifact type, authoritative identity, verified source snapshot, review depth, fit, decision, and rationale.
- `mechanisms`: atomic claims/mechanisms and source-to-artifact evidence.
- `gaps`, `stop_rule`, `coverage_statement`: residual uncertainty and bounded completion claim.

## User-shared seed provenance

`seed_discovery.status` is `not_applicable` unless a candidate records the `user_seed` discovery route. A recorded seed contains:

- controlled `source_type`, source platform, optional source locator, and non-future `shared_at`;
- a privacy-minimized `seed_summary`, rather than unrelated account or personal content;
- `retention` as `redacted`, `verbatim`, or `not_retained`;
- the extraction method, a finite confidence from 0 to 1, and restrained uncertainty variants;
- observed structured source evidence;
- a mechanism fingerprint containing the problem, at least one core mechanism, modalities, runtime constraints, claimed evidence, and unresolved claims.

Validation rejects `user_seed` candidates without recorded seed provenance, and also rejects a recorded provenance block that is not linked to a `user_seed` candidate. This proves that the lead and fingerprint were represented in the contract; it does not prove that OCR, transcription, similarity ranking, or neighbor discovery was accurate.

## Exact query execution

Each `query_families[].executions[]` records:

- `source`: a searched `source_classes[].name`;
- `interface`: database/API/site interface actually used;
- `exact_query`: exact syntax as executed, not a paraphrase;
- `executed_at`: non-future ISO date or timezone-aware timestamp;
- `filters` and `limits`: explicit lists, including empty lists;
- `results_count`;
- `result_evidence`: structured evidence pointing to a search URL, exported record set, log, or hashed file.

`record_management` records identified, deduplicated, screened, deeply reviewed, and included counts. These are generic records and may represent papers, repositories, datasets, standards, or grey literature. For a formal systematic/scoping review, also follow the applicable reporting and human-screening protocol supplied by the specialist research skill.

For `scoping` and `systematic` review types, `search_quality.strategy_peer_review` must be completed with reviewer and evidence. A publication-status check records when corrections, retractions, superseded versions, or changed publication status were last assessed. Selected papers without that check produce a visible warning.

## Authoritative source identity

Every candidate has a `source_identity` object:

```json
{
  "kind": "doi",
  "value": "10.1186/s13643-020-01542-z",
  "status": "verified",
  "verified_at": "<runtime UTC ISO-8601 timestamp>",
  "verification_method": "Crossref REST API",
  "canonical_id": "doi:10.1186/s13643-020-01542-z",
  "canonical_url": "https://doi.org/10.1186/s13643-020-01542-z",
  "resolved_title": "PRISMA-S: ...",
  "title_match": 1.0,
  "evidence": "https://api.crossref.org/works/..."
}
```

The timestamp above is a placeholder, not a value to copy. `verify-sources --write` sets `verified_at` from the UTC clock at the time an authoritative service is actually queried. It records the last successful or attempted verification event; it must not advance merely because a contract is opened, rendered, or validated offline. Verified source and snapshot timestamps must be timezone-aware UTC values and cannot be in the future.

Supported automatic identity methods:

- `doi`: Crossref, with DataCite fallback;
- `arxiv`: official arXiv API;
- `pmid`: NCBI E-utilities;
- `github`: GitHub REST API;
- `official_url`: bounded public HTTPS reachability only, enabled explicitly with `--allow-official-url`; suitable for official standards/datasets/documents but not sufficient for selected papers or GitHub repositories;
- `other`: requires manual appraisal and cannot be automatically verified.

Run `verify-sources --write` before selecting candidates. `include` and `adapt` papers require verified DOI, arXiv, or PMID metadata. Selected GitHub repositories require GitHub API verification. Canonical IDs are deduplicated and must agree with the normalized input identity and verified snapshot. A real identifier with a mismatched title always fails verification. Matching is exact after case, whitespace, and punctuation normalization; GitHub's verified short repository name is also accepted. Correct the candidate to the authoritative title and record translations or local labels outside `source_identity`.

For a verified identity, the evidence locator must identify the same object, not merely use the right domain: the DOI embedded in a Crossref/DataCite path, arXiv `id_list`, NCBI PMID, or GitHub `owner/repository` must match the canonical record. Metadata redirects must remain on the expected authoritative HTTPS host, and the final response URL must still identify the requested record. GitHub commit, tag, and release checks additionally bind the final API path and returned metadata to the requested object. GitHub repository redirects are normalized to the identity returned by the API. Public `official_url` retrieval pins each request to an already validated public IP address and revalidates every redirect to limit DNS-rebinding and local-network access; it is still only a bounded reachability check.

Candidate `type` uses a controlled value: `paper`, `preprint`, `github_repository`, `repository`, `dataset`, `standard`, `official_document`, `model`, `package`, `trial_registry`, `law_policy_source`, `grey_literature`, or `other`. Do not combine a paper and its repository into one row; link two candidates instead.

Technical coverage may additionally use `technical_blog`, `community_discussion`, or `newsletter`. These types remain grey evidence classes; their identity, authorship, conflicts, primary-source relationship, and claim limits must be appraised explicitly.

## Emerging and popular-source signals

A completed `trend_discovery` records:

- `window_days` and an operational `definition` of popularity or emergence;
- at least two independent `sources`, each with an explicit `independence_group`, interface, exact query/feed, search time, result count, and observed evidence;
- one or more `signals`, each with a stable ID, linked candidate ID, source, controlled signal type, value, observation time, and evidence;
- optional `claims` such as `emerging` or `popular`; every claim binds one candidate to at least two signals from different independence groups and states a boundary;
- a `triangulation_rule`;
- `evidence_policy: discovery_only`.

Supported signal types include repository velocity, release activity, package/model adoption, technical-blog frequency, community attention, curated/newsletter visibility, benchmark visibility, search interest, and `other`. A candidate discovered this way records `trend:<signal-id>` in `discovered_via`; validation binds that route to a completed signal for the same candidate. Signal popularity cannot satisfy mechanism evidence or source-quality requirements.

## Verified source snapshots

`source_snapshot` is structured separately from source identity. It records `kind`, requested `value`, status, verification time, canonical value, and evidence.

- Selected GitHub sources pin a verified `commit`, `tag`, or `release`; tags and releases also store the resolved Git object ID so a moved reference is detectable. Repository existence alone is insufficient.
- Papers and preprints use a `publication_version` registry snapshot bound to the canonical DOI, arXiv ID, or PMID. For DOI and PMID this is an identity/registry check, not a byte-level archive of the article; an arXiv `vN` identifier can preserve an explicit preprint version.
- Other source types may use an edition, dataset/standard version, or dated access snapshot.
- A v1 snapshot string migrates as pending and must be reverified.

Identity verification proves only that the metadata registry resolved the artifact at the recorded time. It does not prove quality, safety, novelty, correctness, or that no source was missed.

## Structured evidence references

Evidence is an object, never a free-form success sentence:

```json
{
  "kind": "file",
  "locator": "research/results/test-output.json",
  "status": "verified",
  "checked_at": "<actual evidence-check UTC ISO-8601 timestamp>",
  "sha256": "<64 hex characters>",
  "note": "Behavioral test output"
}
```

Kinds are `file`, `url`, `command`, `manual`, `section`, `dataset`, `log`, and `note`. Statuses are `pending`, `observed`, `verified`, `failed`, `blocked`, and `not_applicable`.

- Verified files require existence and a matching SHA-256 when `validate --base` is used. They must resolve inside `--base`, contain no symbolic-link path component below that base, be regular files, and be no larger than 100 MB.
- Verified files require a SHA-256 even when file existence cannot be checked because no base path was supplied.
- Verified commands require a verified `result_artifact`.
- Verified manual evidence requires `checked_by`.
- Verified URL, dataset, section, log, and note evidence requires both `checked_by` and `verification_method`; a timestamp and locator alone cannot claim verification.
- `implemented` mechanisms require observed evidence.
- `validated` mechanisms require verified artifact, positive test, failure test, and audit evidence.
- Migrated v1 strings become `pending` evidence and cannot silently pass.

## Candidate status and review depth

Candidate statuses: `include`, `adapt`, `monitor`, `exclude`, `unresolved`.

Review depths:

- `discovered`: metadata/search result only;
- `screened`: abstract, summary, or README;
- `deep`: relevant methods/modules, evidence/tests, and limitations/issues recorded as structured evidence;
- `blocked`: intended material inaccessible.

An included/adapted candidate must have verified identity, deep review, and at least one atomic mechanism row. Do not infer `deep` from a long README.

## Mechanism decisions

Use `adopt`, `adapt`, `represented`, `defer`, `reject`, or `unverified`. An adaptation must not be reported as a reproduction.

For `implemented` or `validated` work, record the artifact, decision effect, positive test, failure/falsification test, audit evidence, and claim boundary. Non-software evidence may point to a protocol, instrument, experimental control, preregistered analysis, participant verification, or ethics safeguard.

## Mode-specific floors

- `landscape` and `full`: 3 searched source classes, 4 query families, 2 completed chaining paths, both search lanes, completed record management.
- `refresh`: 2 searched source classes, 2 query families, 1 completed chaining path, both lanes, completed record management.
- `source-depth`, `translate`, and `audit`: 1 searched source class; independent landscape queries and chaining are optional, while candidate identity, source depth, mechanisms, evidence, and gaps remain required.

Unavailable source classes remain visible as gaps but do not count toward searched-source floors. When record management is required, `records_included` must equal the number of `include` and `adapt` candidates. Adopted or implemented mechanisms cannot be supported by an excluded, unresolved, or monitor-only source.

Coverage exceptions may only address the named numeric/record-management floors. Each exception requires `check`, `reason`, `approved_by`, and `impact`. Exceptions cannot bypass source-identity or evidence gates.

`coverage_statement` must explicitly state that the result is bounded and does not prove exhaustive or complete discovery. A missing boundary is a validation error, not a warning.

## Commands and semantics

- `init`: create a v2 template.
- `verify-sources`: query authoritative metadata services; use `--write` to save results.
- `validate --base .`: validate recorded schema, local evidence files, hashes, and traceability.
- `validate --base . --online`: additionally re-resolve selected source identities and snapshots against authoritative services; use this before a final authenticity claim.
- `migrate`: convert v1 to v2 while leaving legacy evidence unverified.
- `diff`: compare all search-method and search-quality sections plus every candidate/mechanism field; invalid inputs are blocked unless explicitly allowed for migration diagnostics.
- `render`: generate a deterministic nine-section Markdown report from the contract, including search quality, emerging/popular signals, discovery routes, source metadata, evidence, deferred/unresolved rows, residual risks, refresh triggers, and the bounded coverage statement. Existing reports are not replaced unless `--force` is supplied.

`SCHEMA_PASS` means the recorded offline gates passed. `ONLINE_IDENTITY_PASS` additionally means current authoritative metadata matched the contract during that run. Neither is a claim of exhaustive discovery or scientific validity. The optional report scanner accepts UTF-8 text formats and catches known overclaim patterns; it is a heuristic, not semantic fact-checking.

Contract and metadata JSON are parsed strictly: duplicate keys and non-finite numbers are rejected. For resource safety, a contract is limited to 20 MB, 200,000 JSON nodes, and 64 nesting levels; expensive title, locator, and query fields also have explicit length ceilings. Report text scanned by the optional phrase gate is limited to 20 MB. A locally verified evidence file is limited to 100 MB and must remain inside the selected base directory without symbolic-link traversal. Reads verify regular-file identity before and after use. Writes are atomic, use optimistic file-state checks, and request parent-directory synchronization on POSIX, so `verify-sources --write`, `init`, `migrate`, and `render` stop rather than overwrite a concurrent external edit. If replacement succeeds but directory synchronization fails, the command reports explicitly that the new file is already present. Generated Markdown and routine terminal output neutralize control and bidirectional-formatting characters from contract content. These are operational limits, not study-size recommendations.
