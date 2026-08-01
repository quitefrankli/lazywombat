from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from web_app.config import ConfigManager


class TargetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    hostname: str
    addresses: tuple[str, ...] = ()


def _is_blocked_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _looks_local(hostname: str) -> bool:
    if hostname in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return _is_blocked_ip(hostname)
    except ValueError:
        return False


def _resolved_ips(hostname: str, port: int | None) -> set[str]:
    try:
        infos = socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise TargetValidationError("Could not resolve target host") from e
    return {info[4][0] for info in infos}


def validate_public_web_url(
    raw_url: str,
    *,
    allow_local: bool | None = None,
) -> ValidatedTarget:
    raw = (raw_url or "").strip()
    if allow_local is None:
        allow_local = ConfigManager().sentinel.allow_local_targets
    if "://" not in raw:
        # Local development servers usually speak plain HTTP, so when local targets are
        # explicitly enabled default a
        # scheme-less local target to http:// rather than https:// (which fails
        # with ERR_SSL_PROTOCOL_ERROR against a non-TLS server).
        scheme_host = raw.split("/", 1)[0]
        bare_host = urlparse(f"//{scheme_host}").hostname or scheme_host
        default_scheme = "http" if (allow_local and _looks_local(bare_host.rstrip(".").lower())) else "https"
        url = f"{default_scheme}://{raw}"
    else:
        url = raw
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise TargetValidationError("URL must start with http:// or https://")
    if not parsed.hostname:
        raise TargetValidationError("URL must include a valid host")
    hostname = parsed.hostname.rstrip(".").lower()
    try:
        addresses = (str(ipaddress.ip_address(hostname)),)
    except ValueError:
        addresses = tuple(sorted(_resolved_ips(hostname, parsed.port)))

    # Permit local/private targets only when the explicit Sentinel capability
    # is enabled (for example, by the foreground QA CLI).
    if not allow_local:
        if hostname in {"localhost", "localhost.localdomain"}:
            raise TargetValidationError("Local targets are not allowed")

        for address in addresses:
            if _is_blocked_ip(address):
                raise TargetValidationError(
                    "Target resolves to a private or local address"
                )

    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    userinfo = f"{parsed.netloc.rsplit('@', 1)[0]}@" if "@" in parsed.netloc else ""
    normalized_netloc = f"{userinfo}{host_for_netloc}"
    if parsed.port:
        normalized_netloc = f"{normalized_netloc}:{parsed.port}"
    normalized = urlunparse(
        (
            parsed.scheme,
            normalized_netloc,
            parsed.path or "/",
            "",
            parsed.query,
            "",
        )
    )
    return ValidatedTarget(
        url=normalized,
        hostname=hostname,
        addresses=addresses,
    )
