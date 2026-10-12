# Modular architecture
- `domain`: immutable contracts and lifecycle rules
- `repository`: persistence schema and policy seed data
- `services`: approval policy, evidence/versioning, agent execution, specification validation, differential comparison, RCA/remediation
- `server`: versioned REST adapter and security headers
- `static`: workspace UI
- `schemas`: machine-readable migration contract

All mutations are designed to create traceable records. Enterprise adapters remain boundaries rather than fake implementations.
