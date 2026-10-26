# Frameworks-repo fallback — for backends the skill has no trained pattern for

**When this applies (standing instruction from the user, 2026-10-13):** the IIB flow's backend is none of the 5 trained types — **SOAP, QL/CICS + Camel Bindy, RxClaim SBO (XML/JAXB), IBM MQ + COBOL Copybook, REST** — or it matches one of them but no trained reference repo for it exists in the workspace. Don't guess from the closest-looking type; read the org frameworks repo `EIPMS_cvs-technical-edb-cs-frameworks` and migrate against the module it provides.

Everything generated through this fallback is **single-source / unverified** (evidence-hierarchy tier 3 in `workflow-and-state.md`) until a real compile and a HANDOFF-C smoke test prove it. Say so in the HANDOFF output.

---

## 1. Finding the repo

The upstream repo is private (`https://github.com/cvs-legacy-source-code/EIPMS_cvs-technical-edb-cs-frameworks`, HTTP 404 when unauthenticated). Look for a local clone first — a folder named `*cs-frameworks*` in the workspace. If there is none, ask the user for a path or to clone it with their credentials. If it still can't be read, **stop and report a blocker** — never invent framework class names, annotations, or property keys from memory.

## 2. Read the branch that matches the target framework version — not master

Verified when this file was written (clone fetched 2026-10-13):

| Branch | `pom.xml` version | Note |
|---|---|---|
| `origin/3.2.x` | 3.2.3 | closest line to the skill's default `framework-starter` **3.2.2** |
| `origin/3.3.x` | 3.3.0 | |
| `master` | **4.0.0-SNAPSHOT** | last commit `BREAKING CHANGE: framework 4.0.0-SNAPSHOT` — do **not** use it as a reference for a 3.2.2 migration |

Older lines `1.0.x … 3.1.x` also exist. The clone's checked-out branch is usually `master`, so read other branches **without checking them out** (don't disturb the user's working tree):

```bash
git -C <frameworks-repo> show origin/3.2.x:framework-database/pom.xml
git -C <frameworks-repo> grep -n "@ConfigurationProperties" origin/3.2.x -- '*.java'
git -C <frameworks-repo> ls-tree -r --name-only origin/3.2.x framework-database
```

The module list is identical on 3.2.x, 3.3.x and master (verified), but class and property details can differ between versions — read the branch for the version the user chose in INTAKE (Rule 7). 3.2.x is at 3.2.3, i.e. slightly ahead of 3.2.2; note that in the action items.

## 3. Module map (read from the 3.2.x branch)

"Trained?" = a real repo in the workspace's `phase1/`/`phase2/` already depends on the artifact (checked by grepping poms).

| Module | What it is | Config prefix / enable key | Trained? |
|---|---|---|---|
| `framework-restclient` | HTTP client, `EieHeader`, `ErrorUtil` | `application.http.restclient.<name>.*` | ✅ REST |
| `framework-soapclient` | SOAP client | `application.http.soapclient.*` | ✅ SOAP (single-source) |
| `framework-connector-qlcics` | QL/CICS | — | ✅ |
| `framework-connector-rxclaimsbo` | RxClaim SBO | `rxclaimconfig` | ✅ |
| `framework-mqclient` | IBM MQ | `application.ibm.mq` | ✅ MQ_COBOL (single-source) |
| `framework-oauth` | OAuth2 / JWT assertion | `application.oauth` | ✅ |
| `framework-eieusercontext` | EIE user context | `application.eieusercontext` | ✅ |
| `framework-connector-refdata` | reference-data lookup | — | ✅ (4 repos) |
| `framework-cache` | Ehcache | `application.ehcache`, `application.ehcache.enabled=true` | ✅ (4 repos) |
| `framework-connector-db` | HTTP DB connectors: `QLDBConnector` (`executeQLDBStoreProc`, `executeQlDbSql`), `EcsDBConnector` (`executeEcsDbSql`, `executeEcsDbSqlResponseList`), `RxclaimDBConnector` (`executeRxclaimDbSql`, `…ResponseList`); request types `SqlRequest`, `StoreProcRequest`, `StoreProcParam` | — | ⚠️ 2 repos: `fepclaimdetail`, `orderimageinfoservice` |
| `framework-database` | JDBC template, multiple datasources, DB health | `application.databases`, `application.databases.enabled=true` | ❌ none |
| `framework-db-mongo` | MongoDB | `application.mongodb`, `application.mongodb.enabled=true` | ❌ none |
| `framework-db-redis` | Redis (`RedisUtility`, `SerializationFormat`) | `application.redis`, `application.redis.enabled=true` | ❌ none |
| `framework-carrieridvalidation` | carrier/client ID validation, backed by Redis | `application.carrieridvalidation.enabled=true` | ❌ none |
| `framework-legacyds` | ECS error/validation helpers (`ECSErrorMsgHelper`, `ECSException`, `ErrorCode`, `LegacyErrorUtil`, `ValidationHelper`) — **purpose inferred from class names only, read before using** | — | ❌ none |
| `framework-adapter` | **See §4 — not a migration target** | — | ❌ none |
| `framework-core`, `-starter`, `-bom`, `-integration-test` | base, parent pom, BOM, test support | `application.circuitbreaker`, `application.general`, `application.jackson`, `application.logging`, `application.security`, `application.vault` | ✅ |

For a ❌ row there is **no real repo in the workspace** to diff against, so the framework source is the only evidence. For the ⚠️ row, read the two real repos too.

## 4. `framework-adapter` is NOT a migration target

`KongAceAdapter`'s own Javadoc: it "transform[s] APIC request to IIB request and IIB response to APIC response" and "connects to IIB back-end". It keeps IIB running as the backend behind a Kong facade — the opposite of this skill's goal. If a flow seems to need it, stop and ask the user before using it; do not pull it in as the fallback module.

## 5. Procedure when the fallback triggers

1. Say which of the 5 trained types the backend was compared against and why none fit (cite the ESQL/msgflow evidence).
2. Locate the repo (§1), pick the branch for the chosen framework version (§2), and pick the candidate module(s) from §3. **The IIB-signal → module pairing is a heuristic** (for example, ESQL doing SQL against a database points at `framework-database` or `framework-connector-db`) — confirm it by reading the module and the ESQL; if two modules fit, ask the user.
3. Read the module's main classes, its `@ConfigurationProperties` prefix, its `@ConditionalOnProperty` enable key, and its public method signatures. Check `pom.xml` for the artifactId and what it pulls in transitively (Rule 2 still applies — don't add dependencies that aren't imported).
4. Generate using the framework's own classes and annotations, following the same layout conventions as the trained types (`structural-conventions.md`).
5. Record `backend_type: "NEW:<short-name>"` in `state.json`, and add to the action items: module used, branch + commit read (`git rev-parse origin/3.2.x`), and that the result is single-source/unverified.
6. All 13 mandatory rules and HANDOFF-C still apply unchanged. Rule 10 forbids *defining* exception types; using a framework-provided one is not defining one, but check with the user if unsure.

## 6. SSL / TLS (from the repo's `docs/sslBundles.md`, present on 3.2.x and master)

REST, SOAP, OAuth/JWT assertion, Redis and IBM MQ all use Spring Boot PEM SSL bundles (`spring.ssl.bundle.pem.<name>.*`); the bundle name is the integration's own logical name (REST client name, OAuth client name, Redis channel, MQ queue-manager name). This is the same mechanism Rule 6 Form 2 relies on.

**⚠️ Flagged correction candidate for `soap-backend.md` — not yet applied.** `soap-backend.md` says that on framework 3.2.0+ SOAP SSL properties are silently ignored and a custom `WebServiceTemplate` bean with `TrustAllStrategy` is needed. On the 3.2.x branch, `SoapClientProperties` indeed has no truststore fields, **but** `SoapClientConfig` builds its message sender from `httpClientMap.get(soapClientProperties.getName())` — the REST client of the same name, which `RestClientConfig` builds with the SSL bundle — and `docs/sslBundles.md` documents this as the supported path (`application.http.soapclient.name=<n>` plus `application.http.restclient.<n>.customSSLTrustStoreEnabled=true` and `spring.ssl.bundle.pem.<n>.truststore.certificate=…`). So the same-named-REST-client bundle should be tried before the custom `TrustAllStrategy` bean. This was established by reading the framework source, **not by running a SOAP migration** — treat it as code-read evidence until a live SOAP call confirms it.
