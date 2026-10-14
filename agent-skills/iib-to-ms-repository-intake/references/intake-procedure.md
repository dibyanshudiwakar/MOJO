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

### Batch C — Toolchain check
```bash
java -version 2>&1 | head -1   # must contain "21"
mvn -v 2>&1 | head -1          # must contain "3.8" or higher
```

### Batch D — Pre-flight table
Print before proceeding:
```
[✓/✗] IIB source readable, N flows found
[✓/✗] Backend type detected: {SOAP|QL_CICS|RXCLAIM_SBO|MQ_COBOL|REST}
[✓/✗] All flows have msgflow + esql
[✓/✗] Target dir writable
[✓/✗] JDK 21, Maven 3.8+
[✓/✗] Reference OCI repo found (pom cross-check)
```
Confirm with the user before proceeding: "Proceed with {N} flow(s) as {backend type} service?"
