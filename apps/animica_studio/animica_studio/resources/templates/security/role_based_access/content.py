"""{{CONTRACT_NAME}} — Role-Based Access Control (RBAC).

Author: {{AUTHOR}}

Built-in roles: DEFAULT_ADMIN_ROLE (granted to deployer).
Add custom roles as needed; use has_role() to guard methods.

Pattern:
    MINTER_ROLE = "MINTER"
    ...
    def mint(ctx, to, amount):
        _require_role(ctx, MINTER_ROLE)
        ...
"""

# Well-known role names
DEFAULT_ADMIN_ROLE = "ADMIN"
MINTER_ROLE = "MINTER"
PAUSER_ROLE = "PAUSER"

STORAGE = {
    "roles": "map(string, map(address, bool))",  # role -> {address -> granted}
    "role_admins": "map(string, string)",         # role -> admin_role
}

ABI = [
    {"name": "deploy",        "type": "constructor", "inputs": [{"name": "admin", "type": "address"}]},
    {"name": "grant_role",    "type": "function",    "inputs": [{"name": "role", "type": "string"}, {"name": "account", "type": "address"}]},
    {"name": "revoke_role",   "type": "function",    "inputs": [{"name": "role", "type": "string"}, {"name": "account", "type": "address"}]},
    {"name": "renounce_role", "type": "function",    "inputs": [{"name": "role", "type": "string"}]},
    {"name": "has_role",      "type": "function",    "inputs": [{"name": "role", "type": "string"}, {"name": "account", "type": "address"}], "outputs": [{"type": "bool"}], "stateMutability": "view"},
    {"name": "set_role_admin","type": "function",    "inputs": [{"name": "role", "type": "string"}, {"name": "admin_role", "type": "string"}]},
    {"name": "RoleGranted",   "type": "event",       "inputs": [{"name": "role", "type": "string"}, {"name": "account", "type": "address"}, {"name": "by", "type": "address"}]},
    {"name": "RoleRevoked",   "type": "event",       "inputs": [{"name": "role", "type": "string"}, {"name": "account", "type": "address"}, {"name": "by", "type": "address"}]},
]


def deploy(ctx, admin: str) -> None:
    ctx.storage["roles"] = {}
    ctx.storage["role_admins"] = {}
    _grant(ctx, DEFAULT_ADMIN_ROLE, admin, ctx.caller)


def _get_roles(ctx) -> dict:
    return ctx.storage.get("roles") or {}


def _set_roles(ctx, roles: dict) -> None:
    ctx.storage["roles"] = roles


def _grant(ctx, role: str, account: str, by: str) -> None:
    roles = _get_roles(ctx)
    role_map = roles.get(role) or {}
    role_map[account] = True
    roles[role] = role_map
    _set_roles(ctx, roles)
    ctx.emit("RoleGranted", {"role": role, "account": account, "by": by})


def has_role(ctx, role: str, account: str) -> bool:
    roles = _get_roles(ctx)
    return bool((roles.get(role) or {}).get(account))


def _require_role(ctx, role: str) -> None:
    if not has_role(ctx, role, ctx.caller):
        raise PermissionError(f"AccessControl: account {ctx.caller!r} is missing role {role!r}")


def _get_admin_role(ctx, role: str) -> str:
    admins = ctx.storage.get("role_admins") or {}
    return admins.get(role, DEFAULT_ADMIN_ROLE)


def grant_role(ctx, role: str, account: str) -> None:
    _require_role(ctx, _get_admin_role(ctx, role))
    _grant(ctx, role, account, ctx.caller)


def revoke_role(ctx, role: str, account: str) -> None:
    _require_role(ctx, _get_admin_role(ctx, role))
    roles = _get_roles(ctx)
    role_map = roles.get(role) or {}
    role_map.pop(account, None)
    roles[role] = role_map
    _set_roles(ctx, roles)
    ctx.emit("RoleRevoked", {"role": role, "account": account, "by": ctx.caller})


def renounce_role(ctx, role: str) -> None:
    """Caller removes themselves from a role."""
    roles = _get_roles(ctx)
    role_map = roles.get(role) or {}
    role_map.pop(ctx.caller, None)
    roles[role] = role_map
    _set_roles(ctx, roles)
    ctx.emit("RoleRevoked", {"role": role, "account": ctx.caller, "by": ctx.caller})


def set_role_admin(ctx, role: str, admin_role: str) -> None:
    """Change which role administers another role (DEFAULT_ADMIN_ROLE only)."""
    _require_role(ctx, DEFAULT_ADMIN_ROLE)
    admins = ctx.storage.get("role_admins") or {}
    admins[role] = admin_role
    ctx.storage["role_admins"] = admins

# CURSOR
