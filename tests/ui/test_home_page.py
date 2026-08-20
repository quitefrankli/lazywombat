"""
UI tests for the home page using Playwright.
"""
import time
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


def test_home_page_loads(logged_in_page, test_server):
    """Test that the home page loads successfully."""
    expect(logged_in_page).to_have_title("NabiCat")
    expect(logged_in_page.locator("img.home-logo[alt='NabiCat']")).to_be_visible()


def test_app_grid_visible(logged_in_page, test_server):
    """Test that the app grid is displayed with all expected apps for admin."""
    expected_apps = [
        "Loft",
        "Todoist",
        "Metrics",
        "Tubio",
        "File Store",
        "Proxy",
        "Dev",
    ]

    for app_name in expected_apps:
        app_card = logged_in_page.locator("text=" + app_name)
        expect(app_card).to_be_visible()


def test_all_app_cards_clickable(logged_in_page, test_server):
    """Test that all app cards are clickable links for admin."""
    apps = [
        ("Loft", "/loft/"),
        ("Todoist", "/todoist"),
        ("Metrics", "/metrics"),
        ("Tubio", "/tubio"),
        ("File Store", "/file_store"),
        ("Proxy", "/proxy"),
        ("Dev", "/dev"),
    ]

    for app_name, path in apps:
        link = logged_in_page.locator(f"a:has-text('{app_name}')")
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", path)


def test_crosswords_visible_for_admin(logged_in_page, test_server):
    """Test that Crosswords is visible for admin user."""
    expect(logged_in_page.locator("text=Crosswords")).to_be_visible()


def test_admin_section_visible_for_admin(logged_in_page):
    expect(
        logged_in_page.locator(".section-label", has_text="Admin")
    ).to_be_visible()


def test_loft_uses_custom_icon(logged_in_page, test_server):
    expect(logged_in_page.locator(".app-icon-loft img.app-icon-image")).to_be_visible()


def test_version_badge_displayed(logged_in_page):
    """Test that the version/build badge is displayed."""
    expect(logged_in_page.locator("code")).to_be_visible()


def test_cache_controls_display_after_service_worker_ready(logged_in_page):
    """Test that cache usage and clear controls appear on the landing page."""
    expect(logged_in_page.locator("#cacheInfoCard")).to_be_visible(timeout=10000)
    expect(logged_in_page.locator("#cacheUsageText")).not_to_have_text("—")
    expect(logged_in_page.locator("#clearCacheBtn")).to_be_visible()


def test_navbar_present(logged_in_page):
    """Test that the navigation bar is present with key elements."""
    expect(logged_in_page.locator("nav")).to_be_visible()
    expect(logged_in_page.locator("a[href='/']", has_text="NabiCat")).to_be_visible()
    expect(logged_in_page.locator("button:has-text('Actions')")).to_be_visible()


def test_logout_button_present(logged_in_page):
    """Test that logout button is present (or login if not logged in)."""
    expect(logged_in_page.locator("a:has-text('Logout')")).to_be_visible()


def test_admin_section_hidden_for_non_admin(page, test_server):
    """Test that non-admin users do not receive the admin dashboard section."""
    username = f"ui_non_admin_{int(time.time() * 1000)}"
    password = "testpass123"

    page.goto(f"{test_server}/account/login")
    page.wait_for_load_state("networkidle")
    page.fill("input#username", username)
    page.fill("input#password", password)
    page.click("button:has-text('Create Account')")
    page.wait_for_url(f"{test_server}/", timeout=10000)
    page.wait_for_load_state("networkidle")

    # Regular authenticated and public apps remain available.
    for app_name in [
        "Metrics",
        "Tubio",
        "File Store",
        "Todoist",
        "Crosswords",
    ]:
        expect(page.locator("text=" + app_name)).to_be_visible()

    expect(page.locator(".section-label", has_text="Admin")).to_have_count(0)
    for app_name in ["Proxy", "Dev"]:
        expect(page.locator(".app-title", has_text=app_name)).to_have_count(0)
