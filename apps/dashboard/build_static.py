"""Build the WATTS Command Center as one self-contained HTML file.

The page renders exactly the data the report renders: `build` runs the same pipeline as
`python watts.py demo` and embeds its output as JSON. There is no second computation, so
the dashboard and the document cannot disagree about what happened in the window.

In a deployed WATTS this is the Next.js dashboard reading the live API over WebSockets. The
static build exists so the whole thing is inspectable from a laptop, with no server.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.reporting import PipelineConfig, run_pipeline  # noqa: E402

TEMPLATE = Path(__file__).with_name("template.html")


def build(out: Path, rps: float = 2.0, duration: float = 600.0, gpus: int = 4,
          seed: int = 42) -> Path:
    config = PipelineConfig(seed=seed, duration_s=duration, rps=rps, gpus=gpus)
    result = run_pipeline(config, command="python watts.py dashboard")
    data = result.as_dict()
    html = TEMPLATE.read_text().replace(
        "__WATTS_DATA__", json.dumps(data, separators=(",", ":"), default=str))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return out


def main() -> None:  # pragma: no cover - CLI
    import argparse
    parser = argparse.ArgumentParser(description="Build the WATTS Command Center")
    parser.add_argument("--out", type=Path, default=Path("apps/dashboard/dist/index.html"))
    parser.add_argument("--rps", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--gpus", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    print(build(args.out, args.rps, args.duration, args.gpus, args.seed))


if __name__ == "__main__":  # pragma: no cover
    main()
