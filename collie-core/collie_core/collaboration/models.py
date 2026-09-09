"""Versioned collaboration DTOs and fail-closed execution identity checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SharedExecutionContext:
    session_id: str
    requester_id: str
    credential_owner_id: str
    executor_device_id: str
    audience_revision: int
    context_cutoff: int
    run_id: str
    lease_token: str
    lease_expires_at: str
    publication_authorized: bool = False

    @classmethod
    def from_claim(
        cls,
        claim: Mapping[str, Any],
        *,
        enrolled_account_id: str,
        enrolled_device_id: str,
    ) -> SharedExecutionContext:
        required = (
            "session_id",
            "requester_id",
            "credential_owner_id",
            "executor_device_id",
            "audience_revision",
            "context_cutoff",
            "run_id",
            "lease_token",
            "lease_expires_at",
        )
        if any(claim.get(key) in (None, "") for key in required):
            raise ValueError("The shared run claim is incomplete.")
        requester = str(claim["requester_id"])
        credential_owner = str(claim["credential_owner_id"])
        executor = str(claim["executor_device_id"])
        if not enrolled_account_id or requester != enrolled_account_id:
            raise ValueError("This shared request belongs to another account.")
        if credential_owner != enrolled_account_id:
            raise ValueError("Shared work cannot use another person's credentials.")
        if not enrolled_device_id or executor != enrolled_device_id:
            raise ValueError("This shared request belongs to another device.")
        revision = int(claim["audience_revision"])
        cutoff = int(claim["context_cutoff"])
        if revision < 1 or cutoff < 0:
            raise ValueError("The shared run claim has an invalid revision.")
        return cls(
            session_id=str(claim["session_id"]),
            requester_id=requester,
            credential_owner_id=credential_owner,
            executor_device_id=executor,
            audience_revision=revision,
            context_cutoff=cutoff,
            run_id=str(claim["run_id"]),
            lease_token=str(claim["lease_token"]),
            lease_expires_at=str(claim["lease_expires_at"]),
            publication_authorized=bool(claim.get("publication_authorized", False)),
        )

    def permission_metadata(self) -> dict[str, Any]:
        return {
            "shared_session_id": self.session_id,
            "requester_id": self.requester_id,
            "credential_owner_id": self.credential_owner_id,
            "executor_device_id": self.executor_device_id,
            "audience_revision": self.audience_revision,
            "context_cutoff": self.context_cutoff,
            "shared_run_id": self.run_id,
            "lease_token": self.lease_token,
            "lease_expires_at": self.lease_expires_at,
            "publication_authorized": self.publication_authorized,
        }
