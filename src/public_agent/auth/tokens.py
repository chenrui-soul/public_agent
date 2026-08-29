from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from pydantic import SecretStr

from public_agent.auth.base import AuthenticationError

_TOKEN_PATTERN = re.compile(
    r"^public_agent_([A-Za-z0-9_-]{12})\.([A-Za-z0-9_-]{43})$"
)


@dataclass(frozen=True, slots=True)
class TokenMaterial:
    token: SecretStr
    prefix: str
    digest: bytes


class APITokenCodec:
    def __init__(self, pepper: SecretStr | str) -> None:
        value = pepper.get_secret_value() if isinstance(pepper, SecretStr) else pepper
        if not value:
            raise ValueError("API token pepper must not be blank")
        self._key = hashlib.sha256(value.encode("utf-8")).digest()

    def issue(self) -> TokenMaterial:
        prefix = secrets.token_urlsafe(9)
        secret = secrets.token_urlsafe(32)
        plaintext = f"public_agent_{prefix}.{secret}"
        return TokenMaterial(
            token=SecretStr(plaintext),
            prefix=prefix,
            digest=self._digest(prefix=prefix, secret=secret),
        )

    def parse(self, token: SecretStr | str) -> tuple[str, bytes]:
        plaintext = token.get_secret_value() if isinstance(token, SecretStr) else token
        match = _TOKEN_PATTERN.fullmatch(plaintext)
        if match is None:
            raise AuthenticationError("authentication required")
        prefix, secret = match.groups()
        return prefix, self._digest(prefix=prefix, secret=secret)

    @staticmethod
    def matches(candidate: bytes, stored: bytes) -> bool:
        return hmac.compare_digest(candidate, stored)

    def _digest(self, *, prefix: str, secret: str) -> bytes:
        return hmac.new(
            self._key,
            f"{prefix}.{secret}".encode("ascii"),
            hashlib.sha256,
        ).digest()
