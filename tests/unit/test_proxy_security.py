import socket
from unittest.mock import Mock, patch

import pytest
from flask import Flask

from web_app.config import ConfigManager
from web_app.proxy import (
    ProxyPolicyError,
    _fetch_public_response,
    proxy_api,
    _request_target,
    _rewrite_links,
    _validate_proxy_target,
)
from web_app.web_targets import ResolvedWebTarget


def _addrinfo(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://224.0.0.1/",
        "http://240.0.0.1/",
        "http://metadata.google.internal/",
    ],
)
def test_proxy_rejects_non_public_and_metadata_targets(url):
    with pytest.raises(ProxyPolicyError):
        _validate_proxy_target(url)


def test_proxy_rejects_hostname_resolving_to_private_address():
    with patch(
        "web_app.web_targets.socket.getaddrinfo",
        return_value=_addrinfo("127.0.0.1"),
    ):
        with pytest.raises(ProxyPolicyError):
            _validate_proxy_target("https://attacker.example/")


def test_proxy_pins_request_to_the_validated_address():
    response = Mock()
    session = Mock()
    session.get.return_value = response
    target = ResolvedWebTarget(
        url="https://example.com/",
        allowed_hosts=frozenset({"example.com"}),
        addresses={"example.com": ("93.184.216.34",)},
    )

    with patch("web_app.proxy.requests.Session", return_value=session):
        assert _request_target(target) is response

    mounted_adapter = session.mount.call_args_list[0].args[1]
    assert mounted_adapter.address == "93.184.216.34"
    assert session.trust_env is False
    session.get.assert_called_once_with(
        target.url,
        headers={"User-Agent": ConfigManager().proxy.user_agent},
        timeout=ConfigManager().proxy.request_timeout_s,
        allow_redirects=False,
        stream=True,
    )


def test_proxy_validates_every_redirect_target():
    first = Mock(
        status_code=302,
        headers={"Location": "http://169.254.169.254/latest/meta-data/"},
    )
    first.close = Mock()

    with patch("web_app.proxy._request_target", return_value=first) as request_target:
        with patch(
            "web_app.web_targets.socket.getaddrinfo",
            return_value=_addrinfo("93.184.216.34"),
        ):
            with pytest.raises(ProxyPolicyError):
                _fetch_public_response("https://example.com/start")

    request_target.assert_called_once()
    first.close.assert_called_once()


def test_proxy_rejects_declared_and_streamed_oversized_responses():
    cfg = ConfigManager().proxy
    declared = Mock(
        status_code=200,
        headers={
            "Content-Type": "text/html",
            "Content-Length": str(cfg.response_max_bytes + 1),
        },
    )
    declared.close = Mock()

    with patch("web_app.proxy._validate_proxy_target") as validate:
        validate.return_value = Mock(url="https://example.com/", hostname="example.com")
        with patch("web_app.proxy._request_target", return_value=declared):
            with pytest.raises(ProxyPolicyError, match="too large"):
                _fetch_public_response("https://example.com/")

    streamed = Mock(
        status_code=200,
        headers={"Content-Type": "text/html"},
    )
    streamed.iter_content.return_value = [
        b"x" * cfg.response_read_chunk_bytes
        for _ in range((cfg.response_max_bytes // cfg.response_read_chunk_bytes) + 1)
    ]
    streamed.close = Mock()

    with patch("web_app.proxy._validate_proxy_target") as validate:
        validate.return_value = Mock(url="https://example.com/", hostname="example.com")
        with patch("web_app.proxy._request_target", return_value=streamed):
            with pytest.raises(ProxyPolicyError, match="too large"):
                _fetch_public_response("https://example.com/")


@pytest.mark.parametrize(
    "content_type",
    [
        "application/octet-stream",
        "application/javascript",
        "image/svg+xml",
        "text/css",
    ],
)
def test_proxy_rejects_disallowed_content_types(content_type):
    response = Mock(
        status_code=200,
        headers={"Content-Type": content_type},
    )
    response.close = Mock()

    with patch("web_app.proxy._validate_proxy_target") as validate:
        validate.return_value = Mock(url="https://example.com/", hostname="example.com")
        with patch("web_app.proxy._request_target", return_value=response):
            with pytest.raises(ProxyPolicyError, match="content type"):
                _fetch_public_response("https://example.com/")


def test_remote_html_removes_active_and_hostile_content():
    html = """
        <script src="/payload.js"></script>
        <style>body { background: url(https://attacker.example/leak) }</style>
        <iframe src="https://attacker.example/frame"></iframe>
        <img src="/safe.png" onerror="steal()" style="background:url(https://attacker.example)">
        <a href="javascript:steal()" onclick="steal()">bad</a>
        <a href="/next">safe</a>
    """

    test_app = Flask(__name__)
    test_app.register_blueprint(proxy_api)
    with test_app.test_request_context():
        rewritten = _rewrite_links(html, "https://example.com/start")

    lowered = rewritten.lower()
    assert "<script" not in lowered
    assert "<style" not in lowered
    assert "<iframe" not in lowered
    assert "onerror" not in lowered
    assert "onclick" not in lowered
    assert "javascript:" not in lowered
    assert 'style=' not in lowered
    assert "/proxy/browse?url=https://example.com/next" in rewritten
    assert "/proxy/browse?url=https://example.com/safe.png" in rewritten


def test_proxy_iframe_has_unique_origin_and_scripts_disabled():
    template = (
        ConfigManager().project_dir
        / "web_app"
        / "proxy"
        / "templates"
        / "proxy_index.html"
    ).read_text()

    assert "sandbox>" in template
    assert "allow-same-origin" not in template
    assert "allow-scripts" not in template
