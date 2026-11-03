"""CLI for the on-demand pelican qualification test."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .pelican import DEFAULT_MODEL, QualificationError, run_pelican_test, write_report

ACCOUNTS_PATH = Path("var/accounts.jsonl")


def _load_api_key(explicit: str | None) -> str:
    if explicit:
        return explicit
    if env_key := os.environ.get("TH_API_KEY"):
        return env_key
    if not ACCOUNTS_PATH.exists():
        raise QualificationError(
            "no API key: pass --key, set TH_API_KEY, or register an account first"
        )
    lines = ACCOUNTS_PATH.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise QualificationError(f"{ACCOUNTS_PATH} is empty")
    return json.loads(lines[-1])["api_key"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="th2api-qualify",
        description=(
            "Run the pelican SVG-animation qualification test and write an "
            "auditable HTML report. Expensive: a normal run thinks for tens of "
            "thousands of tokens."
        ),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--effort",
        default="max",
        help="reasoning_effort sent to the gateway (default: max)",
    )
    parser.add_argument("--max-tokens", type=int, default=64_000)
    parser.add_argument("--timeout", type=float, default=1_800.0)
    parser.add_argument("--output-dir", default="var/tests")
    parser.add_argument("--key", help="API key; defaults to the latest registration")
    args = parser.parse_args(argv)

    try:
        run = run_pelican_test(
            _load_api_key(args.key),
            model=args.model,
            effort=args.effort,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
        )
        report = write_report(run, args.output_dir)
    except QualificationError as exc:
        print(f"qualification failed: {exc}", file=sys.stderr)
        return 1

    usage = run.usage
    print(f"model:      {run.model} (requested {run.requested_model})")
    print(f"effort:     {run.effort}")
    print(f"elapsed:    {run.elapsed_seconds:.1f}s")
    print(
        "tokens:     "
        f"prompt={usage.get('prompt_tokens', 0)} "
        f"completion={usage.get('completion_tokens', 0)} "
        f"total={usage.get('total_tokens', 0)}"
    )
    print(f"reasoning:  {len(run.reasoning):,} chars")
    print(f"report:     {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
