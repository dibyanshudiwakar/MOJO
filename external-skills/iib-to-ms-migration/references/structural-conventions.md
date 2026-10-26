# Structural Conventions — Folder Layout, Files, and Annotations

**Load this file for EVERY migration, in addition to the connector-group-specific reference file.** It was derived by diffing a skill-generated migration against the real, deployed `ValidateBridgeSupply` MS repo and finding real structural gaps. Everything here is folder/file/annotation structure — not business logic.

---

## 1. Repository-level layout

```
EIPMS_cvs-v1-<service>-oci/                          ← repo root
├── .github/                                          ← REQUIRED — CI/CD workflows, easy to forget entirely
│   ├── config/
│   │   └── DeployMetadata.json
│   └── workflows/
│       ├── build.yaml
│       └── deploy.yaml
├── Dockerfile                                        ← AT REPO ROOT, not inside the web module
├── cvs-v1-<service>-oci-parent/
│   └── pom.xml
├── cvs-v1-<service>-oci-web/
│   ├── pom.xml
│   └── src/main/
│       ├── java/com/cvshealth/eip/microservices/...
│       └── resources/
│           ├── application.properties                ← minimal: just spring.application.name + spring.profiles.active
│           └── application-local.properties           ← local-dev overrides
└── config/
    ├── config-map/
    │   └── application.<env>-<region>.properties      ← one per environment (see §5 for the full list)
    └── deploy-config/                                  ← Helm chart — REQUIRED, commonly missing from generated output
        ├── Chart.yaml
        ├── templates/
        │   ├── Deployment.yaml
        │   ├── Route.yaml
        │   ├── Secret.yaml
        │   └── Service.yaml
        └── values.<env>-<region>.yaml                  ← one per environment
```

⚠️ **Common miss:** generating only `config/config-map/*.properties` and skipping `config/deploy-config/` entirely. Both must be produced — the Helm chart is what actually deploys the service.

---

## 2. `.github` — CI/CD workflows (always required, always at repo root)

Every service repo carries a `.github/` folder with a build workflow, a deploy workflow, and a deploy-metadata file. This is **not optional** and is easy to skip entirely because it isn't Java source — check for it every time.

`.github/config/DeployMetadata.json`:
```json
{
  "environment_list": [
    "Dev", "Dev2", "Dev3", "QA", "QA2", "QA3", "Stress",
    "Prod", "Prod2", "Prod3", "Prod4", "Prod5",
    "TRG", "UAT", "UAT2", "DR", "DR2"
  ],
  "multiEnvDeploy": [],
  "artifact-repo-folder": "EIPMS",
  "LibraryVariableSets": [
    "OpenShift Deployment Global Variable Set",
    "Global - Variable Set (Universal for All Projects)"
  ]
}
```
Note: `environment_list` here is a **superset** of the `config/config-map`/`config/deploy-config` environment list in §4 — it additionally includes environments (`Prod2-5`, `TRG`, `UAT2`, `DR`, `DR2`) that may not need their own properties/values file but must still be deployable targets.

`.github/workflows/build.yaml` — calls the org's shared reusable CI workflow, does not inline build steps:
```yaml
---
name: <Service> Service CI Build
on:
  push:
    branches:
      - 'main'
      - 'master'
      - '*_Path1'
      - '*_Path2'
      - '*_Path3'
    paths-ignore:
      - '.github/**'
      - '*.md'
jobs:
  j2ee-oci-build:
    uses: cvs-health-enterprise-code/legacy-ci-gha-workflow-actions/.github/workflows/j2ee-oci.yml@main
    with:
      java-version: '21'
      container-build-tool: 'docker'
      environment: 'development'
      minimum-code-coverage: '70'
      jacoco-version: '0.8.12'
      maven-version: '3.9.12'
      SNYK_ORG: 'enterprise-services-layer-esl'
      all-projects: 'true'
    secrets: inherit
```

`.github/workflows/deploy.yaml` — `workflow_dispatch` + `repository_dispatch`, delegates to the shared OpenShift deploy workflow, then triggers Karate regression:
```yaml
---
name: <Service> Service - Deploy
on:
  workflow_dispatch:
    inputs:
      environment:
        required: true
        type: choice
        description: Environment
        options: [ Dev, Dev2, Dev3, QA, QA2, QA3, Stress, Prod, Prod2, Prod3,
                   Prod4, Prod5, TRG, UAT, UAT2, DR, DR2 ]
      artifact:
        required: true
        type: string
        description: artifact to deploy (image tag)
      app_name:
        required: true
        type: string
        default: cvs-v1-<service>-oci
        description: Application Name
      change_request:
        required: false
        type: string
        description: Change Request ID. **Only for Prod Deployments**
        default: ''
  repository_dispatch:
    types: [ autodeploy-from-ci ]
run-name: ${{ github.event.inputs.environment ||
  github.event.client_payload.environment }} - ${{ github.event.inputs.artifact
  || github.event.client_payload.artifact }}
jobs:
  Confirm-Workflow-Branch:
    if: github.ref != 'refs/heads/master' && github.ref != 'refs/heads/main'
    runs-on:
      group: cvs-linux-self-hosted
    steps:
      - name: Print Skip Message
        run: |
          echo "This workflow is not intended to be run on branches other than master or main. Skipping the workflow."
          exit 1
  Deploy:
    needs: Confirm-Workflow-Branch
    if: |
      always() &&
      needs.Confirm-Workflow-Branch.result == 'skipped'
    uses:
      cvs-health-enterprise-code/openshift-deploy/.github/workflows/deploy-to-openshift-reusable-workflow.yaml@main
    with:
      environment: "${{ github.event.inputs.environment || github.event.client_payload.environment }}"
      artifact: "${{ github.event.inputs.artifact || github.event.client_payload.artifact }}"
      app_name: "${{ github.event.inputs.app_name || github.event.client_payload.app_name }}"
      change_request: "${{ github.event.inputs.change_request }}"
    secrets: inherit

  trigger-karate:
    name: Karate regression
    needs: Deploy
    if: always()
    uses: cvs-health-source-code/cvs-v1-karate-regression-automation/.github/workflows/octopus-deployment-test.yml@main
    with:
      env: "${{ github.event.client_payload.environment || github.event.inputs.environment }}"
      app_repo: "${{ github.event.client_payload.app_name || github.event.inputs.app_name }}"
    secrets: inherit
```

---

## 3. Dockerfile — exact template

The Dockerfile lives at the **repo root** (sibling to the parent/web module folders), not inside `cvs-v1-<service>-oci-web/`.

```dockerfile
FROM quay-registry.aetna.com/cvs-base-images/os-eng/redhat/ubi9/openjdk-micro:21
LABEL LINE_OF_BUSINESS='cvs-pss'
LABEL maintainer='IntegrationEngineeringTeam@CVSHealth.com'
LABEL Name='cvs-v1-<service>-oci'
LABEL Version='0.0.1-SNAPSHOT'
USER root

RUN mkdir -p /os/spring-boot-cvs-v1-<service>-oci/logs \
    && chmod -R a+rw /os/spring-boot-cvs-v1-<service>-oci \
    && chown -R 1001:0 /os/spring-boot-cvs-v1-<service>-oci

RUN mkdir -p /var/log && chmod 777 /var/log && chown 1001:0 /var/log

COPY ./cvs-v1-<service>-oci-web/target/cvs-v1-<service>-oci-web-*.jar /os/spring-boot-cvs-v1-<service>-oci/app.jar

WORKDIR /os/spring-boot-cvs-v1-<service>-oci

USER 1001

ENTRYPOINT ["java", "-XX:InitialRAMPercentage=25.0", "-XX:MaxRAMPercentage=50.0", "-XX:+UseG1GC", "-jar", "app.jar"]
```

Key points:
- Base image: `quay-registry.aetna.com/cvs-base-images/os-eng/redhat/ubi9/openjdk-micro:21` (org-internal mirror, not a public registry)
- **Four** `LABEL` lines (not just `maintainer`) — `LINE_OF_BUSINESS`, `maintainer`, `Name`, `Version` — all single-quoted
- Runs as **non-root** (`USER 1001`), with explicit `mkdir`/`chmod`/`chown` for log directories before dropping privileges
- No `EXPOSE` directive
- JVM flags are fixed: `-XX:InitialRAMPercentage=25.0 -XX:MaxRAMPercentage=50.0 -XX:+UseG1GC`
- `COPY` path is relative to repo root, referencing the web module's `target/` output

---

## 4. Parent pom.xml — additional required elements

Beyond the `<parent>`/`framework.version` block already covered in the connector-specific reference files, the parent pom also needs:

```xml
<properties>
    <java.version>21</java.version>
    <project.reporting.outputEncoding>UTF-8</project.reporting.outputEncoding>
    <camel.version><!-- pin to what sibling repos in this family use, not a guessed "latest" --></camel.version>
    <springdoc.version><!-- same --></springdoc.version>
    <framework.version>3.2.2</framework.version>
</properties>

<scm>
    <connection>scm:git:https://github.com/cvs-legacy-source-code/EIPMS_cvs-v1-&lt;service&gt;-oci.git</connection>
    <developerConnection>scm:git:https://github.com/cvs-legacy-source-code/EIPMS_cvs-v1-&lt;service&gt;-oci.git</developerConnection>
    <url>https://github.com/cvs-legacy-source-code/EIPMS_cvs-v1-&lt;service&gt;-oci</url>
</scm>
```

⚠️ **`camel.version`/`springdoc.version` are pinned per-repo, not globally uniform** — sibling repos in the same family can differ (e.g. one uses camel `4.20.0` + springdoc `2.8.4`, another uses `4.22.1`/`3.1.0`). Rule 7 (framework version) covers `framework.version` — for `camel.version`/`springdoc.version`, prefer copying the value from the **closest sibling repo for the same service family** if one exists, over inferring "the highest available."

### Web pom.xml — explicit version tags

Each framework dependency in the **web** module's pom gets an explicit `<version>${framework.version}</version>` tag (don't rely solely on BOM management):

```xml
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-connector-qlcics</artifactId>
    <version>${framework.version}</version>
</dependency>
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-connector-refdata</artifactId>   <!-- only if a ref-data lookup is needed -->
    <version>${framework.version}</version>
</dependency>
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-restclient</artifactId>
    <version>${framework.version}</version>
    <!-- NOTE: this dependency also transitively brings in framework-core:
         EieHeader, EIPMicroserviceLogger, ErrorUtil, ErrorStatus,
         EIPMicroserviceProvider/EIPMicroserviceProviderCall/EIPMicroserviceOperation annotations.
         Always include framework-restclient even for a QL/CICS-only service — it is the source
         of EieHeader and ErrorUtil, not framework-connector-qlcics. -->
</dependency>
```

⚠️ **Common miss:** omitting `framework-restclient` because the service "only" talks to QL/CICS. `framework-restclient` is required in every service regardless of connector group — it's where `EieHeader`, `ErrorUtil`, `ErrorStatus`, and the `@EIPMicroserviceProvider*` annotations actually live.

---

## 5. Full environment property-file list

Generate a `config/config-map/application.<env>-<region>.properties` for **every** environment actually used by the org, not just dev/qa/prod:

```
application.dev-pbmaz.properties
application.dev2-pbmaz.properties
application.dev3-pbmaz.properties
application.qa-pbmaz.properties
application.qa2-pbmaz.properties
application.qa3-pbmaz.properties
application.stress-pbmaz.properties
application.uat-pbmaz.properties
application.prod-pbmaz.properties
application.prod-pbmri.properties
```

Each one carries the same key set (circuit breaker, `application.http.restclient.<name>.*` for every connector the service uses, `management.endpoints.web.exposure.include=health,info`) with only the URI/host and timeout values differing per environment. When a service has more than one outbound connector (e.g. QL **and** refdata), every environment file needs a properties block for **each** connector name:

> 🛑 **The config-map is the ONLY config source at runtime — it must be self-contained.** `Deployment.yaml` sets `SPRING_CONFIG_LOCATION=/etc/config/<APP_NAME>/data/`, and `spring.config.location` *replaces* the default config locations rather than adding to them (it is not `spring.config.additional-location`). So `classpath:/application.properties` is **never loaded** in a deployed pod: a key that exists only in the packaged file is silently absent in dev/qa/uat/stress/prod.
> Do not treat the `management.endpoints.web.exposure.include` clause above as optional boilerplate — **[live-tested 2026-09-29]** a config-map missing it served `health`→200 but `info`→404 and `metrics`→404, because Spring Boot's default web exposure is `health` alone; adding just that key restored 200/200/200. Use `health,info,metrics` when the service's actuator is expected to serve metrics (what the generated README's Observability section claims), and pair it with `management.endpoints.web.base-path=${CONTEXT_ROOT:<default>}/actuator`.
> This clause previously existed *only* as passive prose here, and was consequently skipped during generation on `cumulativeCopaySummaries` — the same way §1's repo-root wrapper convention was skipped on `ValidateBridgeSupply`. It is now backed by an active step (`workflow-and-state.md` HANDOFF-A) and a mechanical check (`gap-checklist.md` **[M15]**). Run those; don't rely on remembering this paragraph.

> 🛑 **Host convention (standing instruction, 2026-10-23): every internal-CVS-backend connector below shares one host family, `<env>eippbm-oci.cvshealth.com` — plain `pbm-oci`, never `pbmaz`/`pbmri`, in every environment including `prod`.** The `az`/`ri` suffixes only ever belong in the config-map **filename's** `<cluster>` segment (`application.prod-pbmaz.properties` / `application.prod-pbmri.properties`, naming the two physical prod data centers / the Helm `CLUSTER:` value, §3) — never in the URI value inside the file. Real repos are inconsistent here (several phase-1/early-phase-2 services leaked `pbmaz` into the URI itself) — that's the pre-correction convention, not a pattern to match. See `mandatory-migration-rules.md` Rule 12 for the full rule, including the QA-only exception for `application-local.properties`.

```properties
#QL CONNECTOR
application.http.restclient.ql.name=ql
application.http.restclient.ql.uri=https://<env>eippbm-oci.cvshealth.com/ql/request/v1
application.http.restclient.ql.connect-timeout=28000
application.http.restclient.ql.request-timeout=28000
application.http.restclient.ql.socket-timeout=28000
application.http.restclient.ql.max-total-connections=50
application.http.restclient.ql.default-max-per-route=20
application.http.restclient.ql.default-keep-alive-time-millis=20000
application.http.restclient.ql.test-harness-host=http://esl-wiremock.corp.cvscaremark.com:9000/<full path>

#RXCLAIM SBO CONNECTOR (only if this service talks to the RxClaim SBO gateway — see rxclaim-sbo-xml-jaxb.md)
application.http.restclient.rxclaim.name=rxclaim
application.http.restclient.rxclaim.uri=https://<env>eippbm-oci.cvshealth.com/rxclaim/request/v1
application.http.restclient.rxclaim.connect-timeout=10000
application.http.restclient.rxclaim.request-timeout=55000
application.http.restclient.rxclaim.socket-timeout=55000
application.http.restclient.rxclaim.max-total-connections=50
application.http.restclient.rxclaim.default-max-per-route=25
application.http.restclient.rxclaim.default-keep-alive-time-millis=20000
application.http.restclient.rxclaim.test-harness-host=https://esl-wiremock.corp.cvscaremark.com:9080

#QL-DB2 PROXY CONNECTOR (only if this service does a direct DB2 lookup the IIB side did via ODBC — see ql-cics-camel-bindy.md §6a)
application.http.restclient.qlhc.name=qlhc
application.http.restclient.qlhc.uri=https://<env>eippbm-oci.cvshealth.com/qldb/execute/query/v1
application.http.restclient.qlhc.connect-timeout=10000
application.http.restclient.qlhc.request-timeout=10000
application.http.restclient.qlhc.socket-timeout=30000
application.http.restclient.qlhc.max-total-connections=50
application.http.restclient.qlhc.default-max-per-route=25
application.http.restclient.qlhc.default-keep-alive-time-millis=20000

#ECS-DB2 PROXY CONNECTOR (only if this service does a direct ECS DB2 lookup — same family as qlhc, different path)
application.http.restclient.ecshc.name=ecshc
application.http.restclient.ecshc.uri=https://<env>eippbm-oci.cvshealth.com/ecsdb/executeQuery
application.http.restclient.ecshc.connect-timeout=10000
application.http.restclient.ecshc.request-timeout=10000
application.http.restclient.ecshc.socket-timeout=30000
application.http.restclient.ecshc.max-total-connections=50
application.http.restclient.ecshc.default-max-per-route=25
application.http.restclient.ecshc.default-keep-alive-time-millis=20000

#REFDATA CONNECTOR (only if this service does a reference-data lookup — see §8)
application.http.restclient.refdata.name=refdata
application.http.restclient.refdata.uri=https://<env>eippbm-oci.cvshealth.com/refdata
application.http.restclient.refdata.connect-timeout=10000
application.http.restclient.refdata.request-timeout=10000
application.http.restclient.refdata.socket-timeout=20000
application.http.restclient.refdata.max-total-connections=50
application.http.restclient.refdata.default-max-per-route=25
application.http.restclient.refdata.default-keep-alive-time-millis=20000
application.http.restclient.refdata.test-harness-host=https://esl-wiremock.corp.cvscaremark.com:9080
```

---

## 6. Package layout — the full, real class set

A QL/CICS service's package layout is **more subdivided** than a minimal "controller → service → connector" sketch. Use this as the canonical layout:

```
com.cvshealth.eip.microservices
├── <Service>Application.java              — @SpringBootApplication + separate @ComponentScan(basePackages={"com.cvshealth"})
├── constant/
│   ├── Constants.java                      — transaction/buffer-type IDs, restclient names, route IDs, misc literals
│   ├── ErrorConstants.java                 — ALL error message text (HTTP messages, verbatim IIB broker-exception text)
│   └── QLResponseConstants.java            — response-parsing constants (status codes, XML tag markers, buffer field lengths)
├── controller/
│   └── <Service>Controller.java            — @RestController, implements the Api interface, delegates only
├── swagger/
│   ├── api/
│   │   └── <Service>Api.java               — the OpenAPI contract interface (see §7)
│   └── model/
│       ├── request/                        — one record per request shape (see §7)
│       └── response/                       — one record per response shape (see §7)
├── service/
│   ├── <Service>Service.java               — top-level orchestrator only
│   ├── <Service>RequestValidatorService.java  — ALL input validation lives here, not in the orchestrator
│   ├── QLMessagingService.java             — QL round-trip + test-harness bypass (via RestClientWrapper, Rule 4)
│   └── ReferenceDataService.java           — only if the service does a live code→description lookup (§8)
├── connector/
│   └── <Service>QLConnector.java           — @EIPMicroserviceProviderCall wrapper (see §9)
├── ql/
│   ├── QLRequestBufferBuilder.java         — builds the request buffer via Bindy marshal calls
│   ├── QLResponseBufferParser.java         — detects/parses the response buffer via Bindy unmarshal calls
│   ├── QLErrorParser.java                  — builds the ECS-XML and TDS error message strings
│   └── FixedWidthBufferUtil.java           — signed-numeric padding + substring-extract helpers only
├── model/bindy/
│   └── <buffers>.java                      — plain @Data POJOs, no business/parsing methods (§10)
└── utility/
    ├── <Service>Utility.java               — EieHeader-derived requesterId/transactionId resolution (§11)
    └── <Service>CamelBindyUtility.java      — Camel RouteBuilder + marshal/unmarshal helper methods
```

Notable naming/organization rules:
- **Constants are split into 3 files** by concern (`Constants`, `ErrorConstants`, `QLResponseConstants`), not merged into one or two.
- **Validation logic lives in its own `*RequestValidatorService`**, separate from the orchestrating `*Service`.
- **EIE header resolution lives in its own `*Utility` component**, separate from the orchestrating service.
- Buffer builder/parser classes are named `QLRequestBufferBuilder`/`QLResponseBufferParser`/`QLErrorParser` — generic names, not necessarily prefixed with the service name.

---

## 7. DTOs — records with full OpenAPI + Jackson annotations

Every request and response DTO is a Java **record** (never a mutable Lombok `@Data` class) carrying both Jackson and springdoc annotations on every field:

```java
// Request DTO — no @JsonInclude, plain @JsonProperty + @Schema
@Schema(description = "Order details for bridge supply validation")
public record OrderRequest(

        @JsonProperty("orderNumber")
        @Schema(description = "Order number", example = "1530000716")
        String orderNumber,

        @JsonProperty("prescriptions")
        @Schema(description = "List of prescriptions to validate")
        List<PrescriptionRequest> prescriptions
) {}
```

```java
// Response DTO — adds @JsonInclude(NON_NULL) and @JsonPropertyOrder at the class level
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonPropertyOrder({"prescriptionNumber", "beneficiary", "dispensedDrug"})
@Schema(description = "Bridge supply validation result for a prescription")
public record PrescriptionResponse(

        @JsonProperty("prescriptionNumber")
        @Schema(description = "Prescription number", example = "919213532")
        String prescriptionNumber,

        @JsonProperty("beneficiary")
        @Schema(description = "Beneficiary associated with the prescription")
        Beneficiary beneficiary,

        @JsonProperty("dispensedDrug")
        @Schema(description = "Details of the dispensed drug")
        DispensedDrug dispensedDrug
) {}
```

Rules:
- Class-level `@Schema(description = "...")` on every DTO.
- Field-level `@JsonProperty("...")` **and** `@Schema(description = "...", example = "...")` on every field — the `example` value should be a realistic-looking sample, not a placeholder.
- Response DTOs additionally get `@JsonInclude(JsonInclude.Include.NON_NULL)` and `@JsonPropertyOrder({...})` at the class level, listing fields in the order they should serialize.
- **Never define a local `ErrorStatus` record.** Use the framework-provided `com.cvshealth.eip.microservices.frameworks.utilities.ErrorStatus` class — it already exists in `framework-restclient` and has the `{httpCode, httpMessage, moreInformation}` shape every service needs.

---

## 8. Reference-data lookups — prefer the live connector over a hardcoded map

When an IIB ESQL module hardcodes a code→description lookup table (e.g. a `CASE` statement mapping numeric codes to description strings — sometimes explicitly commented as a "temporary fix" in the ESQL), the MS migration should **not** replicate it as a static Java `Map`. Instead, wire it through the live reference-data service:

```java
@Service
@RequiredArgsConstructor
public class ReferenceDataService {

    private final RefDataConnector refDataConnector;

    public Map<String, RefDataQueryResponse> loadRefDataMap() {
        if (refDataConnector == null) return Collections.emptyMap();
        try {
            return refDataConnector.fetchOnCollectionIds(List.of(COLLECTION_ID));
        } catch (Exception e) {
            return Collections.emptyMap();
        }
    }

    public String getShortDescription(Map<String, RefDataQueryResponse> refDataMap, String code) { /* lookup */ }
    public String getLongDescription(Map<String, RefDataQueryResponse> refDataMap, String code) { /* lookup */ }
}
```

This needs `framework-connector-refdata` as a pom dependency and an `application.http.restclient.refdata.*` properties block (§5). **Confirm the correct ref-data collection ID with the QL/reference-data team** — it is not derivable from the IIB ESQL source (the collection ID used by the live service can differ from any ID referenced elsewhere in IIB comments).

---

## 9. Connector class and controller annotations

### Connector class
```java
@Component
@RequiredArgsConstructor
public class <Service>QLConnector {

    private final QlConnector qlConnector;

    @EIPMicroserviceProviderCall(provider = EIPMicroserviceProvider.QL_CICS, function = Constants.QL_CICS_FUNCTION)
    public ResponseEntity<QLResponse> validateBridgeSupply(QLRequest qlRequest) {   // method name matches the operation, not a generic "invoke"
        return qlConnector.invokeQLConnector(qlRequest);
    }
}
```
The connector's own method name should reflect the business operation (`validateBridgeSupply`, `getBeneficiaries`, etc.) rather than a generic `invokeQLConnector`/`invoke` — the generic name belongs to the framework's `QlConnector` bean being wrapped, not to this service's connector class.

### Controller
```java
@RestController
@RequiredArgsConstructor
public class <Service>Controller implements <Service>Api {

    private final <Service>Service service;

    @Override
    @EIPMicroserviceOperation                                    // ← required framework annotation, easy to miss
    public ResponseEntity<<Service>Response> validateBridgeSupply(<Service>Request request) {  // ← typed return, never ResponseEntity<?>
        return service.validateBridgeSupply(request);
    }
}
```
Two easy-to-miss details:
- `@EIPMicroserviceOperation` on the overridden endpoint method (framework-level operation tracking/metrics).
- The method's return type is the **concrete** response type (`ResponseEntity<ValidateBridgeSupplyResponse>`), never `ResponseEntity<?>`.

⚠️ **"Delegates only" means literally nothing else — confirmed by a real-sibling-repo diff, `EmergencyOverrides`, 2026-10-01.** A generated controller for that service had a `try/catch` around a token-validation call, an inline `toCanonicalRequest()` mapping method, and manual `outcome.getHttpStatus()`-based branching calling `ErrorUtil.errorResponse(...)` directly — none of which exists in the real sibling's controller, which is exactly the 8-line shape above (one field, one call, one `ResponseEntity.ok(...)` wrap, per endpoint). Treat any of the following inside a controller class as a defect to fix, not a style choice:
```java
// ❌ AVOID — any of this inside a *Controller class:
try {
    tokenValidationService.validateIfPresent(idToken);           // business/validation logic
} catch (SomeException e) {
    ErrorUtil.errorResponse(401, ...);                            // error-response call — belongs in service/util
}
SomeRequest request = toCanonicalRequest(id, drugId, ...);         // request-building/mapping — belongs in service/util
if (outcome.getHttpStatus() == 400) { ... }                        // status-code branching — belongs in service/util
```
All of that — token/header handling, request-shape translation, status-code branching, any `ErrorUtil.errorResponse(...)` call — belongs in the `*Service`/`*ServiceUtil` layer. The controller's only job is: call the service, wrap the result in `ResponseEntity`. See `mandatory-migration-rules.md` Rules 9 and 10 for two specific things that commonly leak into a controller (gateway-validation reimplementation, custom exception handling) and should never be there at all.

### API interface
```java
@Validated
@Tag(name = "<Service>", description = "...")
@RequestMapping(value = "${CONTEXT_ROOT:/pbmpharmacy/api/v1/cs/<service>}")
public interface <Service>Api {

    @Operation(
            operationId = "validateBridgeSupply",
            summary = "...",
            description = "...",
            responses = {
                    @ApiResponse(responseCode = "200", ..., content = @Content(..., schema = @Schema(implementation = <Service>Response.class))),
                    @ApiResponse(responseCode = "400", ..., content = @Content(..., schema = @Schema(implementation = ErrorStatus.class))),
                    // ... 404, 500, etc.
            })
    @PostMapping(value = "/orders/validatebridgesupply", consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<<Service>Response> validateBridgeSupply(@RequestBody <Service>Request request);
}
```
- `@Validated` at the interface level.
- `responses = {...}` nested inside a single `@Operation`, not separate top-level `@ApiResponse` annotations.
- `ErrorStatus` in the schema references the **framework** class (§7), imported from `com.cvshealth.eip.microservices.frameworks.utilities.ErrorStatus`.

---

## 10. Bindy model classes — plain data holders only

`model/bindy/*.java` classes carry `@Data` **only** (no `@Builder`, `@NoArgsConstructor`, `@AllArgsConstructor` needed — Camel Bindy constructs them via its own mechanism) and contain **no parsing or business-logic methods**:

```java
@Data
@FixedLengthRecord(length = 120, paddingChar = ' ')
public class W022VbsResponseRecord {

    @DataField(pos = 1, length = 12, align = "L", paddingChar = ' ')
    private String rxNumber;

    @DataField(pos = 115, length = 1, align = "R", paddingChar = '0')
    private String inhouseIndicator;

    @DataField(pos = 116, length = 5, align = "R", paddingChar = '0')
    private String bridgeSupplyCode;

    // NOTHING else — no getParsedXxx()/isXxx() helper methods here.
    // Parsing/interpretation of these raw field values happens in QLResponseBufferParser
    // or the orchestrating Service, using FixedWidthBufferUtil.
}
```

**Also:** when the IIB copybook defines a transaction header and a control/count buffer as two *separate* `01`-level groups but they are written to the wire as one contiguous segment (check the byte offsets — if the control buffer's offset picks up exactly where the header ends), model them as **one** Bindy class spanning the full contiguous length, not two separate classes. Splitting a single contiguous wire segment into multiple Bindy classes is a structural mistake — Camel Bindy marshals per-class, and marshalling two separately-instantiated objects back-to-back can introduce record separators that corrupt the buffer.

---

## 11. EieHeader — the real accessor methods, and when `framework-eieusercontext` is actually needed

Do not assume `EieHeader` exposes convenience getters like `getAccountIdentifierValue()` or `getApplicationShortName()` — **verify against the actual framework class**. The confirmed real accessors are:

```java
eieHeader.getEieUserContext()     // raw JSON string — must be manually parsed for fields like accountIdentifierValue
eieHeader.getApiTransactionId()   // transaction ID, when present
eieHeader.getEieCorrelationId()   // fallback correlation ID
eieHeader.getTestHarnessMode()    // "P" = production test-harness mode
eieHeader.getIdToken()            // raw ID token — see the framework-eieusercontext note below
eieHeader.getEieApplication()     // raw application-context JSON string, parsed the same way as getEieUserContext()
```

⚠️ **`framework-eieusercontext` is needed only when `getEieUserContext()` does NOT already arrive pre-populated.** Confirmed via `ValidateBridgeSupply` (QL_CICS, live-tested): the context was already populated by the time the service saw it, so no extra dependency was needed. Confirmed via `carriersearchservice`/`memberidcardprofile` (RxClaim SBO) and `fepclaimdetail` (MQ_COBOL): all three genuinely call `EieHeaderBuilder.createEieUserContextString(eieHeader.getIdToken())` from `framework-eieusercontext` to populate it themselves before use. This is category- (and possibly service-)dependent — check the target flow rather than assuming either way (see `mandatory-migration-rules.md` Rule 8 for the full correction history).

Manual parsing of `accountIdentifierValue` out of the raw context string (mirrors the IIB ESQL's own `SUBSTRING(... AFTER ... BEFORE ...)` pattern) belongs in a dedicated `*Utility` class:

```java
@Component
@RequiredArgsConstructor
public class <Service>Utility {

    private final EieHeader eieHeader;

    public String getRequesterId() {
        try {
            String context = eieHeader != null ? eieHeader.getEieUserContext() : null;
            if (context == null) return "";
            int start = context.indexOf(ACCOUNT_IDENTIFIER_VALUE_MARKER);   // "accountIdentifierValue\":\""
            if (start < 0) return "";
            int valueStart = start + ACCOUNT_IDENTIFIER_VALUE_MARKER.length();
            int end = context.indexOf(ACCOUNT_IDENTIFIER_VALUE_TERMINATOR, valueStart);  // "\"}"
            return end < 0 ? "" : context.substring(valueStart, end);
        } catch (Exception e) {
            return "";
        }
    }

    public String resolveTransactionId() {
        String apiTxId = eieHeader != null ? eieHeader.getApiTransactionId() : null;
        String correlationId = eieHeader != null ? eieHeader.getEieCorrelationId() : null;
        return apiTxId != null && !apiTxId.isBlank() ? apiTxId : correlationId != null ? correlationId : "";
    }
}
```

If a different service's IIB source resolves `applicationName` similarly (from `X-Eieapplication`), apply the same manual-marker-parsing pattern against whatever raw context string `EieHeader` actually exposes for it — **do not invent a getter name without confirming it exists** in the framework version pinned by this repo family.

---

## 12. Verification checklist — structural conventions

- [ ] `.github/config/DeployMetadata.json` + `.github/workflows/{build,deploy}.yaml` generated at repo root (§2) — do not skip because it isn't Java source
- [ ] Dockerfile at repo root (not inside the web module), matching the exact template in §3
- [ ] `config/deploy-config/` Helm chart generated (Chart.yaml + templates/ + per-env values.yaml)
- [ ] `config/config-map/` has one properties file per environment actually used (§5), not just dev/qa/prod
- [ ] Every runtime key in the packaged `application.properties` also appears in **all** config-map files — run the key-diff loop in `workflow-and-state.md` HANDOFF-A (`SPRING_CONFIG_LOCATION` replaces the classpath defaults, so the config-map must be self-contained). `management.endpoints.web.exposure.include` mandatory; `spring.profiles.active=local` never copied. Details + live-test evidence: `gap-checklist.md` **[M15]**
- [ ] Parent pom has `java.version`, `project.reporting.outputEncoding`, and `<scm>` block
- [ ] Web pom has explicit `<version>${framework.version}</version>` on every framework dependency, and includes `framework-restclient` even for QL-only services
- [ ] Constants split into `Constants` / `ErrorConstants` / `QLResponseConstants` (or equivalent concern-based split)
- [ ] Validation logic lives in a dedicated `*RequestValidatorService`, not inline in the orchestrator
- [ ] EIE header resolution lives in a dedicated `*Utility` component, using only confirmed real `EieHeader` accessors (§11)
- [ ] Reference-data-style lookups use a live `ReferenceDataService` + `RefDataConnector`, not a hardcoded Java map, when the pattern applies (§8)
- [ ] Every request/response DTO is a `record` with `@JsonProperty` + `@Schema` (response records also get `@JsonInclude(NON_NULL)` + `@JsonPropertyOrder`) — never a Lombok `@Data` class for API-facing DTOs
- [ ] `ErrorStatus` referenced from `framework-restclient`, never redefined locally
- [ ] Controller's overridden endpoint method carries `@EIPMicroserviceOperation` and returns the concrete response type, never `ResponseEntity<?>`
- [ ] API interface carries `@Validated`, and uses the nested `@Operation(responses = {...})` style
- [ ] Bindy model classes are `@Data`-only with no parsing/business methods
- [ ] Contiguous wire segments are modeled as one Bindy class, not split across multiple classes matching IIB's `01`-level copybook groups
- [ ] Generated output sits inside an `EIPMS_cvs-v1-<service>-oci` top-level directory — created as an explicit first step (`workflow-and-state.md` SCAFFOLD), not left as a bare `<service>` folder with the two Maven modules directly inside it

---

## 13. Helm chart — confirmed real values-schema and templates (cross-checked against RankedMatch + GetGrievanceSummary)

§1 above covers *that* the Helm chart exists and where; this section covers its actual real content, confirmed by reading two deployed charts directly — `EIPMS_cvs-v1-rankedmatchservice-oci` (REST/OAuth2) and `EIPMS_cvs-v1-getgrievancesummary-oci` (QL/CICS) — rather than assuming a generic "Helm best-practices" schema.

⚠️ **The values-key schema is flat and SCREAMING_SNAKE_CASE, not camelCase/nested.** A chart built with `appName`/`replicaCount`/`image.repository`/`resources.limits.cpu` (a plausible-looking, generic Helm convention) does **not** match this org's real templates, which reference `.Values.APP_NAME`/`.Values.PODS`/`.Values.CPU_LIMIT`/etc. directly. The two schemas are incompatible — deploying a chart built on the wrong one renders with missing/null values almost everywhere. Use the exact key names below. Confirmed identical across both backend types checked so far: `Chart.yaml`'s content and the core values keys below.

🔁 **Akeyless/SSL-cert-bundle scaffolding — generate it live by default now, whenever the service has a real credential (revised 2026-10-23, Rule 6 v3).** This supersedes the 2026-10-07 per-service-"is it required"-determination note, which itself superseded the original never-generate version. `rankedMatch`'s real repo has live, enabled Akeyless wiring (creds-only form) — whenever a service has an actual client-id/secret/API-key/cert to protect, generate the matching real form from `mandatory-migration-rules.md` Rule 6 (Form 1 creds-only, or Form 2 creds+certs if the vendor backend needs a private TLS cert) — fully live (`AKEYLESS_ENABLED: true`), never a disabled placeholder, and never gated on asking the user. Only skip the keys entirely when the service genuinely has **no credential at all** (confirmed from the IIB source / a real sibling repo — not assumed from phase). Don't know the exact secret path yet? That's expected — placeholder it (Rule 6) rather than waiting. The examples below show the real repos' baseline shape without Akeyless keys included inline — add the matching Rule 6 form on top whenever a credential exists.

### Chart.yaml — confirmed identical across both real repos checked
```yaml
apiVersion: v1
name: cvs-v1-<service>-oci
version: 1.0.0
```
Minimal — no `description`, `type`, or `appVersion` fields, and `apiVersion: v1` (not `v2`).

### Template filenames — capitalized is the confirmed dominant convention
**Corrected 2026-09-30** (prior note said "50/50, not settled" based on only 2 data points): a direct survey of 12 real training repos across all 4 backend types confirmed **all 12 use capitalized `Deployment.yaml`/`Service.yaml`/`Route.yaml`/`Secret.yaml`**. `RankedMatch`'s real repo (a Testing repo, not a Training repo) uses lowercase `deployment.yaml`/`service.yaml` — it is the single confirmed exception. **Default to capitalized**; use lowercase only if a same-service sibling repo demonstrably uses it. Either casing is safe for Helm itself (template discovery is case-insensitive on Windows) — this is purely a convention point.

### values.\<env\>.yaml — confirmed real key set (add Akeyless keys from Rule 6 on top, by default whenever a credential exists — see above)
```yaml
ENV: dev                                    # short env tag, used in the image tag and the configmap-vol key
REPO_NAME: cvs-v1-<service>-oci
SITE_URL: deveippbmaz-oci.cvshealth.com     # primary Route host
ROUTEAETNET_URL: deveippbm-oci.cvshealth.com # second Route's host (see Route below)
PATH: /
SERVICE_TYPE: ClusterIP
SERVICE_PORT: 8080
CONTAINER_PORT: 8080
Target_Port: 8080
Protocol: TCP
APPLICATION_NAMESPACE: cvs-v1-<service>-oci-dev
APP_NAME: cvs-v1-<service>-oci-dev          # NOT just the base repo name - env-suffixed
BASE_APP_NAME: cvs-v1-<service>-oci
ENV_VAR: my env var                         # confirmed present in both real repos with this exact literal value - looks like inert boilerplate, not service-specific; carry it over as-is
BASE_64_ENCODED_SECRET_TEXT: "SGRtIzUwMDAx" # confirmed the SAME placeholder value across every env in both real repos checked - not a live per-env secret
PODS: 1                                     # replica count; higher in stress/prod
Revision_History: 10
Active_Deadline:                            # confirmed blank in both real repos - not a typo, leave empty
interval_Seconds:
maxSurge:
maxUnavailable:
timeoutSeconds:
updatePeriodSeconds:
type: Rolling
CPU_REQUEST: 200m
MEM_REQUEST: 512Mi
CPU_LIMIT: 400m
MEM_LIMIT: 1024Mi
READINESS_INITIAL_DELAY_SECONDS: 60
READINESS_TIMEOUT_SECONDS: 30
READINESS_SUCESS_THRESHOLD: 1               # sic - "SUCESS" not "SUCCESS", confirmed real key spelling
READINESS_FAILURE_THRESHOLD: 5
LIVENESS_INITIAL_DELAY_SECONDS: 90
LIVENESS_TIMEOUT_SECONDS: 32
LIVENESS_PERIOD_SECONDS: 300
LIVENESS_SUCESS_THRESHOLD: 1
LIVENESS_FAILURE_THRESHOLD: 5
HEALTH_CHECK_PATH: /actuator/health
HEALTH_CHECK_PORT: 8080
CONTEXT_ROOT: /cvshealth/api/v1/cs/<service> # must match the API interface's own CONTEXT_ROOT default exactly
CLUSTER: pbmaz                               # matches the config-map filename suffix (application.<env>-<cluster>.properties)
```
The `PATH`/`SERVICE_TYPE`/`Protocol`/`Revision_History`/`ENV_VAR`/`type`/empty-rollout-placeholder keys were missing from an earlier version of this section (it was written from only the RankedMatch fix, then a values file reconstructed from memory for GetGrievanceSummary dropped them) — restored after directly diffing against both real repos again. Do not drop these keys again just because they look like inert boilerplate; they're part of the confirmed real schema.

### deployment.yaml — confirmed real template shape (add the Akeyless annotations block from Rule 6 on top, by default whenever this service has a credential)
Beyond the basics (image, ports, resources): `strategy: RollingUpdate` (`maxUnavailable: 25%`, `maxSurge: 0%`); two volumes — an empty `var-www-html-vol` and a `configmap-vol` that remaps `application.{{ .Values.ENV }}-{{ .Values.CLUSTER }}.properties` to `application.properties` via `items`; readiness/liveness probes built from `CONTEXT_ROOT` + `HEALTH_CHECK_PATH` (not a hardcoded `/actuator/health`) with full timeout/threshold tuning from the values above; an env var block that includes a `secretKeyRef` pull from the Secret (`ENV_VAR_FROM_SECRET_TEXT` ← `SECRET_TEXT`) — the Secret is actually consumed, not just declared; image built as `quay-registry.aetna.com/cvs-pss/{{ .Values.REPO_NAME }}:{{ .Values.ENV }}` (tag = env name, not a separate `image.tag` value); pod-level `restartPolicy`/`dnsPolicy`/`terminationGracePeriodSeconds`/`schedulerName` all set explicitly. Whenever this service has any credential to protect (per Rule 6, now the default, not a per-service question), add the pod-template `annotations:` block for `akeyless/enabled`/`akeyless/inject_file_creds`(`/inject_file_certs`) live — don't just recognize it in a sibling repo and skip generating it, and don't wait on the real secret path to exist first (placeholder it).

### Route.yaml — two Routes, not one
A primary Route (`host: {{ .Values.SITE_URL }}`, `path: {{ .Values.CONTEXT_ROOT }}`, an `haproxy.router.openshift.io/disable_cookies` annotation, `wildcardPolicy: None`) **and** a second `{{ .Values.APP_NAME }}-gtm` Route pointing at `{{ .Values.ROUTEAETNET_URL }}` (the internal/AETNET-network hostname) — both routing to the same Service. `apiVersion: v1`, not the more OpenShift-specific `route.openshift.io/v1`.

### service.yaml
Port named `8080-tcp` (not `http`), `port`/`targetPort` from separate `SERVICE_PORT`/`Target_Port` values, explicit `protocol: TCP` and `sessionAffinity: None`.

### Secret.yaml
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.APP_NAME | toString }}
  labels:
    app: {{ .Values.APP_NAME | toString }}
data:
  SECRET_TEXT: {{ .Values.BASE_64_ENCODED_SECRET_TEXT | toString }}
```
Actually consumed by `deployment.yaml` via `secretKeyRef` — don't leave it as an inert, unreferenced placeholder.
