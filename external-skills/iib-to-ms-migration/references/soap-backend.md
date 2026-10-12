# SOAP Backend Pattern

**Not yet covered by this skill's training set** — no SOAP training service pair has been read/validated by this skill directly (see `getProgramRefData`, listed as "outside train/test list, existing reference" — worth reading in full before relying heavily on this file). Everything below comes from a sibling migration agent's spec, not from this skill's own live-tested findings. Treat it as a starting hypothesis, the same caution given to the IBM MQ + COBOL group elsewhere in this skill (only one training example there too) — verify against a real SOAP-backed repo before trusting it blindly, and update this file with corrections once you do.

---

## 1. Maven — web pom dependencies

```xml
<dependency>
    <groupId>com.cvshealth.eip.microservices.frameworks</groupId>
    <artifactId>framework-soapclient</artifactId>
    <!-- version from parent BOM — do NOT pin -->
</dependency>
<dependency>
    <groupId>io.swagger.core.v3</groupId>
    <artifactId>swagger-annotations</artifactId>
    <version>2.2.23</version>
</dependency>
<dependency>
    <groupId>jakarta.xml.bind</groupId>
    <artifactId>jakarta.xml.bind-api</artifactId>
</dependency>
<dependency>
    <groupId>org.glassfish.jaxb</groupId>
    <artifactId>jaxb-runtime</artifactId>
</dependency>
```

## 2. Package layout

```
com.cvshealth.eip.microservices
├── {ServiceName}Application         — @SpringBootApplication + SoapClientConfig exclusion (see §3)
├── connector/
│   ├── EOMSConnector                 — @Configuration + BeanPostProcessor; wires WebServiceTemplate
│   └── SoapEnvelopeInterceptor       — ClientInterceptor; rewrites SOAP-ENV → soapenv
├── constants/{ServiceName}Constants  — final class, all static String constants
├── controller/{ServiceName}Controller
├── service/{ServiceName}Service
└── swagger/
    ├── api/{ServiceName}Api
    ├── generated/                    — JAXB stubs: Request, Response, Items, ObjectFactory, package-info
    └── model/                        — Java 21 records with @JsonInclude(NON_NULL); NO Lombok on records
```

## 3. Application class

```java
@SpringBootApplication
@ComponentScan(
    basePackages = { "com.cvshealth.*" },
    excludeFilters = @Filter(type = FilterType.REGEX,
        pattern = "com\\.cvshealth\\.eip\\.microservices\\.frameworks\\.soapclient\\.SoapClientConfig")
)
// WHY: EOMSConnector manually wires WebServiceTemplate; the framework's auto-config would conflict with it.
```

This exclusion filter is **SOAP-specific** — never carry it over to a QL_CICS/RXCLAIM_SBO/MQ_COBOL/REST `Application` class (`gap-checklist.md` [M6]).

## 4. SoapEnvelopeInterceptor

Rewrites Spring-WS's default envelope to the format IIB's SOAP backend actually expects, on every outgoing call:
- Change the namespace prefix `SOAP-ENV` → `soapenv`; add `xmlns:xsd`/`xmlns:xsi`
- Remove the `SOAPHeader` entirely
- Convert any `ns2:`-prefixed namespace to the default namespace on body children, recursively

## 5. EOMSConnector

`@Configuration @RequiredArgsConstructor implements BeanPostProcessor`:
- `@Lazy @Autowired SoapConnector soapConnector`
- `@Bean Jaxb2Marshaller` (contextPath = the generated-stubs package)
- `@Bean WebServiceTemplate` (defaultUri, marshaller, registers `SoapEnvelopeInterceptor`, wires Apache HttpClient 5 with a `TrustAllStrategy`)
- `postProcessAfterInitialization`: if `beanName == "httpClientMap"` → inject a custom `CloseableHttpClient`
- Execution methods:
  ```java
  @EIPMicroserviceProviderCall(provider = EIPMicroserviceProvider.EOMS, function = "{service}")
  public Object execute{Service}(Request req, String soapAction) {
      return soapConnector.callWebService(req, soapAction);
  }

  @EIPMicroserviceProviderCall(provider = EIPMicroserviceProvider.EOMS, function = "{service}")
  public Object execute{Service}TestHarness(String url, Request req, String soapAction) {
      return soapConnector.callTestHarness(url, req, soapAction);
  }
  ```

## 6. SSL / Content-Length — check the framework version FIRST

Two known-working approaches exist, gated by the reference project's own `framework-starter` version — check its parent pom before picking one, never guess:

- **framework 2.3.2**: SSL properties still work — use `customSSLTrustStoreEnabled=true` + `truststoreLocation` + a JKS truststore file. No custom bean needed, no `spring.main.allow-bean-definition-overriding` needed.
- **framework 3.2.0+**: `SoapClientProperties` dropped those fields — SSL properties are silently ignored. Needs the custom `@Bean WebServiceTemplate` with `TrustAllStrategy` + a Content-Length interceptor (the `EOMSConnector` pattern in §5 already shows this).

Never independently re-derive a `TrustAllStrategy` fix if the simpler JKS-properties approach is what the actual pinned framework version supports — check the version first.

## 7. Properties keys

**`{backend-host}` in `application-local.properties` should resolve to the QA environment if this SOAP backend is internal to CVS (Rule 12) — this category has no training repo at all (single-source, unverified per this file's own caveat), so treat this as the same convention every other backend type confirms, applied here by extension rather than by direct SOAP evidence.**

```properties
application.http.soapclient.enabled=true
application.http.soapclient.name={service}
application.http.soapclient.uri=https://{backend-host}/{path}
application.http.soapclient.stubs-package=com.cvshealth.eip.microservices.swagger.generated
application.http.soapclient.{service}.test-harness-host=http://esl-wiremock.corp.cvscaremark.com:9000/{path}
application.http.restclient.{service}.connect-timeout=30000
application.http.restclient.{service}.request-timeout=30000
application.http.restclient.{service}.socket-timeout=30000
application.http.restclient.{service}.max-total-connections=50
application.http.restclient.{service}.default-max-per-route=25
application.http.restclient.{service}.default-keep-alive-time-millis=20000
```

## 8. Test harness

```java
if (eieHeader.getTestHarnessMode().equalsIgnoreCase("P")) {
    return connector.execute{Service}TestHarness(testHarnessUrl, request, SOAP_ACTION);
}
```

Same `getTestHarnessMode()` check as every other backend type — no SOAP-specific deviation here.

## 9. Response models — records, `@JsonInclude(NON_NULL)`, no Lombok

Consistent with the general convention confirmed live for QL_CICS and by cross-repo grep for REST (`structural-conventions.md` §7, `gap-checklist.md` [R14]) — SOAP response models should be records too. Generated JAXB stubs (`swagger/generated/`) are a separate concern from the API-facing `swagger/model/` records; don't conflate the two.
