import logging
import requests
from bs4 import BeautifulSoup
from flask import Blueprint, render_template, request, Response, url_for
from urllib.parse import urljoin, urlparse

from web_app.config import ConfigManager
from web_app.helpers import register_app_name, require_admin_blueprint
from web_app.logging_utils import log_event


proxy_api = Blueprint(
    'proxy',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/proxy'
)


require_admin_blueprint(proxy_api)
register_app_name(proxy_api, 'Proxy')


def _rewrite_links(content, base_url):
    """Rewrite all links in HTML content to route through the proxy."""
    soup = BeautifulSoup(content, 'html.parser')
    
    # Rewrite <a href="..."> links
    for tag in soup.find_all('a', href=True):
        original_href = tag['href']
        rewritten = _rewrite_url(original_href, base_url)
        if rewritten:
            tag['href'] = rewritten
    
    # Rewrite <img src="..."> tags
    for tag in soup.find_all('img', src=True):
        original_src = tag['src']
        rewritten = _rewrite_url(original_src, base_url)
        if rewritten:
            tag['src'] = rewritten
    
    # Rewrite <link href="..."> tags (CSS, etc.)
    for tag in soup.find_all('link', href=True):
        original_href = tag['href']
        rewritten = _rewrite_url(original_href, base_url)
        if rewritten:
            tag['href'] = rewritten
    
    # Rewrite <script src="..."> tags
    for tag in soup.find_all('script', src=True):
        original_src = tag['src']
        rewritten = _rewrite_url(original_src, base_url)
        if rewritten:
            tag['src'] = rewritten
    
    # Rewrite <form action="..."> tags
    for tag in soup.find_all('form', action=True):
        original_action = tag['action']
        rewritten = _rewrite_url(original_action, base_url)
        if rewritten:
            tag['action'] = rewritten
    
    return str(soup)


def _rewrite_url(url, base_url):
    """
    Rewrite a URL to route through the proxy.
    Returns the rewritten URL or None if the URL shouldn't be rewritten.
    """
    if not url:
        return None
    
    # Skip javascript: and mailto: links
    if url.startswith(('javascript:', 'mailto:', 'tel:', 'data:', '#')):
        return None
    
    # Skip anchor-only links (they stay on the same page)
    if url.startswith('#'):
        return None
    
    # Resolve relative URLs against the base URL
    absolute_url = urljoin(base_url, url)
    
    # Generate the proxy URL
    return url_for('proxy.browse', url=absolute_url, _external=False)


@proxy_api.route('/')
def index():
    return render_template("proxy_index.html")


@proxy_api.route('/browse', methods=['GET', 'POST'])
def browse():
    url = request.args.get('url') or request.form.get('url')
    if not url:
        return render_template("proxy_index.html", error="Please enter a URL")

    # Ensure URL has a scheme
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        # Make the request to the target URL
        proxy_cfg = ConfigManager().proxy
        headers = {'User-Agent': proxy_cfg.user_agent}

        resp = requests.get(url, headers=headers, timeout=proxy_cfg.request_timeout_s, allow_redirects=True)
        log_event(
            "proxy",
            "proxy.fetch_completed",
            target_host=urlparse(resp.url).hostname,
            upstream_status=resp.status_code,
        )

        # Get content type
        content_type = resp.headers.get('Content-Type', 'text/html')

        # For HTML content, render in the proxy frame
        if 'text/html' in content_type:
            # Rewrite links in the content to route through proxy
            rewritten_content = _rewrite_links(resp.text, url)
            return render_template(
                "proxy_index.html",
                url=url,
                content=rewritten_content,
                status_code=resp.status_code
            )
        else:
            # For non-HTML content, pass through directly
            excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
            headers = [(name, value) for name, value in resp.raw.headers.items()
                      if name.lower() not in excluded_headers]
            return Response(resp.content, resp.status_code, headers)

    except requests.exceptions.Timeout:
        log_event(
            "proxy",
            "proxy.fetch_failed",
            level=logging.WARNING,
            target_host=urlparse(url).hostname,
            reason="timeout",
        )
        return render_template("proxy_index.html", url=url, error="Request timed out")
    except requests.exceptions.ConnectionError:
        log_event(
            "proxy",
            "proxy.fetch_failed",
            level=logging.WARNING,
            target_host=urlparse(url).hostname,
            reason="connection_error",
        )
        return render_template("proxy_index.html", url=url, error="Could not connect to the URL")
    except requests.exceptions.InvalidURL:
        log_event(
            "proxy",
            "proxy.fetch_failed",
            level=logging.WARNING,
            reason="invalid_url",
        )
        return render_template("proxy_index.html", url=url, error="Invalid URL format")
    except Exception as e:
        log_event(
            "proxy",
            "proxy.fetch_failed",
            level=logging.ERROR,
            target_host=urlparse(url).hostname,
            exc_info=e,
            error_type=type(e).__name__,
        )
        return render_template("proxy_index.html", url=url, error=f"Error: {str(e)}")
