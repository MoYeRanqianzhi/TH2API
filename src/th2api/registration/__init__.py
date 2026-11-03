"""Account registration for Token Harbor."""

from .duckmail import DuckMailClient, DuckMailError, Message
from .flow import RegisteredAccount, RegistrationError, register_account
from .providers import (
    DomainForwardProvider,
    DuckMailProvider,
    EmailProvider,
    EmailProviderError,
    Mailbox,
)
from .store import save_account
from .tokenharbor import (
    AccountCapError,
    ApiKey,
    ApiKeyError,
    CaptchaRequiredError,
    EmailVerificationError,
    RegionBlockedError,
    SignupError,
    TokenHarborClient,
    TokenHarborError,
)

__all__ = [
    "AccountCapError",
    "ApiKey",
    "ApiKeyError",
    "CaptchaRequiredError",
    "DomainForwardProvider",
    "DuckMailClient",
    "DuckMailError",
    "DuckMailProvider",
    "EmailProvider",
    "EmailProviderError",
    "EmailVerificationError",
    "Mailbox",
    "Message",
    "RegionBlockedError",
    "RegisteredAccount",
    "RegistrationError",
    "SignupError",
    "TokenHarborClient",
    "TokenHarborError",
    "register_account",
    "save_account",
]
