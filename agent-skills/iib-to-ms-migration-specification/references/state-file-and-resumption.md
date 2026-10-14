<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- references/workflow-and-state.md lines 1–3, 70–118. File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; every file mentioned there that this skill uses sits in its own references/ folder. -->

# Migration Workflow — Stages, State File, Compile-Gate Loop, Resumption

This is the operating procedure for the migration, adapted from a more mature sibling agent's stage-based, checkpoint-driven workflow. Follow this instead of a flat "generate everything then check" approach — it catches errors early (compile after every layer, not at the end) and survives interruption (state file).
## State File

Every migration in progress is tracked at `<target>/<project-name>/.iib2spring/state.json`:

```json
{
  "target_dir": "...",
  "iib_dir": "...",
  "project_name": "EIPMS_cvs-v1-{service}-oci",
  "service_name": "{service}",
  "package_root": "com.cvshealth.eip.microservices",
  "backend_type": "SOAP|QL_CICS|RXCLAIM_SBO|MQ_COBOL|RECAP|STRATUS|REST|MQ|DB2",
  "framework": {
    "parent_pom": { "groupId": "com.cvshealth.eip.microservices.frameworks", "artifactId": "framework-starter", "version": "..." },
    "reference_repo": "..."
  },
  "decisions": {
    "generate_tests": true,
    "lombok_on_models": false,
    "lombok_on_services": true,
    "profiles": ["local", "dev-pbmaz", "dev2-pbmaz", "dev3-pbmaz", "qa-pbmaz", "qa2-pbmaz", "qa3-pbmaz", "uat-pbmaz", "stress-pbmaz", "prod-pbmaz", "prod-pbmri"]
  },
  "smoke_test": {
    "request": "... collected at INTAKE Batch A item 6, verbatim from the user ...",
    "expected_response": "... collected at the same time, verbatim from the user ...",
    "attempts": [],
    "status": "pending|passed|failed_blocking"
  },
  "flows": [{
    "name": "...",
    "msgflow": "...",
    "esql": ["..."],
    "subflows": ["..."],
    "backend_type": "...",
    "tran_id": "...",
    "status": "pending|in_progress|done|skipped",
    "layers_done": [],
    "compile_passed": {},
    "gap_check_passed": false,
    "iterations": 0,
    "last_checkpoint": "INTAKE-A"
  }]
}
```

**Resumption protocol** — first action on every re-invocation of a migration already in progress:
1. Look for `<target>/.iib2spring/state.json`
2. If found and any flow is `in_progress` or `pending` → announce "Resuming flow X at checkpoint Y" → jump there, don't restart from INTAKE
3. If not found → start at INTAKE Batch A
