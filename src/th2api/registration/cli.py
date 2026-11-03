"""Command-line entry point for the registration pipeline."""

from __future__ import annotations

import argparse
import sys

from curl_cffi.requests.exceptions import RequestException

from .flow import RegistrationError, register_account
from .providers import (
    DomainForwardProvider,
    DuckMailProvider,
    EmailProviderError,
)
from .store import DEFAULT_PATH, save_account


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="th2api-register",
        description=(
            "Register Token Harbor accounts through DuckMail inboxes or a "
            "forwarded custom domain, and mint API keys."
        ),
    )
    parser.add_argument("--count", type=int, default=1, help="accounts to create")
    parser.add_argument("--label", default="th2api", help="API key label")
    parser.add_argument(
        "--email-provider",
        choices=("duckmail", "domain-forward"),
        default="duckmail",
        help="mail source (default: duckmail)",
    )
    parser.add_argument("--email", help="reuse an existing DuckMail address")
    parser.add_argument("--password", help="password for --email")
    parser.add_argument(
        "--domain",
        help="DuckMail domain, or the custom domain for domain-forward",
    )
    parser.add_argument(
        "--forward-to",
        help="DuckMail inbox that receives the custom domain's forwarded mail",
    )
    parser.add_argument(
        "--pin",
        help="DuckMail password or access token for --forward-to",
    )
    parser.add_argument(
        "--proxy",
        help=(
            "proxy for every request (DuckMail + Token Harbor), "
            "e.g. http://user:pass@host:3129"
        ),
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
    if args.email and args.email_provider != "duckmail":
        parser.error("--email is only supported with the duckmail provider")

    try:
        provider = _build_provider(parser, args)
    except EmailProviderError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    for index in range(args.count):
        try:
            account = register_account(
                email=args.email,
                password=args.password,
                label=args.label,
                proxy=args.proxy,
                verification_timeout=args.timeout,
                mail_provider=provider,
            )
        except RegistrationError as exc:
            print(f"registration failed: {exc}", file=sys.stderr)
            return 1
        except RequestException as exc:
            print(f"network/proxy error: {exc}", file=sys.stderr)
            return 1

        path = save_account(account, args.output)
        print(f"[{index + 1}/{args.count}] {account.email}")
        print(f"  provider: {account.email_provider}")
        print(f"  api key:  {account.api_key}")
        print(f"  record:   {path}")

    return 0


def _build_provider(parser: argparse.ArgumentParser, args):
    if args.email_provider == "domain-forward":
        missing = [
            flag
            for flag, value in (
                ("--domain", args.domain),
                ("--forward-to", args.forward_to),
                ("--pin", args.pin),
            )
            if not value
        ]
        if missing:
            parser.error(
                "domain-forward requires " + ", ".join(missing)
            )
        return DomainForwardProvider(
            args.domain,
            args.forward_to,
            args.pin,
            proxy=args.proxy,
        )
    return DuckMailProvider(proxy=args.proxy, domain=args.domain)


if __name__ == "__main__":
    raise SystemExit(main())
