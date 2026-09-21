"""Deterministic guards for untrusted text, SSRF, and secret disclosure."""

import ipaddress
import re
from hashlib import sha256
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field

from packages.domain.common import DomainModel, NonEmptyText, Sha256Digest

InjectionSignal = Literal[
    "ignore_instructions",
    "role_override",
    "secret_request",
    "tool_override",
]

_SIGNAL_PATTERNS: tuple[tuple[InjectionSignal, re.Pattern[str]], ...] = (
    (
        "ignore_instructions",
        re.compile(r"(?:ignore|disregard|忘记|忽略).{0,24}(?:instruction|规则|指令)", re.I),
    ),
    (
        "role_override",
        re.compile(r"(?:you are now|act as|system prompt|你现在是|扮演)", re.I),
    ),
    (
        "secret_request",
        re.compile(r"(?:api[ _-]?key|access[ _-]?token|password|密钥|令牌)", re.I),
    ),
    (
        "tool_override",
        re.compile(r"(?:call|invoke|execute|调用|执行).{0,32}(?:tool|function|工具)", re.I),
    ),
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\b(?:sk|ak)-[A-Za-z0-9_-]{8,}\b"),
)


class SecurityPolicyError(ValueError):
    """Input was rejected before any network or tool action occurred."""


class UntrustedContent(DomainModel):
    """External text carried as data with provenance and injection signals."""

    source: NonEmptyText
    text: Annotated[str, Field(min_length=1, max_length=20_000)]
    content_hash: Sha256Digest
    injection_signals: tuple[InjectionSignal, ...] = ()

    def as_data_block(self) -> str:
        """Delimit content so a model sees it as evidence, never as instructions."""

        return '<untrusted-data source="' + self.source + '">\n' + self.text + "\n</untrusted-data>"


def wrap_untrusted_content(source: str, text: str) -> UntrustedContent:
    """Classify common injection markers without pretending detection is authorization."""

    signals = tuple(signal for signal, pattern in _SIGNAL_PATTERNS if pattern.search(text))
    return UntrustedContent(
        source=source,
        text=text,
        content_hash="sha256:" + sha256(text.encode("utf-8")).hexdigest(),
        injection_signals=signals,
    )


def redact_secret_text(text: str) -> str:
    """Remove common credential shapes from free-form errors and audit text."""

    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


class OutboundUrlPolicy(DomainModel):
    """Exact-host HTTPS allowlist that must be applied to every redirect hop."""

    allowed_hosts: Annotated[tuple[NonEmptyText, ...], Field(min_length=1)]
    allowed_ports: tuple[int, ...] = (443,)

    def validate_url(self, url: str) -> str:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise SecurityPolicyError("outbound URL is malformed") from exc
        if parsed.scheme.casefold() != "https":
            raise SecurityPolicyError("outbound URL must use HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise SecurityPolicyError("outbound URL cannot contain user information")
        hostname = (parsed.hostname or "").rstrip(".").casefold()
        if not hostname or hostname not in {item.casefold() for item in self.allowed_hosts}:
            raise SecurityPolicyError("outbound host is not allowlisted")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise SecurityPolicyError("direct IP destinations are not allowed")
        effective_port = port or 443
        if effective_port not in self.allowed_ports:
            raise SecurityPolicyError("outbound port is not allowlisted")
        return url

    def validate_redirect(self, previous_url: str, next_url: str) -> str:
        self.validate_url(previous_url)
        return self.validate_url(next_url)
