# Retrieval Skill A/B Evaluation

## Contents

- [Research question](#research-question)
- [Conditions and isolation](#conditions-and-isolation)
- [Frozen task sets](#frozen-task-sets)
- [Outcomes](#outcomes)
- [Pooled blind judgment](#pooled-blind-judgment)
- [Analysis](#analysis)
- [Execution](#execution)
- [Interpretation boundary](#interpretation-boundary)
- [Release-audit separation](#release-audit-separation)

## Research Question

Under a fixed model, web-tool set, cutoff date, and execution budget, does loading `research-discovery-and-translation-audit` improve the relevance, source validity, SuperVision constraint fit, and efficiency of research/open-source discovery compared with the same agent without the Skill?

The independent variable is target-Skill presence. The primary outcomes are `nDCG@10` and the number of valid, highly relevant sources. Invalid-source rate and completion are safety outcomes. Time and token-normalized useful-source yield are efficiency outcomes.

This is a benchmark of one frozen model/tool/Skill configuration. It is not a study of BLV users, does not measure accessibility benefit, and cannot establish exhaustive discovery.

## Conditions And Isolation

- `baseline`: a fresh temporary `CODEX_HOME` contains only a short-lived link to the existing authentication file. It contains no user Skills and does not load the user's normal configuration or project rules.
- `skill`: the same isolation is used, but the target Skill directory is copied into the temporary home's `skills/` directory.
- Both conditions use the same model, reasoning effort, prompt, response schema, internet availability, hard timeout, and source limit.
- Every trial is ephemeral. Output from one trial is not provided to another.
- Trial order is randomized from a recorded seed. Repetition and condition remain in the execution manifest but are removed from the pooled judgment file.
- The runner never copies authentication into the experiment output. Its temporary home is removed after each trial.

The prompt says only to use any installed Skill that applies. It does not summarize the target Skill for the baseline. The common JSON response schema is intentionally narrow: ranked sources, queries, gaps, and constraint notes. Source validity and relevance are judged outside the model response.

## Frozen Task Sets

The versioned task set is [supervision-retrieval-ab-tasks.json](supervision-retrieval-ab-tasks.json).

- Pilot: 8 tasks x 2 conditions x 2 repetitions = 32 trials.
- Main: 30 tasks x 2 conditions x 3 repetitions = 180 trials.
- Categories: exact identity, ambiguous user-shared seed, similar-work expansion, current landscape, and mechanism transfer.
- All tasks use the same frozen cutoff date and SuperVision constraints: non-jailbroken standalone iPhone, no runtime computer or remote browser, VoiceOver compatibility, bounded memory, and auditable behavior.

Do not change task wording, source limits, model, reasoning effort, timeout, or scoring rules after viewing condition results. A material change creates a new benchmark version.

## Outcomes

### Primary

- `ndcg_at_10`: graded ranking quality using relevance 0/1/2.
- `valid_high_relevance_sources`: retrieved sources judged both highly relevant and identity-valid.

### Safety And Secondary

- `invalid_source_rate`.
- `completion`.
- `precision_at_10`.
- `pooled_recall_at_20`.
- `direct_fit_sources`.

### Efficiency

- `valid_relevant_per_1k_tokens`.
- `valid_relevant_per_minute`.
- `wall_seconds`.
- `total_tokens`.

### Coverage And Compression Extension

The seed-neighbor policy should be evaluated with two additional observations when the implementation uses the adaptive coverage probe:

- whether the final candidate set covers the declared direct-use, alternative, validation, failure, and current-work categories;
- whether compact-context loading and any optional compressor reduce tokens without reducing identity validity or highly relevant sources.

The coverage probe is allowed at most one targeted gap query within the existing hard search budget. A compressor such as [LLMLingua](https://github.com/microsoft/LLMLingua) or a memory adapter such as [Mem0](https://github.com/mem0ai/mem0) must be tested as a separate adapter condition. It must not be silently enabled in the Skill condition, and its project-reported token savings must not be treated as evidence for this Skill.

Raw source count is not a success criterion. Duplicate, invented, weakly related, or constraint-incompatible sources cannot improve the primary outcomes.

## Pooled Blind Judgment

An exhaustive gold set is unavailable. Use pooled judgment:

1. Merge unique task-source pairs returned by both conditions.
2. Normalize GitHub, DOI, arXiv, and public URLs before deduplication. Merge paper or preprint copies with the same normalized bibliographic title so a DOI landing page and an author-hosted PDF cannot be counted twice.
3. Remove condition, repetition, rank, model prose, and trial identity, while retaining the original task constraints and evaluation focus.
4. Judge every pooled source against the original task, constraints, and evaluation focus.
5. Use `relevance`: 0 irrelevant, 1 relevant, 2 highly relevant.
6. Use `identity_valid`: `valid`, `invalid`, or `unresolved` after opening an authoritative source.
7. Use `constraint_fit`: 0 incompatible, 1 useful only by mechanism transfer, 2 directly compatible.

The primary analysis requires all pool rows to be judged. Automated model judging may be reported as exploratory only; the defensible result requires a human judgment pass blinded to condition. If two judges are available, preserve both raw files and report agreement before resolving disagreements.

## Analysis

The scorer first computes trial metrics. It then averages repetitions within task and condition, subtracts baseline from Skill, and bootstraps tasks rather than individual repeated trials. It reports the mean paired difference, 95% percentile bootstrap interval, and task win rate.

No p-value is a prerequisite for interpretation. Report effect direction, magnitude, interval, failures, and the frozen benchmark scope. If the interval crosses zero, describe the evidence as inconclusive for that endpoint. Do not select a different primary metric after seeing results.

## Execution

From the installed Skill root:

```bash
python3 scripts/retrieval_ab_benchmark.py validate-tasks \
  --tasks references/supervision-retrieval-ab-tasks.json
```

Prepare a pilot outside the Skill package:

```bash
python3 scripts/retrieval_ab_benchmark.py prepare \
  --tasks references/supervision-retrieval-ab-tasks.json \
  --phase pilot \
  --runs 2 \
  --seed 20260716 \
  --model gpt-5.6-sol \
  --reasoning-effort high \
  --max-wall-seconds 600 \
  --max-sources 10 \
  --output-dir /path/to/supervision-retrieval-ab-pilot
```

Execute pending trials only after reviewing the manifest. The explicit confirmation flag prevents accidental model/network spending:

```bash
python3 scripts/retrieval_ab_benchmark.py run \
  --run-dir /path/to/supervision-retrieval-ab-pilot \
  --codex /path/to/codex \
  --source-codex-home "$HOME/.codex" \
  --target-skill "$HOME/.codex/skills/research-discovery-and-translation-audit" \
  --confirm-live-run
```

Create the blind pool:

```bash
python3 scripts/retrieval_ab_benchmark.py pool \
  --run-dir /path/to/supervision-retrieval-ab-pilot \
  --tasks references/supervision-retrieval-ab-tasks.json \
  --output /path/to/supervision-retrieval-ab-pilot/blind_judgments.json
```

After every row has been judged:

```bash
python3 scripts/retrieval_ab_benchmark.py score \
  --run-dir /path/to/supervision-retrieval-ab-pilot \
  --judgments /path/to/supervision-retrieval-ab-pilot/blind_judgments.json \
  --output /path/to/supervision-retrieval-ab-pilot/metrics.json \
  --report /path/to/supervision-retrieval-ab-pilot/report.md
```

## Interpretation Boundary

Software unit tests show that preparation, isolation, validation, pooling, and scoring behave as tested. They do not show that the Skill improves retrieval. A completed, judged A/B run is required for an effectiveness claim.

Pooled recall is recall within the judged union, not recall over all existing work. A positive result applies to the frozen model, date, tools, tasks, and budgets. A negative or inconclusive result should be retained; do not silently replace difficult tasks or discard failed trials.

## Release-Audit Separation

Research-source reverse audit remains part of this Skill because it shares the candidate ledger and source-to-outcome evidence chain. Software release assurance is a maintainer concern. Before the final public release, extract the generic release runner into a separately versioned `Agent Skill Release Assurance` package, keep `RELEASE_COMPLETENESS.json` as target metadata, rerun the complete release gate, then freeze and benchmark the final user-facing Skill. Do not split after collecting the definitive A/B result without rerunning the benchmark.
