from __future__ import annotations

import json
import time
from datetime import timedelta
from unittest.mock import Mock, patch

import fakeredis
import pytest
from flask import Flask
from flask_login import LoginManager
from nabicat_app_sdk import (
    ACCESS_CONTROL,
    DOCUMENTS,
    STATE,
    USER_DOCUMENTS,
    WEB_TARGETS,
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
    HostUserDocuments,
    RedisLeases,
    RedisState,
    production_capabilities,
)
from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.helpers import codex_cli_text
from web_app.redis_client import get_redis
from web_app.users import User


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
    report.write({"verdict": "fail"})
    assert documents.delete_tree("runs/run-1/screenshots") == 1
    assert documents.names() == ("runs/run-1/report.json",)
    assert documents.blob("runs/run-1/report.json").delete()
    with pytest.raises(ValueError, match="safe logical name"):
        documents.blob("../users.json")


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


def test_production_capabilities_provide_coupled_sdk_runtime(
    tmp_path,
    monkeypatch,
):
    from nabicat_app_sdk import NABICAT_RUNTIME, CoupledRuntime

    config = ConfigManager()
    previous_mode = config.debug_mode
    previous_root = config.debug_data_root
    config.debug_mode = True
    config.debug_data_root = tmp_path
    app_config = object()
    try:
        capabilities = production_capabilities("sentinel", app_config)
    finally:
        config.debug_mode = previous_mode
        config.debug_data_root = previous_root

    runtime = capabilities[NABICAT_RUNTIME]
    assert isinstance(runtime, CoupledRuntime)
    assert runtime.config is app_config
    assert runtime.host_config is config
    assert runtime.redis is get_redis()
    assert runtime.data.data.app_id == "sentinel"
    assert runtime.data.data.data_root == tmp_path
    assert runtime.data.data_syncer is runtime.data_syncer
    assert runtime.access is capabilities[ACCESS_CONTROL]
    assert runtime.documents is capabilities[DOCUMENTS]
    assert runtime.user_documents is capabilities[USER_DOCUMENTS]
    assert runtime.state is capabilities[STATE]
    assert runtime.web_targets is capabilities[WEB_TARGETS]

    user = User(username="alice", folder="opaque-7")
    config.debug_mode = True
    config.debug_data_root = tmp_path
    try:
        with DataInterface().edit_users() as users:
            users.add(user)
        monkeypatch.setattr("web_app.app_capabilities.flask_login.current_user", user)
        assert runtime.user().id == "alice"
        assert runtime.user().folder == "opaque-7"
        scope = runtime.current_user_data()
        assert scope.user is user
        scope.documents.blob("private/report.txt").write(b"private")
        assert (tmp_path / "sentinel/opaque-7/private/report.txt").read_bytes() == b"private"
    finally:
        config.debug_mode = previous_mode
        config.debug_data_root = previous_root


def test_user_documents_use_opaque_folder_and_revalidate_active_user(
    tmp_path,
    monkeypatch,
):
    user = User(username="alice", password="pw", folder="opaque-7")
    users = {user.id: user}
    synchronized_loads = 0

    def load_synchronized(_self):
        nonlocal synchronized_loads
        synchronized_loads += 1
        return users

    monkeypatch.setattr(
        "web_app.data_interface.DataInterface.load_users",
        load_synchronized,
    )
    monkeypatch.setattr(
        "web_app.data_interface.DataInterface.load_users_local",
        lambda _self: users,
    )
    monkeypatch.setattr("web_app.app_capabilities.flask_login.current_user", user)

    scope = HostUserDocuments("sentinel", data_root=tmp_path).current()
    assert scope is not None
    if hasattr(scope, "principal"):
        assert scope.principal.subject == "alice"
    documents = getattr(scope, "documents", scope)
    documents.blob("private/report.txt").write(b"report")
    assert (
        tmp_path / "sentinel/opaque-7/private/report.txt"
    ).read_bytes() == b"report"

    users.clear()
    with pytest.raises(RuntimeError, match="no longer active"):
        documents.blob("private/report.txt").read()
    assert synchronized_loads == 1


def test_user_documents_reject_a_symlinked_user_scope(tmp_path, monkeypatch):
    user = User(username="alice", password="pw", folder="opaque-7")
    users = {user.id: user}
    external = tmp_path / "external"
    external.mkdir()
    app_root = tmp_path / "sentinel"
    app_root.mkdir()
    (app_root / user.folder).symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(
        "web_app.data_interface.DataInterface.load_users",
        lambda _self: users,
    )
    monkeypatch.setattr("web_app.app_capabilities.flask_login.current_user", user)

    with pytest.raises(RuntimeError, match="symlink"):
        HostUserDocuments("sentinel", data_root=tmp_path).current()


def test_users_file_rejects_duplicate_document_folders():
    from web_app.users import UsersFile

    with pytest.raises(ValueError, match="folders must be unique"):
        UsersFile(
            root=[
                User(username="alice", password="pw", folder="shared"),
                User(username="bob", password="pw", folder="shared"),
            ]
        )

    users = UsersFile(root=[User(username="alice", password="pw", folder="shared")])
    with pytest.raises(ValueError, match="folders must be unique"):
        users.add(User(username="bob", password="pw", folder="shared"))


def test_users_file_rejects_unsafe_document_folders():
    from web_app.users import UsersFile

    with pytest.raises(ValueError, match="safe opaque names"):
        UsersFile(
            root=[
                User(username="alice", password="pw", folder="../shared"),
            ]
        )
