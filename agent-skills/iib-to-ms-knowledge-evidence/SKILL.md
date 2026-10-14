---
name: iib-to-ms-knowledge-evidence
description: How to weigh and cite evidence in an IIB/ACE to Spring Boot migration — the live-executed, cross-repo and single-repo evidence tiers, how the source skill tags its claims, and how a citation should name a specific version of an artefact. Use when indexing job artefacts for later agents or resolving a conflict between two sources.
---

# Knowledge & Evidence — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`knowledge-evidence`** agent. The source skill has little to say about indexing, but it is explicit about *how much each of its claims can be trusted* — which is what a citation needs to carry.

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `knowledge-evidence` (Knowledge and Evidence Agent) |
| Required role | `KNOWLEDGE_CURATOR` |
| Depends on | — |
| Accepted inputs | `versioned_artefacts`, `metadata` |
| Produced outputs | `cited_context`, `version_lineage` |
| Tool allow-list | `evidence.read`, `index.write`, `index.read` |

## What this agent does with the skill

### The evidence hierarchy

Verbatim in `references/evidence-hierarchy.md`. When two claims conflict, prefer, in order:

1. live-compiled-and-executed against a real backend;
2. cross-repo grep evidence tagged N/M;
3. a single reference project's pattern with no cross-check.

### Reading the tags correctly

The source skill's reference files carry their own provenance. Preserve it when indexing or citing:

- **`[live-tested]`** — caught by actual compilation or execution; the strongest tier.
- **N/M** (for example "0 of 5 surveyed repos") — a cross-repo grep count. Read it literally: N is usually 1–3, not the size of the whole workspace.
- **single-repo** or **N=1** — weakest; treat with suspicion until a second repo or a real run confirms it.
- **`[confirmed by real-sibling-repo diff]`** — a deliberate comparison against a deployed repo.
- Standing rules **decided against contrary evidence** (for example Rule 10) must be cited as decisions, not as evidence of what real repos do.

### Citing

- Cite a reference by **file and item id** — for example `gap-checklist.md [H6]`, `mandatory-migration-rules.md Rule 4`, `common-patterns-and-gotchas.md §9` — so a reader can open the exact item.
- A citation names a **specific version** through the artefact's own content hash. An index entry whose hash no longer matches its source is a stale citation, and visibly so.
- Indexing is idempotent by (reference, content hash); do not split citations across duplicates.
- A claim that cites only a tier-3 source should say so.

## Output

- `cited_context` — the passages a downstream agent may rely on, each with its evidence tier.
- `version_lineage` — which version of which artefact each citation points to.

## Boundaries

- Holds `evidence.read`, `index.write`, `index.read` — **not** `evidence.write`. The index is built *from* evidence; an agent that could add evidence while indexing it could manufacture the citation it returns.
- It does not decide a conflict between sources. It states the tiers and lets the governance and approval path decide.
- It does not rewrite the source skill's claims; where the skill itself marks a claim as corrected or superseded, cite the correction.

## References

Owned by this skill:

- `references/evidence-hierarchy.md` — the hierarchy paragraph from the workflow, verbatim

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/gap-checklist.md` and `mandatory-migration-rules.md` — where the tags appear most densely
- `references/common-patterns-and-gotchas.md` — the cross-category meta-lessons
- `references/fixed-file-templates.md` — the "Training vs. testing repo map (evidence provenance)" section
