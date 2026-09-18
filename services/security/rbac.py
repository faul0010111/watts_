"""Role-based access control for the control plane itself."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Permission(str, Enum):
    READ_TELEMETRY = "telemetry:read"
    READ_ENERGY = "energy:read"
    RUN_SIMULATION = "simulation:run"
    PROPOSE_OPTIMIZATION = "optimization:propose"
    APPROVE_OPTIMIZATION = "optimization:approve"
    EXECUTE_CHANGE = "change:execute"
    MANAGE_POLICY = "policy:manage"
    READ_AUDIT = "audit:read"


ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "viewer": {Permission.READ_TELEMETRY, Permission.READ_ENERGY},
    "analyst": {Permission.READ_TELEMETRY, Permission.READ_ENERGY, Permission.RUN_SIMULATION,
                Permission.PROPOSE_OPTIMIZATION},
    "sre": {Permission.READ_TELEMETRY, Permission.READ_ENERGY, Permission.RUN_SIMULATION,
            Permission.PROPOSE_OPTIMIZATION, Permission.APPROVE_OPTIMIZATION,
            Permission.EXECUTE_CHANGE, Permission.READ_AUDIT},
    "operator": {Permission.READ_TELEMETRY, Permission.READ_ENERGY,
                 Permission.EXECUTE_CHANGE, Permission.READ_AUDIT},
    "security": {Permission.READ_TELEMETRY, Permission.MANAGE_POLICY, Permission.READ_AUDIT},
    "platform-admin": set(Permission),
    # The optimisation LLM is a principal like any other, with deliberately weak rights.
    "watts-assistant": {Permission.READ_TELEMETRY, Permission.READ_ENERGY,
                        Permission.RUN_SIMULATION, Permission.PROPOSE_OPTIMIZATION},
}


@dataclass(frozen=True)
class Role:
    name: str

    @property
    def permissions(self) -> set[Permission]:
        return ROLE_PERMISSIONS.get(self.name, set())


@dataclass(frozen=True)
class Principal:
    id: str
    roles: tuple[str, ...]
    tenant: str = "default"

    @property
    def permissions(self) -> set[Permission]:
        perms: set[Permission] = set()
        for r in self.roles:
            perms |= ROLE_PERMISSIONS.get(r, set())
        return perms


class RBAC:
    @staticmethod
    def can(principal: Principal, permission: Permission) -> bool:
        return permission in principal.permissions

    @staticmethod
    def require(principal: Principal, permission: Permission) -> None:
        if not RBAC.can(principal, permission):
            raise PermissionError(
                f"principal '{principal.id}' lacks {permission.value} "
                f"(roles: {', '.join(principal.roles) or 'none'})")
