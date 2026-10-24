"""Server-owned authentication context and dev token resolution.

Client-supplied identity is never authority.  In Stage 1 development the only
identity source is a server-owned token registry (``scripts/dev_tokens.json``)
that maps opaque tokens to subject identities, teams, UI roles, and capability
grants.  This is a clearly labelled development mechanism; production identity
must come from a directory-synchronization adapter (out of Stage 1 scope and
reported as HOLD).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .domain.models import ActorIdentity, ActorType
from .domain.permissions import PermissionDeclaration
from .repositories.contracts import VisibilityScope

__all__ = ["AuthContext", "DevTokenAuth", "TokenEntry"]


@dataclass(frozen=True, slots=True)
class TokenEntry:
    subject_id: str
    display_name: str
    teams: tuple[str, ...]
    ui_role: str
    grants: tuple[PermissionDeclaration, ...]
    organization_member: bool = True
    governance_roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Effective authenticated caller and its capability grants."""

    actor: ActorIdentity
    grants: tuple[PermissionDeclaration, ...]
    teams: frozenset[str]
    ui_roles_by_team: Mapping[str, str]
    organization_member: bool
    organization_id: str | None = None
    governance_roles: frozenset[str] = frozenset()

    def visibility_scope(self) -> VisibilityScope:
        return VisibilityScope(
            actor_id=self.actor.subject_id,
            team_ids=self.teams,
            organization_member=self.organization_member,
            organization_id=self.organization_id,
        )


class DevTokenAuth:
    """Resolves bearer tokens from a server-owned dev token registry.

    The registry is git-ignored (it is a credential-shaped file), so a fresh
    clone has no ``dev_tokens.json``. When it is missing, the checked-in
    ``dev_tokens.example.json`` template next to it is loaded instead. That keeps
    a clean clone runnable for tests, CI, and the compose stack without
    committing a token file, and without weakening the git-ignore rule.
    """

    def __init__(self, tokens_file: str | Path) -> None:
        self._tokens: dict[str, TokenEntry] = {}
        path = Path(tokens_file)
        source = path
        if not source.is_file():
            # Fall back to the committed template in the same directory.
            template = path.with_name(f"{path.stem}.example{path.suffix}")
            if template.is_file():
                source = template
        self._source = source if source.is_file() else None
        if self._source is not None:
            payload = json.loads(self._source.read_text(encoding="utf-8"))
            for entry in payload.get("tokens", []):
                token = entry.get("token")
                if not token:
                    continue
                grants = tuple(
                    PermissionDeclaration(name=str(name))
                    for name in entry.get("grants", [])
                )
                self._tokens[token] = TokenEntry(
                    subject_id=entry["subject_id"],
                    display_name=entry.get("display_name", entry["subject_id"]),
                    teams=tuple(entry.get("teams", [])),
                    ui_role=entry.get("ui_role", "member"),
                    grants=grants,
                    organization_member=bool(entry.get("organization_member", True)),
                    governance_roles=tuple(entry.get("governance_roles", [])),
                )

    def resolve(self, token: str) -> AuthContext | None:
        entry = self._tokens.get(token)
        if entry is None:
            return None
        actor = ActorIdentity(
            subject_id=entry.subject_id,
            actor_type=ActorType.USER,
            display_name=entry.display_name,
        )
        roles_by_team = {team: entry.ui_role for team in entry.teams}
        return AuthContext(
            actor=actor,
            grants=entry.grants,
            teams=frozenset(entry.teams),
            ui_roles_by_team=roles_by_team,
            organization_member=entry.organization_member,
            governance_roles=frozenset(entry.governance_roles),
        )

    @property
    def has_any_token(self) -> bool:
        return bool(self._tokens)
