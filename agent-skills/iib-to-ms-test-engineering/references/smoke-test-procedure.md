<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 242–257. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

for f in config/config-map/application.*.properties; do
  echo "== $f"; keys "$f" > /tmp/cm.keys; comm -23 /tmp/runtime.keys /tmp/cm.keys
done
```

Every key printed must be either copied into that config-map or deliberately classified as local-only. `management.endpoints.web.exposure.include` **must** be copied (otherwise `info`/`metrics` return 404 in every environment — live-proven); `spring.profiles.active=local` must **never** be copied. Full key-by-key classification table and the live-test evidence: `gap-checklist.md` **[M15]**.

Re-run the loop after fixing until it prints nothing but consciously-excluded keys. Don't spot-check only `dev` — the whole point is per-file drift.

### HANDOFF-C — One real API hit, fix-and-retry on failure (mandatory, every migration)

**Standing instruction from the user (2026-10-09).** This is a narrow, deliberate exception to this skill's own scope boundary (see below) — not full API testing, just enough to prove the generated service actually works end-to-end, using data the user already gave at INTAKE Batch A item 6 (never invented here).

1. **Use the request/expected-response already collected at INTAKE** — `state.json`'s `smoke_test.request`/`smoke_test.expected_response`. Do not ask again here.
2. **Start the built service** — `java -jar <web-module>/target/<artifact>-*.jar` (or `mvn spring-boot:run` from the web module), pointed at the real backend if reachable from this environment, falling back to the test-harness bypass if not. State which path was used.
3. **Make exactly one HTTP call** with the stored request against the service's own endpoint.
