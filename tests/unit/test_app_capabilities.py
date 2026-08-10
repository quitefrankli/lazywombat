from __future__ import annotations

import json
import os
import threading
import time
from datetime import timedelta
from unittest.mock import Mock, patch

import fakeredis
import pytest
from flask import Flask
from flask_login import LoginManager
from nabicat_app_sdk import (
    DOCUMENTS,
    AccessDenied,
    AccessLevel,
    Lease,
    TextGenerationRequest,
    TextImage,
)

from web_app.app_capabilities import (
    FileDocuments,
    HostAccessControl,
    HostTextGeneration,
    RedisLeases,
    RedisState,
    production_capabilities,
)
from web_app.config import ConfigManager
from web_app.helpers import codex_cli_text
from web_app.redis_client import get_redis


class _JsonCodec:
    def encode(self, value: dict, /) -> bytes:
        return json.dumps(value, sort_keys=True).encode()

    def decode(self, value: bytes, /) -> dict:
        return json.loads(value)


def test_file_documents_keep_an_app_inside_its_namespace(tmp_path):
    documents = FileDocuments(tmp_path / "sentinel")
    report = documents.document(
        "runs/run-1/report.json",
        codec=_JsonCodec(),
        default=dict,
    )

    with report.edit() as value:
        value["verdict"] = "pass"
    documents.blob("runs/run-1/screenshots/step-00.png").write(b"png")

    assert report.read() == {"verdict": "pass"}
    assert documents.names("runs/run-1") == (
        "runs/run-1/report.json",
        "runs/run-1/screenshots/step-00.png",
    )
    documents.backup_to(tmp_path / "backup" / "sentinel")
    report.write({"verdict": "fail"})
    assert (
        tmp_path / "backup" / "sentinel" / "runs/run-1/report.json"
    ).read_bytes() == b'{"verdict": "pass"}'
    assert documents.delete_tree("runs/run-1/screenshots") == 1
    assert documents.names() == ("runs/run-1/report.json",)
    assert documents.blob("runs/run-1/report.json").delete()
    assert (
        tmp_path / "backup" / "sentinel" / "runs/run-1/report.json"
    ).read_bytes() == b'{"verdict": "pass"}'
    with pytest.raises(ValueError, match="safe logical name"):
        documents.blob("../users.json")


def test_backup_holds_renewed_document_lock_for_the_complete_snapshot(
    tmp_path,
    monkeypatch,
):
    config = ConfigManager()
    monkeypatch.setattr(config, "rmw_lock_timeout_s", 1)
    monkeypatch.setattr(config, "rmw_lock_renewal_interval_s", 0.1)
    monkeypatch.setattr(config, "rmw_lock_blocking_timeout_s", 3.0)
    documents = FileDocuments(tmp_path / "sentinel")
    documents.blob("first.json").write(b"old-first")
    documents.blob("second.json").write(b"old-second")

    real_link = os.link
    first_linked = threading.Event()
    resume_backup = threading.Event()
    linked_first: list[str] = []

    def paused_link(source, destination, *args, **kwargs):
        result = real_link(source, destination, *args, **kwargs)
        if not linked_first:
            linked_first.append(os.path.basename(source))
            first_linked.set()
            assert resume_backup.wait(3)
        return result

    monkeypatch.setattr("web_app.app_capabilities.os.link", paused_link)
    backup_errors: list[BaseException] = []

    def back_up():
        try:
            documents.backup_to(tmp_path / "backup")
        except Exception as error:  # noqa: BLE001 - assert thread failures below
            backup_errors.append(error)

    backup_thread = threading.Thread(target=back_up)
    backup_thread.start()
    assert first_linked.wait(1)
    time.sleep(1.1)

    remaining_name = "second.json" if linked_first == ["first.json"] else "first.json"
    writer_done = threading.Event()

    def write_remaining():
        documents.blob(remaining_name).write(b"new")
        writer_done.set()

    writer_thread = threading.Thread(target=write_remaining)
    writer_thread.start()
    assert not writer_done.wait(0.2)
    resume_backup.set()
    backup_thread.join()
    writer_thread.join()

    assert backup_errors == []
    assert (tmp_path / "backup/first.json").read_bytes() == b"old-first"
    assert (tmp_path / "backup/second.json").read_bytes() == b"old-second"
    assert documents.blob(remaining_name).read() == b"new"


def test_backup_never_publishes_a_snapshot_after_lock_ownership_is_lost(
    tmp_path,
    monkeypatch,
):
    config = ConfigManager()
    monkeypatch.setattr(config, "rmw_lock_timeout_s", 2)
    monkeypatch.setattr(config, "rmw_lock_renewal_interval_s", 0.05)
    documents = FileDocuments(tmp_path / "sentinel")
    documents.blob("first.json").write(b"first")
    documents.blob("second.json").write(b"second")

    real_link = os.link
    first_linked = threading.Event()
    resume_backup = threading.Event()

    def paused_link(source, destination, *args, **kwargs):
        result = real_link(source, destination, *args, **kwargs)
        if not first_linked.is_set():
            first_linked.set()
            assert resume_backup.wait(3)
        return result

    monkeypatch.setattr("web_app.app_capabilities.os.link", paused_link)
    errors: list[BaseException] = []

    def back_up():
        try:
            documents.backup_to(tmp_path / "backup")
        except Exception as error:  # noqa: BLE001 - assert thread failures below
            errors.append(error)

    thread = threading.Thread(target=back_up)
    thread.start()
    assert first_linked.wait(1)
    redis = get_redis()
    [lock_key] = redis.keys("nabicat:lock:installed-app-documents:*")
    redis.delete(lock_key)
    redis.set(lock_key, b"other-owner", px=2_000)
    time.sleep(0.1)
    resume_backup.set()
    thread.join()
    redis.delete(lock_key)

    assert len(errors) == 1
    assert "lost ownership" in str(errors[0])
    assert not (tmp_path / "backup").exists()


def test_backup_rolls_back_if_publish_lock_is_lost(tmp_path, monkeypatch):
    documents = FileDocuments(tmp_path / "sentinel")
    documents.blob("report.json").write(b"report")
    redis = get_redis()
    real_rename = type(tmp_path).rename

    def rename_then_steal_lock(path, destination):
        result = real_rename(path, destination)
        [lock_key] = redis.keys("nabicat:lock:installed-app-documents:*")
        redis.delete(lock_key)
        redis.set(lock_key, b"other-owner", px=2_000)
        return result

    monkeypatch.setattr(type(tmp_path), "rename", rename_then_steal_lock)

    with pytest.raises(RuntimeError, match="lost ownership"):
        documents.backup_to(tmp_path / "backup")

    for lock_key in redis.keys("nabicat:lock:installed-app-documents:*"):
        redis.delete(lock_key)
    assert not (tmp_path / "backup").exists()


def test_state_replacement_preserves_ttl_and_leases_are_token_safe():
    redis = fakeredis.FakeRedis()
    state = RedisState(redis, "sentinel")
    leases = RedisLeases(redis, "sentinel")

    assert state.put(
        "cancel/run-1", b"requested", expires_in=timedelta(milliseconds=120)
    )
    time.sleep(0.06)
    assert state.replace("cancel/run-1", b"acknowledged")
    assert state.get("cancel/run-1") == b"acknowledged"
    time.sleep(0.08)
    assert state.get("cancel/run-1") is None

    lease = leases.try_acquire("execution", expires_in=timedelta(seconds=5))
    assert lease is not None
    assert leases.try_acquire("execution", expires_in=timedelta(seconds=5)) is None
    impostor = Lease(name=lease.name, token="not-the-owner")
    assert leases.renew(impostor, expires_in=timedelta(seconds=5)) is False
    assert leases.release(impostor) is False
    assert leases.renew(lease, expires_in=timedelta(seconds=5)) is True
    assert leases.release(lease) is True
    assert leases.try_acquire("execution", expires_in=timedelta(seconds=5)) is not None


def test_text_generation_materializes_byte_images_only_for_the_transport_call():
    observed_paths = ()

    def transport(request, image_paths):
        nonlocal observed_paths
        observed_paths = image_paths
        assert request.user == "inspect this"
        assert [path.read_bytes() for path in image_paths] == [b"png", b"jpeg"]
        assert [path.suffix for path in image_paths] == [".png", ".jpg"]
        return "done"

    generation = HostTextGeneration("sentinel", transport=transport)
    response = generation.generate(
        TextGenerationRequest(
            system="system",
            user="inspect this",
            images=(
                TextImage(b"png", "image/png"),
                TextImage(b"jpeg", "image/jpeg"),
            ),
        )
    )

    assert response == "done"
    assert all(not path.exists() for path in observed_paths)


def test_public_access_succeeds_without_an_authenticated_principal():
    app = Flask(__name__)
    app.secret_key = "test"
    login_manager = LoginManager(app)
    login_manager.user_loader(lambda _user_id: None)

    with app.test_request_context("/"):
        access = HostAccessControl()
        assert access.require(AccessLevel.PUBLIC) is None
        with pytest.raises(AccessDenied):
            access.require(AccessLevel.AUTHENTICATED)


@pytest.mark.parametrize("source", ["codex", "meridian", "bedrock"])
def test_text_generation_uses_host_owned_provider_and_model_policy(source):
    config = ConfigManager()
    previous = config.llm.api_source
    config.llm.api_source = source
    try:
        with (
            patch("web_app.helpers.codex_cli_text", return_value="ok") as codex,
            patch("web_app.helpers.meridian_text", return_value="ok") as meridian,
            patch("web_app.helpers.bedrock_text", return_value="ok") as bedrock,
        ):
            assert (
                HostTextGeneration("sentinel").generate(
                    TextGenerationRequest(system="system", user="user")
                )
                == "ok"
            )
            expected_model = (
                "gpt-5.6-sol"
                if source == "codex"
                else config.llm.model_for(config.installed_app_llm_tiers["sentinel"])
            )
    finally:
        config.llm.api_source = previous

    selected = {"codex": codex, "meridian": meridian, "bedrock": bedrock}[source]
    assert selected.call_count == 1
    assert selected.call_args.kwargs["model"] == expected_model


def test_profiled_codex_generation_preserves_sentinel_command_policy(tmp_path):
    image_path = tmp_path / "step.png"
    image_path.write_bytes(b"png")

    class DummyOutput:
        name = "/tmp/nabicat-host-codex-output.txt"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def seek(self, _position):
            return None

        def read(self):
            return "answer"

    completed = Mock(returncode=0, stdout="", stderr="")
    with (
        patch(
            "web_app.helpers.tempfile.NamedTemporaryFile", return_value=DummyOutput()
        ),
        patch("web_app.helpers.subprocess.run", return_value=completed) as run,
    ):
        assert (
            codex_cli_text(
                "user",
                "system",
                model="gpt-5.6-sol",
                image_paths=[image_path],
                reasoning_effort="medium",
                permissions_profile="sentinel_qa",
            )
            == "answer"
        )

    command = run.call_args.args[0]
    assert "--sandbox" not in command
    assert "--ignore-rules" in command
    assert 'default_permissions="sentinel_qa"' in command
    assert 'permissions.sentinel_qa.filesystem.:minimal="read"' in command
    assert 'model_reasoning_effort="medium"' in command
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert command.index("--image") < command.index("--output-last-message")


def test_production_documents_use_the_finalized_runtime_data_root(tmp_path):
    config = ConfigManager()
    previous = (
        config.debug_mode,
        config.production_data_root,
        config.debug_data_root,
    )
    config.production_data_root = tmp_path / "production"
    config.debug_data_root = tmp_path / "debug"
    try:
        config.debug_mode = True
        debug_documents = production_capabilities("sentinel", object())[DOCUMENTS]
        debug_documents.blob("runs/debug/report.json").write(b"debug")

        config.debug_mode = False
        production_documents = production_capabilities("sentinel", object())[DOCUMENTS]
        production_documents.blob("runs/production/report.json").write(b"production")
    finally:
        (
            config.debug_mode,
            config.production_data_root,
            config.debug_data_root,
        ) = previous

    assert (tmp_path / "debug/sentinel/runs/debug/report.json").read_bytes() == b"debug"
    assert (
        tmp_path / "production/sentinel/runs/production/report.json"
    ).read_bytes() == b"production"
    assert not (tmp_path / "production/sentinel/runs/debug/report.json").exists()
