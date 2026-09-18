"""Run context: how to reproduce the thing you are reading.

Every report carries the command that produced it, the seed, a hash of the configuration,
the version of the code and the version of the policy set. Without those, a number in a
report is an anecdote.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field

VERSION = "0.2.0"          # WATTS reference implementation


@dataclass(frozen=True)
class RunContext:
    run_id: str
    seed: int
    configuration: dict
    configuration_hash: str
    command: str
    version: str
    policy_version: str
    started_at: float
    python: str
    platform: str
    code_revision: str = "unknown"
    notes: str = ""

    @property
    def reproduction_command(self) -> str:
        return self.command

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "seed": self.seed,
            "configuration_hash": self.configuration_hash,
            "configuration": self.configuration,
            "command": self.command,
            "reproduction_command": self.reproduction_command,
            "version": self.version,
            "policy_version": self.policy_version,
            "code_revision": self.code_revision,
            "started_at": self.started_at,
            "started_at_utc": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(self.started_at)),
            "python": self.python,
            "platform": self.platform,
            "notes": self.notes,
        }


def make_run_context(*, seed: int, configuration: dict, command: str,
                     policy_version: str = "watts-policy-1") -> RunContext:
    payload = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return RunContext(
        run_id=f"run-{uuid.uuid4().hex[:12]}",
        seed=seed,
        configuration=configuration,
        configuration_hash=hashlib.sha256(payload.encode()).hexdigest()[:16],
        command=command,
        version=VERSION,
        policy_version=policy_version,
        started_at=time.time(),
        python=sys.version.split()[0],
        platform=f"{platform.system()} {platform.machine()}",
        code_revision=_git_revision(),
    )


def _git_revision() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=2)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"
