"""Command-line entry point for the registration pipeline."""

from __future__ import annotations

import argparse
import sys

from .flow import RegistrationError, register_account
from .store import DEFAULT_PATH, save_account


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="th2api-register",
        description=(
            "Register Token Harbor accounts with DuckMail inboxes and mint "
            "API keys."
        ),
    )
    parser.add_argument("--count", type=int, default=1, help="accounts to create")
    parser.add_argument("--label", default="th2api", help="API key label")
    parser.add_argument("--email", help="reuse an existing address")
    parser.add_argument("--password", help="password for --email")
    parser.add_argument("--domain", help="DuckMail domain to use")
    parser.add_argument(
        "--proxy",
        help="HTTP proxy for both services, e.g. http://user:pass@host:3129",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="seconds to wait for each verification email",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_PATH),
        help=f"account record path (default: {DEFAULT_PATH})",
    )
    args = parser.parse_args(argv)

    if args.email and args.count > 1:
        parser.error("--email cannot be combined with --count > 1")

    for index in range(args.count):
        try:
            account = register_account(
                email=args.email,
                password=args.password,
                label=args.label,
                domain=args.domain,
                proxy=args.proxy,
                verification_timeout=args.timeout,
            )
        except RegistrationError as exc:
            print(f"registration failed: {exc}", file=sys.stderr)
            return 1

        path = save_account(account, args.output)
        print(f"[{index + 1}/{args.count}] {account.email}")
        print(f"  api key: {account.api_key}")
        print(f"  record:  {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
