import logging
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
from nabicat_app_sdk import AccessLevel

from web_app.config import ConfigManager


def test_nginx_forwards_original_https_scheme():
    nginx_config = Path("nabicat.conf").read_text()

    assert nginx_config.count("proxy_set_header X-Forwarded-Proto $scheme;") == 2


def test_synchronous_app_requests_have_aligned_proxy_and_worker_timeouts():
    nginx_config = Path("nabicat.conf").read_text()
    config = ConfigManager()

    assert config.gunicorn_request_timeout_s == 720
    assert config.gunicorn_graceful_timeout_s == 720
    for directive in (
        "proxy_connect_timeout 720;",
        "proxy_send_timeout 720;",
        "proxy_read_timeout 720;",
    ):
        assert nginx_config.count(directive) == 2


def test_v2_app_dependencies_use_immutable_git_revisions():
    requirements = Path("requirements.txt").read_text()

    for distribution, repository in (
        ("nabicat-app-sdk", "nabicat-app-sdk"),
        ("nabicat-sentinel", "nabicat-sentinel"),
    ):
        pattern = (
            rf"^{re.escape(distribution)} @ git\+https://github\.com/quitefrankli/"
            rf"{re.escape(repository)}\.git@[0-9a-f]{{40}}$"
        )
        assert re.search(pattern, requirements, re.MULTILINE)


def test_ci_excludes_ffmpeg_tests_without_installing_ffmpeg():
    workflow = Path(".github/workflows/cicd.yml").read_text()

    assert 'pytest -q -m "not ffmpeg"' in workflow
    assert "apt-get install --yes redis-server\n" in workflow


def test_health_reports_commit_loaded_by_worker(client, app):
    import web_app.__main__  # noqa: F401

    app.config["DEPLOY_COMMIT"] = "candidate-sha"
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.get_json()["commit"] == "candidate-sha"
    assert isinstance(response.get_json()["pid"], int)


def test_installed_app_metadata_drives_health_home_static_cache_and_versions(
    client,
    app,
):
    import web_app.__main__  # noqa: F401
    from web_app.app import _add_static_version

    class Registry:
        def health(self):
            return ({"app_id": "alpha", "version": "1.2.3", "status": "loaded"},)

        def navigation(self):
            return (
                SimpleNamespace(
                    access=AccessLevel.PUBLIC,
                    app_id="alpha",
                    endpoint="home",
                    icon="bi-eye-fill",
                    label="Alpha",
                ),
            )

        def static_prefixes(self):
            return ("/alpha/static/",)

        def static_version(self, endpoint):
            return "1.2.3" if endpoint == "alpha.static" else None

    previous = app.extensions.get("nabicat_apps")
    app.extensions["nabicat_apps"] = Registry()
    try:
        assert client.get("/api/health").get_json()["apps"] == [
            {"app_id": "alpha", "version": "1.2.3", "status": "loaded"}
        ]
        home = client.get("/").text
        assert 'data-installed-app="alpha"' in home
        assert "Alpha" in home
        service_worker = client.get("/service-worker.js").text
        assert '"/alpha/static/"' in service_worker
        values = {}
        _add_static_version("alpha.static", values)
        assert values == {"v": "1.2.3"}
    finally:
        if previous is None:
            app.extensions.pop("nabicat_apps", None)
        else:
            app.extensions["nabicat_apps"] = previous


def test_web_launcher_keeps_minimal_option_surface():
    import web_app.__main__ as web_main

    result = CliRunner().invoke(web_main.cli_start, ["--help"])

    assert result.exit_code == 0
    for option in ("--debug", "--port", "--llm-source", "--help"):
        assert option in result.output
    for removed_option in (
        "--host",
        "--qa",
        "--qa-data-root",
        "--reload",
        "--auto-admin-login",
    ):
        assert removed_option not in result.output


def test_debug_launcher_retains_existing_runtime_behavior():
    import web_app.__main__ as web_main

    config = ConfigManager()
    previous_debug = config.debug_mode
    previous_cookie_name = web_main.app.config["SESSION_COOKIE_NAME"]
    with (
        patch.object(web_main, "configure_logging"),
        patch.object(web_main, "ensure_local_redis"),
        patch.object(web_main, "register_installed_apps"),
        patch.object(web_main.app, "run") as app_run,
    ):
        result = CliRunner().invoke(
            web_main.cli_start,
            ["--debug", "--port", "12345"],
        )
    try:
        assert result.exit_code == 0, result.output
        assert config.debug_mode is True
        assert config.flask_session_cookie_name == config.debug_session_cookie_name
        assert web_main.app.config["SESSION_COOKIE_NAME"] == "session_debug"
        app_run.assert_called_once_with(
            host=config.server_host,
            port=12345,
            debug=True,
        )
    finally:
        config.debug_mode = previous_debug
        web_main.app.config["SESSION_COOKIE_NAME"] = previous_cookie_name


def test_non_debug_launcher_uses_normal_session_cookie():
    import web_app.__main__ as web_main

    config = ConfigManager()
    previous_debug = config.debug_mode
    previous_cookie_name = web_main.app.config["SESSION_COOKIE_NAME"]
    with (
        patch.dict("os.environ", {"FLASK_SECRET_KEY": "normal-secret"}),
        patch.object(web_main, "configure_logging"),
        patch.object(web_main, "ensure_local_redis"),
        patch.object(web_main, "register_installed_apps"),
        patch.object(web_main.app, "run"),
    ):
        result = CliRunner().invoke(web_main.cli_start, ["--port", "12346"])
    try:
        assert result.exit_code == 0, result.output
        assert config.debug_mode is False
        assert config.flask_session_cookie_name == config.session_cookie_name
        assert web_main.app.config["SESSION_COOKIE_NAME"] == "session"
        assert config.debug_session_cookie_name != config.session_cookie_name
    finally:
        config.debug_mode = previous_debug
        web_main.app.config["SESSION_COOKIE_NAME"] = previous_cookie_name


def test_build_version_prefers_most_recent_tag_on_head(monkeypatch):
    from web_app import app as app_module

    calls = []

    def fake_check_output(command, **kwargs):
        calls.append(command)
        if command[1] == "rev-parse":
            return b"abc123def456789\n"
        return b"release-2026.07.31\nrelease-2026.07.29\n"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    assert app_module._load_build_version() == ("release-2026.07.31", True)
    assert "--sort=-creatordate" in calls[1]


def test_home_uses_build_version_loaded_at_server_start(client, monkeypatch):
    import web_app.__main__ as web_main

    monkeypatch.setattr(web_main, "BUILD_VERSION", "release-2026.07.29")
    monkeypatch.setattr(web_main, "BUILD_VERSION_IS_TAG", True)
    with patch("web_app.app._load_build_version") as load_build_version:
        response = client.get("/")

    assert response.status_code == 200
    assert response.data.count(b"release-2026.07.29") == 2
    assert b"abc123def456" not in response.data
    load_build_version.assert_not_called()


def test_home_build_version_falls_back_to_abbreviated_commit(client, monkeypatch):
    import web_app.__main__ as web_main

    monkeypatch.setattr(web_main, "BUILD_VERSION", "abc123def456789")
    monkeypatch.setattr(web_main, "BUILD_VERSION_IS_TAG", False)

    response = client.get("/")

    assert response.status_code == 200
    assert b"abc123def456" in response.data
    assert b"abc123d" in response.data


@patch("web_app.api.subprocess.Popen")
@patch("web_app.api.uuid.uuid4", return_value="delivery-id")
def test_update_server_starts_detached_systemd_deployment(_uuid, popen):
    from web_app.api import update_server

    update_server()

    command = popen.call_args.args[0]
    assert command[:3] == ["sudo", "systemd-run", "--quiet"]
    assert "--unit=nabicat-update-delivery-id" in command
    assert command[-2:] == ["bash", "update_server.sh"]
    popen.assert_called_once()


@patch("web_app.api.subprocess.Popen")
@patch("web_app.api.uuid.uuid4", return_value="delivery-id")
def test_update_server_pipes_patch_to_detached_systemd_deployment(_uuid, popen):
    from web_app.api import update_server

    update_server("patch contents")

    command = popen.call_args.args[0]
    assert command[:3] == ["sudo", "systemd-run", "--quiet"]
    assert "--pipe" in command
    assert command[-2:] == ["update_server.sh", "-p"]
    assert popen.call_args.kwargs["stdin"] is not None
    popen.return_value.stdin.write.assert_called_once_with(b"patch contents")
    popen.return_value.stdin.close.assert_called_once()


@patch("web_app.api.update_server")
@patch("web_app.api.parse_request", return_value={"patch": "patch contents"})
def test_api_update_queues_patch_through_update_server(_parse_request, update, client):
    import web_app.__main__  # noqa: F401

    response = client.post("/api/update", json={})

    assert response.status_code == 200
    assert response.get_json()["patch_size"] == "0.01 kB"
    update.assert_called_once_with("patch contents")


def test_canary_worker_does_not_start_scheduler():
    import web_app.__main__ as web_main

    config = ConfigManager()
    previous = config.deployment_canary
    config.deployment_canary = True
    try:
        with (
            patch.object(web_main, "configure_logging"),
            patch.object(web_main, "ensure_local_redis"),
            patch.object(web_main, "register_installed_apps"),
            patch.object(web_main, "start_scheduler") as start_scheduler,
            patch.object(web_main, "Repo") as repo,
        ):
            repo.return_value.head.commit.hexsha = "candidate-sha"
            web_main.prod_entry()
    finally:
        config.deployment_canary = previous

    start_scheduler.assert_not_called()


def test_production_logging_identifies_worker_and_thread():
    import web_app.__main__ as web_main

    with (
        patch.object(web_main, "ConcurrentRotatingFileHandler") as handler,
        patch.object(web_main.logging, "basicConfig") as basic_config,
    ):
        web_main.configure_logging(debug=False)

    config = ConfigManager()
    handler.assert_called_once_with(
        str(config.log_file_path.resolve()),
        maxBytes=config.dev.log_rotation_max_bytes,
        backupCount=config.dev.log_rotation_backup_count,
    )
    log_format = basic_config.call_args.kwargs["format"]
    assert "worker=%(process)d" in log_format
    assert "thread=%(thread)d" in log_format
    assert logging.getLogger("apscheduler.scheduler").level == logging.WARNING
    assert logging.getLogger("redis").level == logging.INFO


def test_prod_data_sync_excludes_server_backups_and_logs(tmp_path):
    from scripts.api_helper import sync_data_from_prod

    with patch("scripts.api_helper.subprocess.run") as run:
        run.return_value.returncode = 0
        result = CliRunner().invoke(
            sync_data_from_prod,
            ["--dest", str(tmp_path)],
        )

    assert result.exit_code == 0
    command = run.call_args.args[0]
    assert "--exclude=backups/" in command
    assert "--exclude=data/logs/" in command


def test_prod_entry_logs_gunicorn_worker_start(caplog):
    import web_app.__main__ as web_main

    config = ConfigManager()
    previous = config.deployment_canary
    config.deployment_canary = True
    try:
        with (
            patch.object(web_main, "configure_logging"),
            patch.object(web_main, "ensure_local_redis"),
            patch.object(web_main, "register_installed_apps"),
            patch.object(web_main, "Repo") as repo,
            caplog.at_level(logging.INFO),
        ):
            repo.return_value.head.commit.hexsha = "candidate-sha"
            web_main.prod_entry()
    finally:
        config.deployment_canary = previous

    assert '"event": "worker.started"' in caplog.text
    assert '"event": "server.started"' not in caplog.text
