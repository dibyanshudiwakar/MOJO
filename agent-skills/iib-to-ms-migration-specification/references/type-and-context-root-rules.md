<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/translation-table.md lines 22–41. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

## Data type mapping

| ESQL type | Java type | Notes |
|---|---|---|
| `DECIMAL` / monetary | `double` | **Never** `int`/`long` for money — precision matters even though `double` has its own caveats; match whatever the existing reference project for this service actually does before introducing `BigDecimal` |
| `INTEGER` | `int` or `long` | Use `long` for any ID-like field that could exceed 2^31 (e.g. a 15-digit signed mainframe ID) |
| `CHARACTER` | `String` | |
| `DATE` | `String`, ISO `"yyyy-MM-dd"` | Don't introduce `LocalDate` unless the reference project already does — matching the existing JSON contract matters more than "more correct" typing |
| `BOOLEAN` | `Boolean` (nullable) or `boolean` (primitive) | Nullable `Boolean` when the IIB field can be genuinely absent, not just false |

## CONTEXT_ROOT derivation (all backend types)

Take the IIB HTTP Input node's URL path and replace the `/edb/` segment with `/cs/`:

```
IIB:  /pbmclaim/api/v1/edb/dmrclaimstatus
MS:   ${CONTEXT_ROOT:/pbmclaim/api/v1/cs/dmrclaimstatus}
```

Never leave `/edb/` in the MS context root (`gap-checklist.md` [R13]), and prefer the property-overridable form over a hardcoded literal (`gap-checklist.md` [R8]).
