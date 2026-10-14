# Known IIB-vs-MS Variance Signatures

> **Authored index, not extracted text.** Every row points to the checklist item or gotcha in the source skill that carries the evidence and the explanation. The "suggested class" column is this index's labelling for the factory's STRUCTURAL / TYPE / VALUE scheme; the source skill does not use those names.

Source files, copied into this skill alongside this one (owned by code-generation and rca):

- `gap-checklist.md` — items written `[B#]`, `[H#]`, `[M#]`, `[R#]`
- `common-patterns-and-gotchas.md` — items written `gotchas §N`

| Symptom in the comparison | Suggested class | Likely explanation | Source |
|---|---|---|---|
| A string field is **absent** from the MS JSON where IIB returned it as `""` | STRUCTURAL | Blank fields trimmed to `null` under `@JsonInclude(NON_NULL)`; the key is omitted instead of shown as `""`. Live-tested | [H6], gotchas §9 |
| A numeric field shows a leading sign or zero padding (for example `+0018`) | VALUE | Signed-decimal (SLS) field passed through without normalising on the way out; can also break a downstream lookup silently | [H5], gotchas §3 |
| A field is `null` or empty while the source buffer clearly carried the value | VALUE | Value was parsed into a Bindy field but never wired to the output DTO field | [H7], gotchas §15 |
| Timeout case returns **500**, IIB returned **408** | VALUE | Timeout handler or circuit-breaker branch missing | gotchas §2, [M18] |
| "No record found" returns 404 where IIB returned **200** with empty body, or the reverse | VALUE | Not-found convention applied repo-wide instead of checked per flow | [R6], gotchas §5 and §14 |
| Error **message text** differs from the IIB text | VALUE | Message paraphrased instead of reproduced verbatim; or BIP broker text not carried over | [H13], gotchas §1 and §8 |
| With several invalid fields, a **different error** surfaces first | VALUE | Validation order does not match the ESQL's order | [H12] |
| Wrong error returned for an auth/token case, for example 500 where 401 is expected | VALUE | An `ErrorUtil.errorResponse(...)` call sits inside a `try` whose `catch` handles a different case and overwrites it | [H14], gotchas §16 |
| Requester-ID field carries a literal such as `"ECS"` | VALUE | Hardcoded instead of resolved from the EIE user context (QL_CICS); the opposite convention applies to RxClaim SBO / MQ_COBOL | [H1], [H1a] |
| A field's JSON type differs (string vs number, list element type) | TYPE | Request/response model type differs from the IIB/JSON contract | [H11] |
| Response field names `httpCode` / `httpMessage` / `moreInformation` differ | STRUCTURAL | Response DTO naming does not follow the convention | gotchas §13 |
| A reference-data description is missing or wrong | VALUE | Lookup key not normalised (see SLS above), or ref-data wired when the IIB flow never called it | [H5], [H10] |
| Service reaches no backend at all (connection error, wrong endpoint, 401 from vendor) | — (not a field variance) | Transport / config / credentials: check config keys, queue names, OAuth setup; may be non-regenerable | gotchas §10, §11, [H15] |

## How to use this table

1. Match on the *symptom*, not the explanation.
2. Record the variance with the suggested class and cite the source id in the variance's notes, so the RCA agent can start from the right file.
3. If nothing matches, record the variance as unexplained. Do not stretch a row to fit.
