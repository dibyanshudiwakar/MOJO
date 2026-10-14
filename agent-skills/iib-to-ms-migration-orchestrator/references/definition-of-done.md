<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- SKILL.md lines 44–56. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

## 2. What "done" means — read this before GENERATE

**Standing instruction from the user (2026-09-29):** the primary goal of a migration is a project that starts from nothing and ends with:

- (a) correct folder/file structure,
- (b) all setup files present and correct (poms, Dockerfile, `.github/`, config-map, Helm), and
- (c) **a working call to the backend** — the generated service compiles, wires the right framework classes and annotations for the detected connector type, and can actually reach the backend (production path or test-harness).

**Exact business-logic fidelity is secondary and gets fixed after testing, not perfected up front.** Concretely:

- **Always get right, no shortcuts**: the framework classes and annotations for the detected backend type — `QlConnector`/`QLRequest`/`QLResponse` + Camel Bindy for QL_CICS, `RxClaimConnector`/`RxClaimRequest` for RxClaim SBO, `MQUtility` for MQ_COBOL, `RestClientWrapper`/`RestClientRequest` for REST, `OAuthClient` where the backend needs OAuth2, plus the cross-cutting ones every type needs — `EieHeader`, `ErrorUtil`/`ErrorStatus`, `@EIPMicroserviceProviderCall`, `@EIPMicroserviceOperation`. These are what make the generated service *able* to reach the backend at all — get them wrong and no amount of correct business logic matters.
- **Can be simplified, refined later**: exact field-by-field ESQL→Java translation, precise validation order/text, exact response transformation nuances. Generate a reasonable, working version (validate the obvious required fields, map the response fields that are clearly identifiable), don't burn time perfecting every byte offset or every error-message wording against the ESQL before the very first test run — that's exactly the kind of detail that's cheap to fix once a real test against the backend shows what's actually wrong, and expensive to get right blind.
- **This does *not* relax the mandatory rules** below (builder-pattern verification, no `RestTemplate`, no `StringBuilder` for QL buffers, credential encryption, `EieHeader` accessor verification) — those are about not introducing *wrong* framework usage, which is a structural/correctness concern, not a business-logic-precision one.
