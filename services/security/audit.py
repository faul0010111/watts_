"""Hash-chained optimisation audit log.

Each entry commits to the previous entry's digest, so removing or editing history
breaks the chain and ``verify()`` reports the first broken index. This is tamper
*evidence*, not tamper proofing: ship the chain head to an append-only store
(object lock, WORM bucket, transparency log) to make it tamper resistant.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, asdict, field


GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditEntry:
    index: int
    timestamp: float
    actor: str
    action: str
    recommendation_id: str
    policy_decision: dict
    change: dict
    outcome: str
    prev_hash: str
    entry_hash: str = ""
    # Audit 2.0. Defaults keep older callers working; the fields are inside the hash, so a
    # record written without a tenant cannot later be claimed to have had one.
    tenant: str = ""
    policy_version: str = "unversioned"
    entry_id: str = ""

    def compute_hash(self) -> str:
        """Commits to every field except the digest itself, including tenant and policy
        version: a decision is only meaningful next to the rule set that produced it."""
        payload = {k: v for k, v in asdict(self).items() if k != "entry_hash"}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Checkpoint:
    """A point in the chain worth anchoring somewhere WATTS cannot reach.

    Hash chaining proves the sequence has not been rewritten *given* a trusted head. It
    cannot prove that on its own, because whoever can rewrite the log can rewrite every
    digest in it. Publishing a checkpoint to an append-only store - object lock, WORM
    bucket, transparency log, a colleague's inbox - is what turns evidence into proof.
    """

    index: int
    head: str
    timestamp: float
    entries: int
    policy_version: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SealedSegment:
    """History removed by a retention policy, reduced to the fact that it existed."""

    from_index: int
    to_index: int
    from_timestamp: float
    to_timestamp: float
    sealed_hash: str          # the entry_hash the surviving chain continues from
    entries: int
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


class AuditLog:
    """Append-only, hash-chained, with tenant scoping and explicit retention."""

    def __init__(self, policy_version: str = "unversioned") -> None:
        self._entries: list[AuditEntry] = []
        self._checkpoints: list[Checkpoint] = []
        self._sealed: list[SealedSegment] = []
        self.policy_version = policy_version

    def append(self, *, actor: str, action: str, recommendation_id: str,
               policy_decision: dict, change: dict, outcome: str,
               timestamp: float | None = None, tenant: str = "",
               policy_version: str | None = None) -> AuditEntry:
        prev = self._entries[-1].entry_hash if self._entries else (
            self._sealed[-1].sealed_hash if self._sealed else GENESIS)
        index = (self._entries[-1].index + 1 if self._entries
                 else (self._sealed[-1].to_index + 1 if self._sealed else 0))
        entry = AuditEntry(
            index=index,
            timestamp=time.time() if timestamp is None else timestamp,
            actor=actor, action=action, recommendation_id=recommendation_id,
            policy_decision=policy_decision, change=change, outcome=outcome,
            prev_hash=prev,
            tenant=tenant,
            policy_version=policy_version or self.policy_version,
            entry_id=f"ae-{uuid.uuid4().hex[:12]}",
        )
        entry = AuditEntry(**{**asdict(entry), "entry_hash": entry.compute_hash()})
        self._entries.append(entry)
        return entry

    @property
    def head(self) -> str:
        if self._entries:
            return self._entries[-1].entry_hash
        return self._sealed[-1].sealed_hash if self._sealed else GENESIS

    # --- integrity ---------------------------------------------------------

    def verify(self) -> tuple[bool, int | None]:
        prev = self._sealed[-1].sealed_hash if self._sealed else GENESIS
        for i, e in enumerate(self._entries):
            if e.prev_hash != prev:
                return False, i
            recomputed = AuditEntry(**{**asdict(e), "entry_hash": ""}).compute_hash()
            if recomputed != e.entry_hash:
                return False, i
            prev = e.entry_hash
        return True, None

    def verify_against(self, checkpoint: Checkpoint) -> tuple[bool, str]:
        """Check the log still contains the history a published checkpoint committed to."""
        ok, broken = self.verify()
        if not ok:
            return False, f"chain broken at index {broken}"
        entry = next((e for e in self._entries if e.index == checkpoint.index), None)
        if entry is None:
            if any(s.from_index <= checkpoint.index <= s.to_index for s in self._sealed):
                return False, (f"index {checkpoint.index} falls inside a sealed segment; "
                               "the checkpoint predates the retention boundary")
            return False, f"index {checkpoint.index} is not present in this log"
        if entry.entry_hash != checkpoint.head:
            return False, (f"index {checkpoint.index} hashes to {entry.entry_hash[:16]}…, "
                           f"the checkpoint committed to {checkpoint.head[:16]}…")
        return True, "log matches the published checkpoint"

    def checkpoint(self, timestamp: float | None = None) -> Checkpoint:
        cp = Checkpoint(
            index=self._entries[-1].index if self._entries else -1,
            head=self.head,
            timestamp=time.time() if timestamp is None else timestamp,
            entries=len(self._entries),
            policy_version=self.policy_version,
        )
        self._checkpoints.append(cp)
        return cp

    def checkpoints(self) -> list[Checkpoint]:
        return list(self._checkpoints)

    # --- retention ---------------------------------------------------------

    def prune(self, *, before_timestamp: float, reason: str = "retention policy") -> SealedSegment | None:
        """Drop entries older than a cutoff, leaving a sealed record that they existed.

        Deleting audit history silently would make the chain verify perfectly while hiding
        that anything was removed - the exact failure the chain exists to prevent. Pruning
        therefore leaves a ``SealedSegment`` holding the range, the count and the hash the
        surviving chain continues from.
        """
        old = [e for e in self._entries if e.timestamp < before_timestamp]
        if not old:
            return None
        segment = SealedSegment(
            from_index=old[0].index, to_index=old[-1].index,
            from_timestamp=old[0].timestamp, to_timestamp=old[-1].timestamp,
            sealed_hash=old[-1].entry_hash, entries=len(old), reason=reason,
        )
        self._sealed.append(segment)
        self._entries = [e for e in self._entries if e.timestamp >= before_timestamp]
        return segment

    def sealed_segments(self) -> list[SealedSegment]:
        return list(self._sealed)

    # --- access ------------------------------------------------------------

    def read(self, *, tenant: str | None = None, actor: str | None = None,
             since: float | None = None) -> list[AuditEntry]:
        """Scoped read. A tenant filter here is a convenience, not the security boundary:
        the boundary is the policy engine deciding whether this principal may call it."""
        rows = self._entries
        if tenant is not None:
            rows = [e for e in rows if e.tenant == tenant]
        if actor is not None:
            rows = [e for e in rows if e.actor == actor]
        if since is not None:
            rows = [e for e in rows if e.timestamp >= since]
        return list(rows)

    def export(self, *, principal=None, tenant: str | None = None) -> dict:
        """Export the chain with its integrity state and retention history attached.

        ``principal`` is checked when supplied: exporting an audit trail is itself an
        action worth authorising, and an export that omitted its own verification status
        would be a document with no evidential value.
        """
        if principal is not None:
            from .rbac import RBAC, Permission
            RBAC.require(principal, Permission.READ_AUDIT)
        ok, broken = self.verify()
        rows = self.read(tenant=tenant)
        return {
            "entries": [e.as_dict() for e in rows],
            "count": len(rows),
            "head": self.head,
            "chain_valid": ok,
            "first_broken_index": broken,
            "policy_version": self.policy_version,
            "checkpoints": [c.as_dict() for c in self._checkpoints],
            "sealed_segments": [s.as_dict() for s in self._sealed],
            "tenant_scope": tenant or "all",
            "note": ("hash chaining evidences that this sequence has not been rewritten "
                     "relative to the head; it is not immutable storage on its own. Anchor "
                     "the head externally (docs/SECURITY.md)."),
        }

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e.as_dict(), sort_keys=True) for e in self._entries)
