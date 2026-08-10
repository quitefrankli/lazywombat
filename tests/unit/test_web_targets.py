from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from web_app.web_targets import TargetValidationError, resolve_web_target


def _addrinfo(*addresses: str):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, 443),
        )
        for address in addresses
    ]


def test_web_target_is_normalized_and_every_allowed_host_is_dns_pinned():
    answers = {
        "example.com": _addrinfo("93.184.216.34"),
        "cdn.example.com": _addrinfo("1.1.1.1", "8.8.8.8"),
    }

    with patch(
        "web_app.web_targets.socket.getaddrinfo",
        side_effect=lambda host, *_args, **_kwargs: answers[host],
    ):
        target = resolve_web_target(
            "EXAMPLE.com/check?mode=full#secret",
            additional_hosts=("cdn.example.com",),
        )

    assert target.url == "https://example.com/check?mode=full"
    assert target.allowed_hosts == frozenset({"example.com", "cdn.example.com"})
    assert target.addresses == {
        "example.com": ("93.184.216.34",),
        "cdn.example.com": ("1.1.1.1", "8.8.8.8"),
    }


@pytest.mark.parametrize(
    "url,additional_hosts",
    [
        ("https://user:password@example.com/", ()),
        ("https://example.com/", ("127.0.0.1",)),
    ],
)
def test_web_target_rejects_credentials_and_any_non_public_allowed_host(
    url,
    additional_hosts,
):
    with (
        patch(
            "web_app.web_targets.socket.getaddrinfo",
            return_value=_addrinfo("93.184.216.34"),
        ),
        pytest.raises(TargetValidationError),
    ):
        resolve_web_target(url, additional_hosts=additional_hosts)


def test_public_policy_rejects_non_global_cgnat_addresses():
    with pytest.raises(TargetValidationError, match="public"):
        resolve_web_target("http://100.64.0.1/")


def test_local_policy_allows_private_qa_but_never_unsafe_special_addresses():
    assert resolve_web_target(
        "127.0.0.1:8000/check",
        allow_local=True,
        additional_hosts=("10.0.0.2",),
    ).allowed_hosts == frozenset({"127.0.0.1", "10.0.0.2"})

    for address in ("0.0.0.0", "169.254.169.254", "224.0.0.1", "240.0.0.1"):
        with pytest.raises(TargetValidationError):
            resolve_web_target(f"http://{address}/", allow_local=True)


@pytest.mark.parametrize("allow_local", [False, True])
def test_web_target_rejects_deprecated_ipv6_site_local_addresses(allow_local):
    with pytest.raises(TargetValidationError):
        resolve_web_target("http://[fec0::1]/", allow_local=allow_local)
