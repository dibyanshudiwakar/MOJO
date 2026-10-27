<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 9–46. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

## INTAKE — source intake, backend-type detection, confirmation (blocking, run once per migration)

No `.java` file is written until this stage completes.

### Batch A — Repos & Identity

**Standing instruction from the user (2026-10-09): always take these 5 inputs directly from the user before moving ahead — ask explicitly, wait for a real answer, never silently default past any of them.** A suggested default is fine to offer *when asking* (it speeds up the answer), but offering a default is not the same as skipping the question — INTAKE does not proceed to Batch B until all 5 have an explicit answer:

1. **IIB source repo path** — validate it contains at least one `.msgflow` file.
2. **Migrated MS destination directory** — when asking, suggest the same parent directory the IIB source repo lives in as the default answer (Rule 13) — e.g. if the source is `<parent>/CVSEDB_PBM-<Service>-EDB-EIL`, suggest the sibling `<parent>/EIPMS_cvs-v1-<service>-oci` — but still ask and capture the actual answer, don't assume it.
3. **Framework version** — when asking, suggest `framework-starter` 3.2.2 and Java 21 as the default answer (Rule 7), but still ask and capture the actual answer.
4. **One successful request's data** — payload/params for one endpoint, for the end-to-end smoke test that runs at HANDOFF-C. Don't invent this yourself; wait for a real answer.
5. **The expected response for that same request** — status code and/or key fields/values. Collected in the same batch as #4, not separately.

Persist #4 and #5 in `state.json` (`smoke_test.request`/`smoke_test.expected_response`) so they survive a resume — collected once, here, never re-asked at HANDOFF.

**Also ask in this same batch** (not part of the mandatory 5, but still collected here): reference OCI project path (an existing sibling MS repo for the same or a similar backend type, for pom/import cross-checking) — try to auto-discover a sibling `*-oci` repo in the workspace first; if none found, use the confirmed reference-project catalog in the connector-specific reference file for the detected backend type. **Always ask: "Generate full JUnit 5 test cases with real content, or structure-only empty skeletons?" (Rule 11)** — if the user wants full content, generate it per the backend type's test-density convention (`rest-external-direct.md` §0c's coverage list, or the fuller per-layer suite for other categories) with real assertions and mocked behavior — not placeholders; if structure-only (or no answer), fall back to empty skeleton classes exactly as Rule 11 describes. Also ask the CVS programme phase (1 or 2).

### Batch B — Auto-detect backend type
Read all `.msgflow` and `.esql` files fully, then apply the Backend Type Detection table (see below). Record the result. If ambiguous, ask rather than guess.

**Fallback for an unrecognized backend (standing instruction from the user, 2026-10-13).** The 5 backend types this skill is trained on are: **SOAP, QL/CICS + Camel Bindy, RxClaim SBO (XML/JAXB), IBM MQ + COBOL Copybook, REST**. If detection finds a backend that matches none of these, **or** matches one but no trained reference repo exists for it in the workspace, do not guess a pattern from the closest-looking type. Instead take the reference from the org's frameworks repo, `https://github.com/cvs-legacy-source-code/EIPMS_cvs-technical-edb-cs-frameworks`, and migrate against the connector/framework module it provides for that backend.

- **Full procedure, module map, and version-branch rules: `references/framework-repo-fallback.md`** — read it before using the fallback. Key points: the repo is private (check for a local clone, ask the user if none, stop with a blocker if unreadable — never invent framework APIs); read the branch matching the chosen framework version (`origin/3.2.x`, **not** `master`, which is 4.0.0-SNAPSHOT with a breaking-change commit) via `git show`/`git grep` without checking out; and `framework-adapter` is **not** a migration target (it fronts a still-running IIB).
- Read the matching framework module's source for the real classes, annotations, and property keys, and cite the module, branch, and commit in the action-items list.
- Tag everything generated this way as **single-source / unverified** (evidence-hierarchy tier 3), same as `soap-backend.md` and `ibm-mq-cobol-copybook.md` are tagged, and say so in the HANDOFF output.
- Record in `state.json` under `backend_type` as `NEW:<short-name>` so a resume knows this run used the fallback.
- All mandatory rules and HANDOFF-C's smoke test still apply unchanged.

### Batch C — Toolchain check
```bash
java -version 2>&1 | head -1   # must contain "21"
mvn -v 2>&1 | head -1          # must contain "3.8" or higher
```

### Batch D — Pre-flight table
Print before proceeding:
```
