from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional, Set

from fastapi import Header, HTTPException, status

from .config import LodiaSettings


@dataclass(frozen=True)
class AuthContext:
    subject_id: str
    roles: Set[str]
    auth_mode: str
    tenant_id: str = "default"

    def has_any_role(self, required_roles: Iterable[str]) -> bool:
        required = set(required_roles)
        return not required or bool(self.roles.intersection(required))


@dataclass(frozen=True)
class TokenPrincipal:
    subject_id: str
    roles: Set[str]
    token_id: Optional[str] = None
    tenant_id: str = "default"


class AuthManager:
    def __init__(self, settings: LodiaSettings, token_resolver: Optional[Callable[[str], Optional[dict]]] = None):
        self.settings = settings
        self._tokens = _parse_token_specs(settings.auth_token_specs)
        self._token_resolver = token_resolver

    @property
    def enabled(self) -> bool:
        return bool(self._tokens or (self.settings.is_production and self._token_resolver))

    def require(self, authorization: Optional[str], required_roles: Iterable[str]) -> AuthContext:
        if not self.enabled:
            if self.settings.is_production:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="auth_not_configured",
                )
            return AuthContext(
                subject_id="demo_actor",
                roles={"admin", "reviewer", "contributor"},
                auth_mode="development",
                tenant_id="default",
            )

        token = _extract_bearer_token(authorization)
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")

        principal = self._lookup_token(token)
        if not principal:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_bearer_token")

        context = AuthContext(subject_id=principal.subject_id, roles=principal.roles, auth_mode="token", tenant_id=principal.tenant_id)
        if not context.has_any_role(required_roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient_role")
        return context

    def _lookup_token(self, token: str) -> Optional[TokenPrincipal]:
        for expected, principal in self._tokens.items():
            if hmac.compare_digest(token, expected):
                return principal
        if self._token_resolver:
            resolved = self._token_resolver(token)
            if resolved:
                return TokenPrincipal(
                    subject_id=resolved["subject_id"],
                    roles=set(resolved["roles"]),
                    token_id=resolved.get("token_id"),
                    tenant_id=resolved.get("tenant_id", "default"),
                )
        return None


def _parse_token_specs(specs: Iterable[str]) -> Dict[str, TokenPrincipal]:
    tokens: Dict[str, TokenPrincipal] = {}
    for spec in specs:
        parts = spec.split(":")
        if len(parts) < 2:
            continue
        token = parts[0].strip()
        roles = {role.strip() for role in parts[1].split(",") if role.strip()}
        subject_id = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else "service_account"
        tenant_id = parts[3].strip() if len(parts) >= 4 and parts[3].strip() else "default"
        if token and roles:
            tokens[token] = TokenPrincipal(subject_id=subject_id, roles=roles, tenant_id=tenant_id)
    return tokens


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    return authorization[len(prefix) :].strip() or None


def auth_dependency(manager: AuthManager, *roles: str):
    async def dependency(authorization: Optional[str] = Header(default=None)) -> AuthContext:
        return manager.require(authorization, roles)

    return dependency
