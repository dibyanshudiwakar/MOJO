<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 251–257; references/gap-checklist.md lines 185–190. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

### HANDOFF-C — One real API hit, fix-and-retry on failure (mandatory, every migration)

**Standing instruction from the user (2026-10-09).** This is a narrow, deliberate exception to this skill's own scope boundary (see below) — not full API testing, just enough to prove the generated service actually works end-to-end, using data the user already gave at INTAKE Batch A item 6 (never invented here).

1. **Use the request/expected-response already collected at INTAKE** — `state.json`'s `smoke_test.request`/`smoke_test.expected_response`. Do not ask again here.
2. **Start the built service** — `java -jar <web-module>/target/<artifact>-*.jar` (or `mvn spring-boot:run` from the web module), pointed at the real backend if reachable from this environment, falling back to the test-harness bypass if not. State which path was used.
3. **Make exactly one HTTP call** with the stored request against the service's own endpoint.

- **[MQ3]** Byte offsets verified against the actual COBOL copybook, not guessed — check the request/response buffer builder's section-length comments against the `.cbl`/`.cpy` source directly.

- **[M27]** **[Rule 14]** **`settings.xml` is at the repo root, byte-identical to `references/templates/maven-settings.xml`.** Mechanical check:
  ```bash
  [ -f settings.xml ] || echo "GAP: settings.xml missing at the repo root"
  cmp -s settings.xml "<skill-dir>/references/templates/maven-settings.xml" || echo "GAP: settings.xml differs from the template"
