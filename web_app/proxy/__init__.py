from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Blueprint, Response, render_template, request, url_for
from requests.adapters import HTTPAdapter

from web_app.config import ConfigManager
from web_app.helpers import register_app_name, require_admin_blueprint
from web_app.logging_utils import log_event
from web_app.web_targets import (
    ResolvedWebTarget,
    TargetValidationError,
    resolve_web_target,
)


proxy_api = Blueprint(
    "proxy",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/proxy",
)


require_admin_blueprint(proxy_api)
register_app_name(proxy_api, "Proxy")


class ProxyPolicyError(ValueError):
    """The requested resource violates the public-content proxy policy."""


@dataclass(frozen=True)
class FetchedResponse:
    url: str
    hostname: str
    status_code: int
    content_type: str
    encoding: str
    body: bytes


class _PinnedTargetAdapter(HTTPAdapter):
    """Connect to a validated IP while retaining the original HTTP/TLS host."""

    def __init__(self, address: str):
        self.address = address
        super().__init__()

    def add_headers(self, request, **kwargs):
        parsed = urlparse(request.url)
        hostname = parsed.hostname or ""
        host = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        request.headers["Host"] = host

    def get_connection_with_tls_context(
        self,
        request,
        verify,
        proxies=None,
        cert=None,
    ):
        parsed = urlparse(request.url)
        _, pool_kwargs = self.build_connection_pool_key_attributes(
            request,
            verify,
            cert,
        )
        if parsed.scheme == "https":
            pool_kwargs["assert_hostname"] = parsed.hostname
            pool_kwargs["server_hostname"] = parsed.hostname
        return self.poolmanager.connection_from_host(
            self.address,
            port=parsed.port,
            scheme=parsed.scheme,
            pool_kwargs=pool_kwargs,
        )


def _validate_proxy_target(raw_url: str) -> ResolvedWebTarget:
    blocked_hosts = ConfigManager().proxy.blocked_metadata_hostnames
    try:
        target = resolve_web_target(
            raw_url,
            allow_local=False,
            blocked_hostnames=blocked_hosts,
        )
    except (TargetValidationError, ValueError) as error:
        raise ProxyPolicyError(str(error)) from error
    return target


def _request_target(target: ResolvedWebTarget):
    config = ConfigManager().proxy
    session = requests.Session()
    session.trust_env = False
    adapter = _PinnedTargetAdapter(target.addresses[target.hostname][0])
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    try:
        response = session.get(
            target.url,
            headers={"User-Agent": config.user_agent},
            timeout=config.request_timeout_s,
            allow_redirects=False,
            stream=True,
        )
    except Exception:
        session.close()
        raise
    response._nabicat_session = session
    return response


def _close_upstream(response) -> None:
    response.close()
    session = getattr(response, "_nabicat_session", None)
    if session is not None:
        session.close()


def _read_response_body(response) -> bytes:
    config = ConfigManager().proxy
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise ProxyPolicyError("Remote response has an invalid size") from error
        if declared_size > config.response_max_bytes:
            raise ProxyPolicyError("Remote response is too large")

    chunks = []
    size = 0
    for chunk in response.iter_content(config.response_read_chunk_bytes):
        if not chunk:
            continue
        size += len(chunk)
        if size > config.response_max_bytes:
            raise ProxyPolicyError("Remote response is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch_public_response(raw_url: str) -> FetchedResponse:
    config = ConfigManager().proxy
    next_url = raw_url

    for redirect_count in range(config.max_redirects + 1):
        target = _validate_proxy_target(next_url)
        response = _request_target(target)

        if response.status_code in config.redirect_status_codes:
            location = response.headers.get("Location")
            _close_upstream(response)
            if not location:
                raise ProxyPolicyError("Remote redirect is missing a destination")
            if redirect_count >= config.max_redirects:
                raise ProxyPolicyError("Remote response has too many redirects")
            next_url = urljoin(target.url, location)
            continue

        content_type = (
            response.headers.get("Content-Type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if content_type not in config.allowed_content_types:
            _close_upstream(response)
            raise ProxyPolicyError("Remote response content type is not allowed")

        encoding = response.encoding or "utf-8"
        try:
            body = _read_response_body(response)
        finally:
            _close_upstream(response)
        return FetchedResponse(
            url=target.url,
            hostname=target.hostname,
            status_code=response.status_code,
            content_type=content_type,
            encoding=encoding,
            body=body,
        )

    raise ProxyPolicyError("Remote response has too many redirects")


def _rewrite_links(content: str, base_url: str) -> str:
    """Remove active content and route safe resource URLs through the proxy."""
    soup = BeautifulSoup(content, "html.parser")

    for tag in soup.find_all(
        [
            "script",
            "style",
            "link",
            "iframe",
            "object",
            "embed",
            "applet",
            "base",
            "svg",
            "math",
            "video",
            "audio",
            "source",
        ]
    ):
        tag.decompose()
    for tag in soup.find_all("meta"):
        if tag.get("http-equiv"):
            tag.decompose()

    for tag in soup.find_all(True):
        for attribute in list(tag.attrs):
            lowered = attribute.lower()
            if lowered.startswith("on") or lowered in {
                "background",
                "ping",
                "srcdoc",
                "srcset",
                "style",
            }:
                del tag.attrs[attribute]

    for tag_name, attribute in (
        ("a", "href"),
        ("img", "src"),
        ("form", "action"),
    ):
        for tag in soup.find_all(tag_name):
            if not tag.has_attr(attribute):
                continue
            rewritten = _rewrite_url(str(tag[attribute]), base_url)
            if rewritten is None:
                del tag.attrs[attribute]
            else:
                tag[attribute] = rewritten

    return str(soup)


def _rewrite_url(candidate: str, base_url: str) -> str | None:
    value = candidate.strip()
    if not value:
        return None
    if value.startswith("#"):
        return value

    absolute_url = urljoin(base_url, value)
    if urlparse(absolute_url).scheme.lower() not in {"http", "https"}:
        return None
    return url_for("proxy.browse", url=absolute_url, _external=False)


@proxy_api.route("/")
def index():
    return render_template("proxy_index.html")


@proxy_api.route("/browse", methods=["GET", "POST"])
def browse():
    raw_url = request.args.get("url") or request.form.get("url")
    if not raw_url:
        return render_template("proxy_index.html", error="Please enter a URL")

    try:
        fetched = _fetch_public_response(raw_url)
        log_event(
            "proxy",
            "proxy.fetch_completed",
            target_host=fetched.hostname,
            upstream_status=fetched.status_code,
            bytes=len(fetched.body),
            content_type=fetched.content_type,
        )

        if fetched.content_type == "text/html":
            text = fetched.body.decode(fetched.encoding, errors="replace")
            rewritten_content = _rewrite_links(text, fetched.url)
            return render_template(
                "proxy_index.html",
                url=fetched.url,
                content=rewritten_content,
                status_code=fetched.status_code,
            )

        response = Response(
            fetched.body,
            status=fetched.status_code,
            mimetype=fetched.content_type,
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; sandbox"
        )
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    except ProxyPolicyError:
        log_event(
            "proxy",
            "proxy.fetch_rejected",
            level=logging.WARNING,
            target_host=urlparse(raw_url).hostname,
            reason="target_or_content_policy",
        )
        return render_template(
            "proxy_index.html",
            url=raw_url,
            error="The requested resource is not allowed by the proxy policy",
        ), 400
    except requests.exceptions.Timeout:
        log_event(
            "proxy",
            "proxy.fetch_failed",
            level=logging.WARNING,
            target_host=urlparse(raw_url).hostname,
            reason="timeout",
        )
        return render_template(
            "proxy_index.html",
            url=raw_url,
            error="Request timed out",
        ), 504
    except requests.exceptions.RequestException as error:
        log_event(
            "proxy",
            "proxy.fetch_failed",
            level=logging.WARNING,
            target_host=urlparse(raw_url).hostname,
            reason="connection_error",
            exc_info=error,
            error_type=type(error).__name__,
        )
        return render_template(
            "proxy_index.html",
            url=raw_url,
            error="Could not fetch the requested resource",
        ), 502
    except Exception as error:
        log_event(
            "proxy",
            "proxy.fetch_failed",
            level=logging.ERROR,
            target_host=urlparse(raw_url).hostname,
            exc_info=error,
            error_type=type(error).__name__,
        )
        return render_template(
            "proxy_index.html",
            url=raw_url,
            error="Could not fetch the requested resource",
        ), 502
