"""Browser coverage for service-worker cache isolation across logins."""

import time

import pytest

pytest.importorskip("playwright")


def _register(page, test_server, username):
    page.goto(f"{test_server}/account/login")
    page.fill("#username", username)
    page.fill("#password", "testpass123")
    page.click("button:has-text('Create Account')")
    page.wait_for_url(f"{test_server}/")
    page.wait_for_load_state("networkidle")


def test_user_cannot_receive_previous_users_cached_json(page, test_server):
    first_user = f"cache_a_{int(time.time() * 1000)}"
    second_user = f"cache_b_{int(time.time() * 1000)}"
    secret_filename = f"{first_user}-secret.txt"

    _register(page, test_server, first_user)
    assert page.evaluate(
        """async () => Boolean((await navigator.serviceWorker.ready).active)"""
    )
    first_result = page.evaluate(
        """async (filename) => {
            const body = new FormData();
            body.append("file", new File(["private"], filename, {type: "text/plain"}));
            const upload = await fetch("/file_store/upload", {
                method: "POST",
                headers: {"X-Requested-With": "XMLHttpRequest"},
                body,
            });
            if (!upload.ok) throw new Error(`upload failed: ${upload.status}`);
            return fetch("/file_store/files_list").then((response) => response.json());
        }""",
        secret_filename,
    )
    assert secret_filename in str(first_result)
    page.evaluate(
        """async (secret) => {
            const cache = await caches.open("nabicat-cache-user-scoped-test");
            await cache.put(
                "/file_store/files_list",
                new Response(JSON.stringify({files: [secret]})),
            );
        }""",
        secret_filename,
    )

    with page.expect_navigation(wait_until="networkidle"):
        page.click("a:has-text('Logout')")
    page.wait_for_url(f"{test_server}/")
    _register(page, test_server, second_user)
    assert "nabicat-cache-user-scoped-test" not in page.evaluate(
        """() => caches.keys()"""
    )

    second_result = page.evaluate(
        """() => fetch("/file_store/files_list").then((response) => response.json())"""
    )
    assert secret_filename not in str(second_result)

    page.evaluate(
        """async (secret) => {
            const cache = await caches.open("nabicat-cache-hostile-test");
            await cache.put(
                "/file_store/files_list",
                new Response(JSON.stringify({files: [secret]}), {
                    headers: {"Content-Type": "application/json"},
                }),
            );
        }""",
        secret_filename,
    )
    page.context.set_offline(True)
    try:
        offline_result = page.evaluate(
            """async () => {
                try {
                    return await fetch("/file_store/files_list")
                        .then((response) => response.text());
                } catch (error) {
                    return "network-unavailable";
                }
            }"""
        )
    finally:
        page.context.set_offline(False)
    assert secret_filename not in offline_result
    page.evaluate("""() => caches.delete("nabicat-cache-hostile-test")""")

    cache_keys = page.evaluate(
        """async () => {
            const names = await caches.keys();
            const keys = [];
            for (const name of names) {
                for (const request of await (await caches.open(name)).keys()) {
                    keys.push(request.url);
                }
            }
            return keys;
        }"""
    )
    assert not any("/file_store/files_list" in key for key in cache_keys)
