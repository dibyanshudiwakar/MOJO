<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/translation-table.md lines 1–20. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

# IIB → MS Translation Table

Quick-reference mapping from common IIB/ESQL constructs and shared-library calls to their Spring Boot / CVS EIP framework equivalents. Use this during GENERATE (per-flow generation — map ESQL modules to Java classes) alongside the connector-specific reference file.

⚠️ **Before applying the reference-data row below**: grep the actual IIB ESQL for a real reference-data call first. Cross-repo evidence found **zero of five** surveyed QL/CICS repos call ref-data at all — don't wire a `RefDataConnector` speculatively just because this table lists the mapping as available. See `gap-checklist.md` item [H10].

| IIB Construct | MS Equivalent |
|---|---|
| `ESLReferenceDataCacheHelper.mapToEMM(collId, "ql", value)` | `referenceDataService.getDescription(collectionId, value)` — `mapToEMM` is a pass-through; confirm the flow actually calls this before wiring it (see `gap-checklist.md` [H10]) |
| `ESLConfigurationManager` | `application.properties` / `@ConfigurationProperties` |
| `ESLMetricHelper` | Micrometer / Spring Actuator (auto-wired by the framework) |
| `ESLCommonUtil` | Inline Java utility methods, no framework equivalent needed |
| `ESLClientTraceCacheHelper` | Not needed in the MS |
| IIB HTTP Input node path `/edb/` segment | MS `CONTEXT_ROOT` uses `/cs/` — e.g. IIB `/pbmclaim/api/v1/edb/{service}` → `${CONTEXT_ROOT:/pbmclaim/api/v1/cs/{service}}` |
| IIB EhCache | `ConcurrentHashMap`, lazily initialized, no TTL (unless the specific flow genuinely needs one) |
| DB2 table accessed for a small static/config-like set (e.g. `TIEZ_IMG_EXC_ZNE`) | A `application.{property-name}` config property + a small repository class parsing it into a cache — not a live DB2 call, when the source data is effectively static config |
| `ESLDrugCacheHelper` | Drug downstream REST call |
| `ESLPharmacyCacheHelper` | Pharmacy downstream REST call |
| `ESLPrescriberCacheHelper` | Prescriber downstream REST call |
| `getBrokerErrorShortDescription` (external Java function called from ESQL, `com.cvscm.esd.common.util.ESDExceptionListUtil`) | No direct Java equivalent needed — the generic broker-exception-tree walk this function supports has no MS analog; Java exceptions are caught directly by type instead |
