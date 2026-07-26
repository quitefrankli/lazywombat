import logging
from pathlib import Path
from unittest.mock import patch

from web_app.config import ConfigManager


def test_nginx_forwards_original_https_scheme():
    nginx_config = Path("nabicat.conf").read_text()

    assert nginx_config.count("proxy_set_header X-Forwarded-Proto $scheme;") == 2


def test_health_reports_commit_loaded_by_worker(client, app):
    import web_app.__main__  # noqa: F401

    app.config["DEPLOY_COMMIT"] = "candidate-sha"
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.get_json()["commit"] == "candidate-sha"
    assert isinstance(response.get_json()["pid"], int)


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


def test_canary_worker_does_not_start_scheduler():
    import web_app.__main__ as web_main

    config = ConfigManager()
    previous = config.deployment_canary
    config.deployment_canary = True
    try:
        with (
            patch.object(web_main, "configure_logging"),
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
        patch.object(web_main, "RotatingFileHandler"),
        patch.object(web_main.logging, "basicConfig") as basic_config,
    ):
        web_main.configure_logging(debug=False)

    log_format = basic_config.call_args.kwargs["format"]
    assert "worker=%(process)d" in log_format
    assert "thread=%(thread)d" in log_format
    assert logging.getLogger("apscheduler.scheduler").level == logging.WARNING


def test_prod_entry_logs_gunicorn_worker_start(caplog):
    import web_app.__main__ as web_main

    config = ConfigManager()
    previous = config.deployment_canary
    config.deployment_canary = True
    try:
        with (
            patch.object(web_main, "configure_logging"),
            patch.object(web_main, "Repo") as repo,
            caplog.at_level(logging.INFO),
        ):
            repo.return_value.head.commit.hexsha = "candidate-sha"
            web_main.prod_entry()
    finally:
        config.deployment_canary = previous

    assert "Starting gunicorn worker" in caplog.text
    assert "Starting server" not in caplog.text
