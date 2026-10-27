<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- SKILL.md lines 135–165. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->


### Step 4 — HANDOFF: full build & hand-off

1. Run `mvn -s <repo-root>/settings.xml clean verify`.
2. **Delete `*-oci-parent/target/`** — the parent module is a pure aggregator pom with no jar of its own, so its `target/` is just empty build noise, not a deliverable; never leave it in the handed-off tree. **Leave `*-oci-web/target/` alone** — that one holds the actual built jar and is expected (`workflow-and-state.md` HANDOFF for the full rationale).
3. **Run HANDOFF-A0 before HANDOFF-A** (`workflow-and-state.md`): check the literal baseline block in `references/templates/config-map-baseline.properties` byte-for-byte against the packaged `application.properties` AND every config-map independently — this catches the recurring real failure (RankedMatch, GetGrievanceSummary) where the block was missing from *both*, which a packaged-vs-config-map diff alone can't see. Copy that physical file's content, don't retype it from memory.
4. **Run HANDOFF-A — reconcile runtime properties into every config-map** (`workflow-and-state.md` HANDOFF-A): diff the packaged `application.properties` key list against each of the ten `config/config-map/application.<env>-<cluster>.properties` files.
   - `SPRING_CONFIG_LOCATION` *replaces* the classpath config locations, so the packaged file is never loaded in a deployed pod and the config-map must be self-contained.
   - `management.endpoints.web.exposure.include` must be copied (live-proven: without it `info`/`metrics` 404 in every environment).
   - `spring.profiles.active=local` must never be copied.
   - See `gap-checklist.md` **[M15]**.
5. **Run HANDOFF-C — one real API hit, fix-and-retry on failure** (`workflow-and-state.md` HANDOFF-C): using the request/expected-response collected at INTAKE (never re-asked here), start the built service, make exactly one real HTTP call, and compare the actual response to the expected one.
   - If it passes, record which attempt succeeded and continue.
   - If it fails on a *generated-code* problem (wrong mapping, wrong config key, wrong endpoint, wrong header), fix it, recompile, restart, and retry the same stored request — up to **3 attempts total**, the same bound used by the gap-checklist and compile-gate loops.
   - If it still fails after 3 attempts, or the cause clearly isn't something a regenerate can fix (backend unreachable, no matching test data, credentials not provisioned), **stop retrying** and report it as a **BLOCKING** gap with the actual-vs-expected diff from every attempt — never report HANDOFF as successful while this is unresolved.
6. Re-run the full gap checklist on the final tree. Print residual GAPs with a remediation path (who to ask — QL team for buffer questions, infra team for queue/deployment questions, the service owner for any plaintext credential value still needing encryption).
7. If this service has any credential at all, confirm the Akeyless scaffolding was generated live (not disabled/scaffolded-and-off, and not merely asked about) and list the actual secret path as an action item since it's never known at migration time; if the service genuinely has no credential to manage, say so explicitly rather than leaving it unaddressed (Rule 6).
8. **Always list the deferred exception handler as a future action item** (Rule 10) — e.g. *"No exception handler was generated; add one after `iib-ms-testing` identifies what's actually needed"* — and, if the ESQL had an ID-token-check node, that Kong is expected to enforce it upstream and nothing was generated for it (Rule 9).
9. Print a suggested commit message.

> **Never** `git commit`/`git push`/`mvn deploy` without being explicitly asked.

#### Output format

State at the top:
- **Framework version selected:** `x.y.z` (reason: user-specified / highest found in workspace repo `<name>`)
- **CVS programme phase:** 1 or 2 (determines credential handling approach)
- **Backend type:** determined in INTAKE

Then present files in this order: `.github/` workflows → parent pom → web pom → Dockerfile → config-map properties + Helm chart → controller/API interface → service → connector → buffer builders/XML converters/request mappers → buffer parsers/response mappers → Bindy/JAXB models → error/validator classes → constants.
