from __future__ import annotations

import ipaddress
import socket
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlparse, urlunparse


class TargetValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedWebTarget:
    url: str
    allowed_hosts: frozenset[str]
    addresses: Mapping[str, tuple[str, ...]]

    @property
    def hostname(self) -> str:
        return urlparse(self.url).hostname or ""


def _is_safe_local_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(
        (address.is_private or address.is_loopback)
        and not _is_site_local(address)
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _is_site_local(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return isinstance(address, ipaddress.IPv6Address) and address.is_site_local


def _is_unsafe_special_ip(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return bool(
        _is_site_local(address)
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _looks_local(hostname: str) -> bool:
    if hostname in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return _is_safe_local_ip(hostname)
    except ValueError:
        return False


def _resolve_host(
    hostname: str, port: int | None, *, allow_local: bool
) -> tuple[str, ...]:
    try:
        addresses = (str(ipaddress.ip_address(hostname)),)
    except ValueError:
        try:
            infos = socket.getaddrinfo(
                hostname,
                port or 443,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as error:
            raise TargetValidationError("Could not resolve target host") from error
        addresses = tuple(sorted({info[4][0] for info in infos}))
    if not addresses:
        raise TargetValidationError("Could not resolve target host")
    parsed_addresses = tuple(ipaddress.ip_address(address) for address in addresses)
    if allow_local:
        if any(
            _is_unsafe_special_ip(address)
            or not (address.is_global or _is_safe_local_ip(str(address)))
            for address in parsed_addresses
        ):
            raise TargetValidationError("Target resolves to an unsafe special address")
    elif hostname in {"localhost", "localhost.localdomain"} or any(
        _is_unsafe_special_ip(address) or not address.is_global
        for address in parsed_addresses
    ):
        raise TargetValidationError("Target must resolve only to public addresses")
    return addresses


def _additional_hostname(raw_host: str) -> str:
    raw = (raw_host or "").strip()
    parsed = urlparse(f"//{raw}")
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise TargetValidationError("Additional domains must be hostnames")
    return parsed.hostname.rstrip(".").lower()


def resolve_web_target(
    raw_url: str,
    *,
    additional_hosts: Collection[str] = (),
    allow_local: bool = False,
    blocked_hostnames: Collection[str] = (),
) -> ResolvedWebTarget:
    """Normalize a web target and pin every allowed hostname to safe DNS results."""
    raw = (raw_url or "").strip()
    if "://" not in raw:
        scheme_host = raw.split("/", 1)[0]
        bare_host = urlparse(f"//{scheme_host}").hostname or scheme_host
        scheme = "http" if allow_local and _looks_local(bare_host.lower()) else "https"
        raw = f"{scheme}://{raw}"
    try:
        parsed = urlparse(raw)
        parsed_port = parsed.port
    except ValueError as error:
        raise TargetValidationError("URL must include a valid host") from error
    if parsed.scheme.lower() not in {"http", "https"}:
        raise TargetValidationError("URL must start with http:// or https://")
    if not parsed.hostname:
        raise TargetValidationError("URL must include a valid host")
    if parsed.username is not None or parsed.password is not None:
        raise TargetValidationError("URLs containing credentials are not allowed")

    hostname = parsed.hostname.rstrip(".").lower()
    blocked = {candidate.rstrip(".").lower() for candidate in blocked_hostnames}
    hostnames = {hostname}
    hostnames.update(_additional_hostname(candidate) for candidate in additional_hosts)
    if hostnames & blocked:
        raise TargetValidationError("Cloud metadata targets are not allowed")

    resolved = {
        candidate: _resolve_host(
            candidate,
            parsed_port if candidate == hostname else None,
            allow_local=allow_local,
        )
        for candidate in sorted(hostnames)
    }
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    if parsed_port is not None:
        host_for_netloc = f"{host_for_netloc}:{parsed_port}"
    normalized = urlunparse(
        (
            parsed.scheme.lower(),
            host_for_netloc,
            parsed.path or "/",
            "",
            parsed.query,
            "",
        )
    )
    return ResolvedWebTarget(
        url=normalized,
        allowed_hosts=frozenset(hostnames),
        addresses=MappingProxyType(resolved),
    )
