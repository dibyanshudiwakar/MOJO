# Fixed-File Templates — Dockerfile, `.github/`, Helm chart, config-map skeleton

**Why this file exists:** several migrations (`RankedMatch`, `GetGrievanceSummary`) shipped real bugs in exactly these "fixed"/setup files — a `sed` substitution that missed a spaced-out name variant, missing Helm values keys, wrong file casing, a malformed annotations block — because they were hand-generated from memory instead of copied from a verified source. This file is that verified source: literal, copy-ready templates extracted directly from **12 real training repos** across all 4 confirmed backend types, plus the explicit list of where those repos disagree with each other so a copy-paste doesn't silently pick up one repo's bug as if it were the pattern.

**How to use this file during Phase 1 (Scaffold):** don't hand-generate the Dockerfile, `.github/`, or Helm chart. Instead:
1. Check whether a **training repo for the exact same service** already exists (see the categorization below) — if the migration target itself is a re-run or near-duplicate of a training service, mirror that repo's fixed files directly, not the generic templates here.
2. Otherwise, copy the relevant template block below for the detected backend type, substitute the placeholders, and use the decision tables to make the choices this file can't make for you (JVM flags, Helm family, `permissions:` block).
3. Cross-check the result against the "known bugs to avoid" section before treating a checkpoint as passed.

---

## Training vs. testing repo map (evidence provenance)

| Backend type | Training repos (ground truth used below) | Testing repos (this skill's own migrations, validated *against* training evidence, not used to derive it) |
|---|---|---|
| QL/CICS + Camel Bindy | `beneficiarystatusservice`, `findallergiesanddiagnosis`, `inductionorderservices`, `orderimageinfoservice`, `BenService-GetBeneficiaries` | `ValidateBridgeSupply`, `GetGrievanceSummary`, `Comments`, `prescriptionstatusservice` |
| RxClaim SBO (XML/JAXB) | `carriersearchservice`, `memberidcardprofile`, `messagecodeservice` | `cumulativeCopaySummaries`, `memberseobpreferences`, `EmergencyOverrides` |
| IBM MQ + COBOL Copybook | `fepclaimdetail` (only one — treat everything below as single-repo evidence) | — |
| REST → External/Direct | `EOB-ExplanationOfBenefit`, `GetGrievancesAppeal`, `GetGrievanceByCriterias` | `rankedMatch`, `RET-Pharmacy-Checkinimzprescriptions`, `DMRClaimStatus` |
| Deployment scaffolding only (not a real MS repo) | `EOB-ExplanationOfBenefits` (plural) — generic onboarding scaffold, no `pom.xml`/`src/`; has 3 alternative deployment mechanisms (Helm+OpenShift-Route, AKS/GKE+Ingress+Istio, raw OpenShift `Template` JSON). Only useful if a future target genuinely isn't OpenShift+Helm. | — |

All real MS repos live at `phase1\<service>\EIPMS_cvs-v1-*-oci\` or `phase2\<service>\EIPMS_cvs-v1-*-oci\` — never inside `Migrated\` (that's this skill's own output, not evidence).

---

## 1. `.github/` — one strong shared skeleton across ALL 4 backend types

Confirmed: the reusable-workflow call structure is identical across QL/CICS, RxClaim SBO, MQ_COBOL, and REST — this is an org-wide CI/CD convention, not backend-type-specific.

### `.github/config/DeployMetadata.json` — canonical form (11/13 real repos)
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
`orderimageinfoservice` is the one confirmed exception — a compact single-line array **missing "TRG"** (16 entries, not 17) while its own `deploy.yaml` still offers TRG as a dropdown option. That's a genuine content gap in that one training repo, not a variant to copy — always use the 17-entry form above.

### `.github/workflows/build.yaml`
```yaml
---
name: {{SERVICE_DISPLAY_NAME}} CI Build
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
The `with:` block is **byte-identical across every one of the 12 training repos, every backend type** — never change these values. `minimum-code-coverage: '0'` appears exactly once (`fepclaimdetail`) — that's a real repo choosing to disable the gate, not evidence the default should be `'0'`; keep `'70'` unless told otherwise. `orderimageinfoservice` uses `master`-only branches (drops `main`) — a real but almost certainly unintentional deviation; use the 5-branch list above.

### `.github/workflows/deploy.yaml`
```yaml
---
name: {{SERVICE_DISPLAY_NAME}} - Deploy
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
        default: {{ARTIFACT_ID}}
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
      environment: "${{ github.event.inputs.environment || github.event.client_payload.environment
        }}"
      artifact: "${{ github.event.inputs.artifact || github.event.client_payload.artifact
        }}"
      app_name: "${{ github.event.inputs.app_name || github.event.client_payload.app_name
        }}"
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
The 3-job chain (`Confirm-Workflow-Branch` → `Deploy` → `trigger-karate`) and the two reusable-workflow targets are confirmed identical across every training repo in every backend type.

**Decision point — `permissions:` block.** Roughly half the training repos add this right after `on:`/before `run-name:`:
```yaml
permissions:
  actions: read
  contents: write
  id-token: write
```
No pattern ties its presence to backend type or phase — it looks like whichever onboarding-template generation was used at scaffold time. **Default to including it** for new migrations (narrows the workflow's token scope, strictly safer, never observed to break anything) unless a same-service sibling repo shows it deliberately absent.

---

## 2. Dockerfile — genuinely no fixed template; here's the decision table

Every one of the 13 real repos checked has a **different** Dockerfile — different maintainer, different `mkdir` grouping, different JVM flags. There is no "the" Dockerfile to copy verbatim; there's a skeleton plus 3 real decision points.

### Skeleton (identical shape across all 13)
```dockerfile
FROM quay-registry.aetna.com/cvs-base-images/os-eng/redhat/ubi9/openjdk-micro:21
LABEL LINE_OF_BUSINESS='cvs-pss'
LABEL maintainer='{{MAINTAINER_EMAIL}}'
LABEL Name='{{ARTIFACT_ID}}'
LABEL Version='0.0.1-SNAPSHOT'
USER root

RUN mkdir -p /os/spring-boot-{{ARTIFACT_ID}}/logs \
    && chmod -R a+rw /os/spring-boot-{{ARTIFACT_ID}} \
    && chown -R 1001:0 /os/spring-boot-{{ARTIFACT_ID}}

RUN mkdir -p /var/log && chmod 777 /var/log && chown 1001:0 /var/log

COPY ./{{ARTIFACT_ID}}-web/target/{{ARTIFACT_ID}}-web-*.jar /os/spring-boot-{{ARTIFACT_ID}}/app.jar

WORKDIR /os/spring-boot-{{ARTIFACT_ID}}

USER 1001

ENTRYPOINT ["java", {{JVM_FLAGS}}, "-jar", "app.jar"]
```

### Decision 1 — `{{MAINTAINER_EMAIL}}`
**Default: `IntegrationEngineeringTeam@CVSHealth.com`** — this is the majority value across the sample and the user-confirmed correct default (2026-09-29). `ESDTeamMembers@cvscaremark.com` appears in `beneficiarystatusservice`, `inductionorderservices`, and `GetGrievanceSummary`'s real repo — all 3 correlate with domains that call `com.cvscm.esd.common.util.ESDExceptionListUtil` in their ESQL (the "ESD" team's own long-standing broker-exception helper). Use the ESD address only if the target IIB source calls that same utility class; otherwise use the default.

### Decision 2 — `mkdir` grouping
Cosmetic — either the combined single-`RUN` form (skeleton above) or two separate `RUN` statements (one for the app log dir, one for `/var/log`) are both real, both fine. Default to the combined form shown above.

### Decision 3 — `{{JVM_FLAGS}}`
Every real combination observed:
| Combination | Seen in |
|---|---|
| `-XX:InitialRAMPercentage=25.0 -XX:MaxRAMPercentage=50.0 -XX:+UseG1GC` | `orderimageinfoservice`, `BenService-GetBeneficiaries`, all 3 REST training repos, `ValidateBridgeSupply` |
| `-XX:InitialRAMPercentage=25.0 -XX:MaxRAMPercentage=75.0 -XX:+UseG1GC` | `findallergiesanddiagnosis` |
| `-XX:InitialRAMPercentage=25.0 -XX:MaxRAMPercentage=75.0 -XX:+UseG1GC -XX:+UseStringDeduplication` | `inductionorderservices` |
| `-XX:InitialRAMPercentage=50.0 -XX:MinRAMPercentage=25.0 -XX:MaxRAMPercentage=75.0` (no G1GC) | `beneficiarystatusservice` |
| `-XX:InitialRAMPercentage=50.0 -XX:MinRAMPercentage=25.0 -XX:MaxRAMPercentage=75.0 -XX:+UseG1GC` | `fepclaimdetail` |

**Default to the first row** (`25.0`/`50.0`/`+UseG1GC`, no dedup) — it's the most frequent across the sample and is what this skill's own live-tested migrations already use. No evidence ties a specific combination to a specific backend type or resource profile; don't invent a rationale for picking a different one without a specific reason (e.g. a service known to handle unusually large/duplicated string payloads might justify `+UseStringDeduplication`).

---

## 3. Helm chart — two real families, not one template

Confirmed across **both** QL/CICS and RxClaim SBO: the fixed files split into a "RICH" family and a "SIMPLE" family, each internally byte-identical across the repos that use it. This split does not correlate with backend type — it looks like which onboarding-template generation was used when the repo was scaffolded. `fepclaimdetail` (MQ_COBOL, only repo) is closest to RICH plus MQ-specific additions. All 3 REST training repos share a third, REST-specific skeleton that resembles RICH but isn't identical to either QL/CICS family.

### Chart.yaml — near-universal (3 cosmetic variants, all functionally equivalent)
```yaml
apiVersion: v1
name: {{ARTIFACT_ID}}
version: 1.0.0
```
Some training repos add a `description:` line, some prepend a comment block about being able to delete this file for OpenShift-native deploys — cosmetic, safe to omit both.

### Template file naming
**Confirmed capitalized `Deployment.yaml`/`Service.yaml`/`Route.yaml`/`Secret.yaml` across all 12 training repos, every backend type** — this supersedes the earlier "casing is unsettled, 50/50" note in `structural-conventions.md` §13, which was based on only 2 repos (`RankedMatch` lowercase, `GetGrievanceSummary` capitalized). With 12 training repos now checked and all 12 capitalized, `RankedMatch`'s lowercase `deployment.yaml`/`service.yaml` is the outlier, not evidence of a 50/50 split. **Default to capitalized.**

### "RICH" family — confirmed byte-identical: `beneficiarystatusservice` + `findallergiesanddiagnosis`
```yaml
# templates/Deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Values.APP_NAME }}
  labels:
    app: {{ .Values.APP_NAME }}
    k8s-app: {{ .Values.APP_NAME }}
spec:
  replicas: {{ int .Values.PODS }}
  selector:
    matchLabels:
      k8s-app: {{ .Values.APP_NAME }}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 25%
      maxSurge: 0%
  template:
    metadata:
      labels:
        app: {{ .Values.APP_NAME }}
        deployment: {{ .Values.APP_NAME }}
        k8s-app: {{ .Values.APP_NAME }}
    spec:
      {{- if .Values.SERVICE_ACCOUNT }}
      serviceAccountName: "{{ .Values.SERVICE_ACCOUNT }}"
      {{- end }}
      volumes:
        - name: var-www-html-vol
          emptyDir: {}
        - name: configmap-vol
          configMap:
            name: {{ .Values.APP_NAME }}
            items:
              - key: application.{{ .Values.ENV }}-{{ .Values.CLUSTER }}.properties
                path: application.properties
            defaultMode: 420
      containers:
        - resources:
            limits:
              cpu: {{ .Values.CPU_LIMIT }}
              memory: {{ .Values.MEM_LIMIT }}
            requests:
              cpu: {{ .Values.CPU_REQUEST }}
              memory: {{ .Values.MEM_REQUEST }}
          readinessProbe:
            httpGet:
              path: "{{ .Values.CONTEXT_ROOT }}{{ .Values.HEALTH_CHECK_PATH }}"
              port: {{ int .Values.HEALTH_CHECK_PORT }}
              scheme: HTTP
            initialDelaySeconds: {{ int .Values.READINESS_INITIAL_DELAY_SECONDS }}
            timeoutSeconds: {{ int .Values.READINESS_TIMEOUT_SECONDS }}
            successThreshold: {{ int .Values.READINESS_SUCESS_THRESHOLD }}
            failureThreshold: {{ int .Values.READINESS_FAILURE_THRESHOLD }}
          terminationMessagePath: /dev/termination-log
          name: {{ .Values.APP_NAME }}
          livenessProbe:
            httpGet:
              path: "{{ .Values.CONTEXT_ROOT }}{{ .Values.HEALTH_CHECK_PATH }}"
              port: {{ int .Values.HEALTH_CHECK_PORT }}
              scheme: HTTP
            initialDelaySeconds: {{ int .Values.LIVENESS_INITIAL_DELAY_SECONDS }}
            periodSeconds: {{ int .Values.LIVENESS_PERIOD_SECONDS }}
            successThreshold: {{ int .Values.LIVENESS_SUCESS_THRESHOLD }}
            failureThreshold: {{ int .Values.LIVENESS_FAILURE_THRESHOLD }}
          env:
            - name: TZ
              value: America/Chicago
            - name: ENV_VAR_IN_RUNNING_CONTAINER
              value: {{ .Values.ENV_VAR }}
            - name: ENV_VAR_FROM_SECRET_TEXT
              valueFrom:
                secretKeyRef:
                  name: {{ .Values.APP_NAME }}
                  key: SECRET_TEXT
            - name: SITE_URL
              value: https://{{ .Values.SITE_URL }}
            - name: CONTEXT_ROOT
              value: {{ .Values.CONTEXT_ROOT }}
            - name: MANAGEMENT_ENDPOINTS_WEB_BASEPATH
              value: "{{ .Values.CONTEXT_ROOT }}{{ .Values.HEALTH_CHECK_PATH }}"
            - name: SPRING_CONFIG_LOCATION
              value: "/etc/config/{{ .Values.APP_NAME }}/data/"
          imagePullPolicy: Always
          volumeMounts:
            - name: var-www-html-vol
              mountPath: /var/www/html
            - name: configmap-vol
              mountPath: "/etc/config/{{ .Values.APP_NAME }}/data/"
          terminationMessagePolicy: File
          image: quay-registry.aetna.com/cvs-pss/{{ .Values.REPO_NAME }}:{{ .Values.ENV }}
          ports:
            - containerPort: {{ int .Values.CONTAINER_PORT }}
              protocol: TCP
      {{- if .Values.IMAGE_SECRET_NAME }}
      imagePullSecrets:
        - name: "{{ .Values.IMAGE_SECRET_NAME }}"
      {{- end }}
      restartPolicy: Always
      terminationGracePeriodSeconds: 30
      securityContext: {}
      dnsPolicy: ClusterFirst
      schedulerName: default-scheduler
```
```yaml
# templates/Route.yaml
apiVersion: v1
kind: Route
metadata:
  labels:
    app: {{ .Values.APP_NAME  }}
  name: {{ .Values.APP_NAME  }}
  annotations:
    haproxy.router.openshift.io/disable_cookies: 'true'
spec:
  host: {{ .Values.SITE_URL  }}
  path: {{ .Values.CONTEXT_ROOT  }}
  port:
    targetPort: 8080-tcp
  tls:
    termination: edge
  to:
    kind: Service
    name: {{ .Values.APP_NAME  }}
    weight: 100
  wildcardPolicy: None

---
apiVersion: v1
kind: Route
metadata:
  labels:
    app: {{ .Values.APP_NAME  }}
  name: {{ .Values.APP_NAME  }}-gtm
  annotations:
    haproxy.router.openshift.io/disable_cookies: 'true'
spec:
  host: {{ .Values.ROUTEAETNET_URL  }}
  path: {{ .Values.CONTEXT_ROOT  }}
  port:
    targetPort: 8080-tcp
  tls:
    termination: edge
  to:
    kind: Service
    name: {{ .Values.APP_NAME  }}
    weight: 100
  wildcardPolicy: None
```
```yaml
# templates/Secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.APP_NAME | toString }}
  labels:
    app: {{ .Values.APP_NAME | toString  }}
data:
  SECRET_TEXT: {{ .Values.BASE_64_ENCODED_SECRET_TEXT | toString }}
```
```yaml
# templates/Service.yaml
apiVersion: v1
kind: Service
metadata:
  labels:
    app: {{ .Values.APP_NAME  }}
  name: {{ .Values.APP_NAME  }}
spec:
  ports:
    - name: 8080-tcp
      port: {{ int .Values.SERVICE_PORT }}
      protocol: TCP
      targetPort: {{ int .Values.Target_Port }}
  selector:
    app: {{ .Values.APP_NAME  }}
  sessionAffinity: None
  type: ClusterIP
```

### "SIMPLE" family — confirmed byte-identical: `inductionorderservices` + `BenService-GetBeneficiaries` (QL/CICS); structurally the same shape as `memberidcardprofile`/`messagecodeservice` (RxClaim SBO)
```yaml
# templates/Deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Values.APP_NAME }}
  labels:
    app: {{ .Values.APP_NAME }}
spec:
  replicas: {{ .Values.PODS }}
  selector:
    matchLabels:
      app: {{ .Values.APP_NAME }}
  template:
    metadata:
      labels:
        app: {{ .Values.APP_NAME }}
    spec:
      volumes:
        - name: configmap-vol
          configMap:
            name: {{ .Values.APP_NAME }}
            items:
              - key: application.{{ .Values.ENV }}-{{ .Values.CLUSTER }}.properties
                path: application.properties
      containers:
        - name: {{ .Values.APP_NAME }}
          image: quay-registry.aetna.com/cvs-pss/{{ .Values.REPO_NAME }}:{{ .Values.ENV }}
          imagePullPolicy: Always
          ports:
            - containerPort: {{ int .Values.CONTAINER_PORT }}
              protocol: TCP
          readinessProbe:
            httpGet:
              path: "{{ .Values.HEALTH_CHECK_PATH }}"
              port: {{ int .Values.HEALTH_CHECK_PORT }}
            initialDelaySeconds: {{ int .Values.READINESS_INITIAL_DELAY_SECONDS }}
            timeoutSeconds: {{ int .Values.READINESS_TIMEOUT_SECONDS }}
          livenessProbe:
            httpGet:
              path: "{{ .Values.HEALTH_CHECK_PATH }}"
              port: {{ int .Values.HEALTH_CHECK_PORT }}
            initialDelaySeconds: {{ int .Values.LIVENESS_INITIAL_DELAY_SECONDS }}
            timeoutSeconds: {{ int .Values.LIVENESS_TIMEOUT_SECONDS }}
            periodSeconds: {{ int .Values.LIVENESS_PERIOD_SECONDS }}
          env:
            - name: SITE_URL
              value: https://{{ .Values.SITE_URL }}
            - name: CONTEXT_ROOT
              value: {{ .Values.CONTEXT_ROOT }}
            - name: SPRING_CONFIG_LOCATION
              value: "/etc/config/{{ .Values.APP_NAME }}/data/"
          resources:
            limits:
              cpu: {{ .Values.CPU_LIMIT }}
              memory: {{ .Values.MEM_LIMIT }}
            requests:
              cpu: {{ .Values.CPU_REQUEST }}
              memory: {{ .Values.MEM_REQUEST }}
          volumeMounts:
            - name: configmap-vol
              mountPath: "/etc/config/{{ .Values.APP_NAME }}/data/"
```
Note this family's probes have **no `scheme: HTTP`, no success/failureThreshold** — fewer fields than RICH, not a typo. Route/Secret/Service for this family are the same shape as RICH's, with two small real differences: the `-gtm` Route omits the `haproxy.router.openshift.io/disable_cookies` annotation, and `Service.yaml`'s `type:` is parameterized (`{{ .Values.SERVICE_TYPE }}`) rather than hardcoded `ClusterIP`.

### Which family to default to
**Default to RICH** for a brand-new migration — it's the more complete/defensive option (explicit success/failure thresholds, `var-www-html-vol`, k8s-app label, conditional serviceAccountName/imagePullSecrets hooks that cost nothing when unset) and it's what this skill's own `structural-conventions.md` §13 already documents in more detail from direct `RankedMatch`/`GetGrievanceSummary` verification. Use SIMPLE only if a same-service or closely-related sibling repo demonstrably uses it.

### REST's own skeleton (all 3 REST training repos, structurally RICH-like but not identical to either QL/CICS family)
Same `Deployment.yaml` shape as RICH above (`SPRING_CONFIG_LOCATION` mount pattern, same values keys), but:
- `annotations:` under the pod template metadata is present-but-empty in 2 of 3 (`annotations:` immediately followed by `creationTimestamp: null` — a malformed/copy-paste artifact, not valid nested YAML, harmless to Helm but don't replicate the malformation)
- `GetGrievancesAppeal` is the one exception with a **populated** annotations block — real, live Akeyless wiring (`akeyless/enabled`, `akeyless/inject_file_creds`). Per Rule 6 (revised 2026-10-07), this is now the template to actually generate when a service is determined to require Akeyless — not something to merely recognize and skip.
- `selector.matchLabels` sometimes has only `app` (missing `deployment`) — inconsistent across the 3, not meaningful; include both.

---

## 4. Config-map — small shared skeleton, everything else is connector-specific

Confirmed shared across all 4 backend types (this is what `gap-checklist.md` [M11] documents at the checklist level — this is the literal content). **This exact block also lives as a physical file at `references/templates/config-map-baseline.properties` — copy that file's content directly (and run the HANDOFF-A0 mechanical check in `workflow-and-state.md`) rather than retyping it from this markdown.** Confirmed byte-for-byte against the real `application.properties`/config-map pairs in `GetGrievanceSummary`, `RankedMatch`, and `ValidateBridgeSupply`'s actual phase2 repo (`phase2/ValidateBridgeSupply/EIPMS_cvs-v1-validatebridgesupply-oci/config/config-map/application.dev-pbmaz.properties`) — 3 independent real repos, not a guess:
```properties
server.port=${PORT:8080}
spring.main.banner-mode=off
server.servlet.context-path=/
server.tomcat.relaxed-path-chars=^
application.jackson.serializationInclusion=ALWAYS
application.jackson.enableLegacyErrorStructure=true
logging.config=classpath:{{LOGBACK_FILE_FOR_TIER}}
server.max-http-request-header-size=128KB
server.tomcat.max-http-response-header-size=128KB
server.tomcat.max-http-header-count=200

management.endpoints.web.exposure.include=health,info

#CIRCUIT BREAKER PROPERTIES
application.circuitbreaker.failureRateThreshold=90.0
application.circuitbreaker.slowCallDurationThreshold=8s
application.circuitbreaker.slowCallRateThreshold=80.0
application.circuitbreaker.writableStackTraceEnabled=false
```
`{{LOGBACK_FILE_FOR_TIER}}` = `logback-dev.xml` (dev/dev2/dev3), `logback-qa.xml` (qa/qa2/qa3), `logback-stress.xml`, `logback-uat.xml`, `logback-prod.xml` (prod-*) — all bundled inside `framework-core`, confirmed safe to reference without authoring locally (see `gap-checklist.md` [M11]).

⚠️ **One real repo (`GetGrievanceByCriterias`) uses `resilience4j.circuitbreaker.instances.*` instead of `application.circuitbreaker.*`.** Don't assume the property namespace above is the only one in use anywhere — it's the majority pattern (11/12), not a universal guarantee; check the target's own reference project if one exists.

Everything past this skeleton — QL/EIE-user-context/refdata/MQ/RxClaim/OAuth connector blocks — is always service-specific and must come from that flow's actual ESQL/properties, never copied from another service's connector block.

---

## 5. Known bugs in the training set — recognize, don't replicate

These are real, confirmed defects sitting in the training repos themselves. They're documented here specifically so a future migration doesn't mistake "it's in a real repo" for "it's the pattern":

- **`orderimageinfoservice`** (QL/CICS): hardcoded `containerPort: 8080` (not templated) in `Deployment.yaml`; a literal un-substituted `value: my env var` for `ENV_VAR_IN_RUNNING_CONTAINER`; `livenessProbe.timeoutSeconds` copy-pasted from `READINESS_TIMEOUT_SECONDS` instead of its own `LIVENESS_TIMEOUT_SECONDS`; `Route.yaml` uses `apiVersion: route.openshift.io/v1` instead of the standard `v1`; `DeployMetadata.json` missing "TRG".
- **`memberidcardprofile`** (RxClaim SBO): the same literal un-templated `value: my env var` bug, independently.
- **`GetGrievanceByCriterias`** (REST): commits real plaintext vendor credentials directly in its config-map (`application.mhk.password=Medhok2017`, etc.) — a direct violation of the "always encrypt via CVS endpoint" rule. Flag as a bad example if this repo is ever used as a structural reference; never copy the credential-handling approach.
- **`fepclaimdetail`**'s (MQ_COBOL) `Deployment.yaml` references `{{ .Files.Get "DBTRUSTSTORE.jks" | b64enc }}` for a `binaryData` mount, but no such file exists in that training repo's `config/deploy-config/` — if this template is reused, either supply that keystore file or drop the binaryData block; don't ship a Helm chart that will fail to render.
- **2 of 3 REST training repos** have an empty/malformed `annotations:` block in `Deployment.yaml` (see §3 above) — cosmetic (renders as `annotations: null`) but don't propagate the malformed YAML shape into new templates; either populate it correctly or omit the key entirely.
