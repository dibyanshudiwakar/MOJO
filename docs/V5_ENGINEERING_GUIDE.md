# V5 Engineering Guide

## Components
- `ACEParser`: deterministic XML and ESQL inventory, dependency, behavior, interface, and ambiguity extraction.
- `FrameworkConventionExtractor`: Maven/Gradle, source annotation, configuration, security, logging, error-handling, messaging, and observability discovery; versioned framework template and golden verifier.
- `SpringProjectGenerator`: traceable Maven project, externalized configuration, Spring application, flow services, tests, and file manifest.
- `BuildRunner`: allow-listed Maven/Gradle execution with timeout and hashed output evidence.
- `ACERuntimeAdapter` and `SpringBootRuntimeAdapter`: TLS-enabled HTTP invocation with correlation and response evidence.
- `DifferentialEvidenceEngine`: masks, ignores, tolerances, ordering controls, deep structural/value comparison, and evidence hashes.
- `ConfidenceRemediationService`: weighted evidence score, approved-pattern eligibility, direct-fix or clarification routing, tests, iteration, and rollback pointer.
- `OIDCIdentityProvider`: issuer, audience, expiry, subject, JWKS key lookup, RS256 signature validation, role/project/environment claims, and key caching.

## Governance boundary
The classes are implementation adapters. They do not establish connectivity or authorization until approved URLs, certificates, claims, credentials, repositories, and policies are configured. Generated flow services preserve provenance but unresolved ACE business semantics must remain clarification items and must not be guessed.
