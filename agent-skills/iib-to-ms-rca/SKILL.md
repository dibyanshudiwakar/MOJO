---
name: iib-to-ms-rca
description: Root-cause analysis for differences between an IIB service and its generated Spring Boot migration — hypotheses grouped by variance class, weighted by evidence tier, with the missing evidence listed and the owning team named. Use when variances have been recorded and the cause must be proposed; carries the cross-cutting migration gotchas.
---

# RCA — IIB → Spring Boot

Part of the `iib-to-ms-migration` skill, split per agent. This is the skill for the **`rca`** agent. It owns the source skill's catalogue of **common patterns and gotchas**, many confirmed by live execution — which is exactly the knowledge needed to turn a variance into a hypothesis.

## Agent binding

_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; edit the catalogue, not this table._

| | |
|---|---|
| Agent id | `rca` (RCA Agent) |
| Required role | `RCA_ANALYST` |
| Depends on | `variance-analysis` |
| Accepted inputs | `variance`, `logs`, `code`, `analysis` |
| Produced outputs | `rca_hypotheses`, `confidence`, `missing_evidence` |
| Tool allow-list | `evidence.read`, `repository.read`, `rca.write` |

## What this agent does with the skill

For each group of variances, propose one hypothesis with a confidence and the evidence still missing.

1. **Start from the variance's signature.** `references/known-variance-signatures.md` maps symptoms to the gotcha or checklist item that explains them.
2. **Read the gotcha** in `references/common-patterns-and-gotchas.md` (items 1–16, then the cross-category meta-lessons and per-group gotchas) and the gap-checklist item it cites.
3. **Separate transport failures from logic differences.** A failed call to the backend (unreachable, no test data for that input, credentials not provisioned) is often **not** something regeneration can fix. Say so plainly; the remediation agent treats that as a blocking gap rather than a retry.
4. **Name the missing evidence and who holds it:**
   - QL team — buffer-layout questions
   - infra team — queue names and deployment
   - the service owner — a plaintext credential still needing encryption
   - the Kong/gateway owners — token validation that was deliberately not reimplemented (Rule 9)

## Weighing evidence

The source skill ranks its own claims, and a hypothesis should say which tier it rests on:

1. **Live-compiled-and-executed against a real backend** — strongest; it caught bugs no reading of source would.
2. **Cross-repo grep evidence tagged N/M** — solid, not runtime-verified.
3. **A single reference project's pattern** — weakest; treat with suspicion until verified.

Read "confirmed against N repos" literally — N is usually 1–3. See `references/evidence-hierarchy.md`.

Do not generalise a single-repo finding into an absolute cause. The source skill's own Rule 8 once did exactly that and had to be corrected; the meta-lessons at the end of the gotchas file say so.

## Confidence

Confidence is computed from the job's own records by the governed remediation-confidence service, never chosen by this agent — the score decides whether remediation may be applied directly, so a component the agent could pick freely would let it authorise its own fix. The evidence tier above is a qualitative input to a hypothesis's wording, not a number to add.

## Output

- `rca_hypotheses` — one per variance group.
- `confidence` — as computed by the governed service.
- `missing_evidence` — explicit, with an owner for each item.

## Boundaries

- Reads evidence and repositories; writes RCA records only. It does not edit generated code or apply a fix.
- A hypothesis that spans structural, type and value differences at once is too vague to act on. Keep them apart.

## References

Owned by this skill:

- `references/common-patterns-and-gotchas.md`, verbatim

Copies of files owned by other skills (read-only here: edit the owning skill, then run `python scripts/sync_agent_skills.py`):

- `references/known-variance-signatures.md`
- `references/gap-checklist.md`
- `references/mandatory-migration-rules.md`
- `references/evidence-hierarchy.md`
