"""
Grant all roles required for end-to-end testing to a target user.

Usage:
    python scripts/grant_tester_roles.py \
        --admin-email <GOVERNANCE_ADMIN email> \
        --admin-password <password> \
        --target-email somya.paliwal@cvshealth.com \
        [--base-url http://localhost:8080]
"""

import argparse
import sys
import requests

# Every role needed to run, approve, and observe the complete E2E pipeline
# without hitting a permission error at any stage.
ALL_REQUIRED_ROLES = [
    # ── Agent invocation roles ──────────────────────────────────────────────
    "REPOSITORY_INTAKE",       # Repository Intake agent
    "DORMANCY_ANALYST",        # Dormancy Intelligence agent
    "FRAMEWORK_ANALYST",       # Framework Baseline agent
    "ACE_ANALYST",             # ACE Analysis agent
    "SPECIFICATION_AUTHOR",    # Migration Specification agent
    "MIGRATION_PLANNER",       # Planning agent
    "CODE_GENERATOR",          # Code Generation agent
    "TEST_ENGINEER",           # Test Engineering agent
    "VARIANCE_ANALYST",        # Variance Analysis agent
    "RCA_ANALYST",             # RCA agent
    "REMEDIATION_ENGINEER",    # Remediation agent
    "GOVERNANCE_ENGINE",       # Governance agent
    "KNOWLEDGE_CURATOR",       # Knowledge & Evidence agent

    # ── Gate approval roles (G0–G5) ─────────────────────────────────────────
    "SERVICE_OWNER",           # Approve G0
    "JAVA_ARCHITECT",          # Approve G1
    "ACE_SME",                 # Approve G2
    "PRODUCT_OWNER",           # Approve G3
    "QA_LEAD",                 # Approve G4
    "RELEASE_MANAGER",         # Approve G5

    # ── Pipeline & orchestration ─────────────────────────────────────────────
    "ORCHESTRATOR",            # Run full orchestrated pipeline
    "AGENT_OPERATOR",          # Send model gateway requests

    # ── Admin & observability ─────────────────────────────────────────────────
    "GOVERNANCE_ADMIN",        # Grant roles, full admin override
    "GATE_ADMIN",              # Configure gate controls, retrigger lifecycle
    "AUDITOR",                 # View audit ledger
    "AGENT_REGISTRY_ADMIN",    # Manage LLM bindings

    # ── Delivery & support ────────────────────────────────────────────────────
    "DELIVERY_ENGINEER",       # Git delivery
    "CONNECTOR_ADMIN",         # Manage connectors
    "SERVICE_REGISTRAR",       # Register services
]


def login(base_url: str, email: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/api/v2/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"  [ERROR] Login failed ({resp.status_code}): {resp.text}")
        sys.exit(1)
    token = resp.json().get("token")
    if not token:
        print(f"  [ERROR] No token in login response: {resp.json()}")
        sys.exit(1)
    return token


def grant(base_url: str, token: str, target_email: str, role: str) -> bool:
    resp = requests.post(
        f"{base_url}/api/v2/auth/grant-role",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": target_email, "role": role},
        timeout=10,
    )
    if resp.status_code == 200:
        print(f"  [OK]    {role}")
        return True
    else:
        print(f"  [FAIL]  {role}  →  {resp.status_code}: {resp.text}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Grant all E2E test roles to a user.")
    parser.add_argument("--admin-email",    required=True,  help="GOVERNANCE_ADMIN account email")
    parser.add_argument("--admin-password", required=True,  help="GOVERNANCE_ADMIN account password")
    parser.add_argument("--target-email",   default="somya.paliwal@cvshealth.com",
                        help="Email of the user to grant roles to")
    parser.add_argument("--base-url",       default="http://localhost:8080",
                        help="Base URL of the running app  (default: http://localhost:8080)")
    args = parser.parse_args()

    print(f"\nLogging in as admin: {args.admin_email}")
    token = login(args.base_url, args.admin_email, args.admin_password)
    print(f"Logged in. Granting {len(ALL_REQUIRED_ROLES)} roles to {args.target_email} ...\n")

    failed = []
    for role in ALL_REQUIRED_ROLES:
        if not grant(args.base_url, token, args.target_email, role):
            failed.append(role)

    print(f"\n{'─'*55}")
    granted = len(ALL_REQUIRED_ROLES) - len(failed)
    print(f"Done — {granted}/{len(ALL_REQUIRED_ROLES)} roles granted successfully.")
    if failed:
        print(f"Failed roles ({len(failed)}):")
        for r in failed:
            print(f"  • {r}")
        sys.exit(1)
    else:
        print(f"\n{args.target_email} can now run the complete E2E pipeline without permission errors.")


if __name__ == "__main__":
    main()
