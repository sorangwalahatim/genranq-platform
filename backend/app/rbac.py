"""Role-based access control (F-001). Roles from the Database Schema sheet:
owner/admin/manager/staff/client. Permissions from the build prompt §19.
"""
from __future__ import annotations

ROLES = ["owner", "admin", "manager", "staff", "client"]

PERMISSIONS = [
    "view", "create", "edit", "delete", "approve", "publish", "export",
    "manage_integrations", "access_credentials", "manage_billing", "manage_users",
]

MATRIX: dict[str, set[str]] = {
    "owner": set(PERMISSIONS),
    "admin": set(PERMISSIONS) - {"manage_billing"},
    "manager": {"view", "create", "edit", "delete", "approve", "publish", "export", "access_credentials"},
    "staff": {"view", "create", "edit", "export"},
    "client": {"view", "export"},  # limited to projects they are assigned to
}

# Role hierarchy rank: a user may only grant roles at or below their own (owner can grant anything).
RANK = {"owner": 5, "admin": 4, "manager": 3, "staff": 2, "client": 1}


def can(role: str, perm: str) -> bool:
    return perm in MATRIX.get(role, set())


def matrix() -> dict:
    return {r: {p: (p in MATRIX[r]) for p in PERMISSIONS} for r in ROLES}
