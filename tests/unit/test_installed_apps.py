from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest
from flask import Blueprint, Flask, request
from flask_login import LoginManager, UserMixin
from nabicat_app_sdk import (
    CURRENT_CONTRACT,
    AccessLevel,
    AppDefinition,
    AppMetadata,
    ContractRange,
    ContractVersion,
    Navigation,
)

from web_app.config import ConfigManager
from web_app.helpers import backup_installed_app_data
from web_app.installed_apps import InstalledAppError, install_apps


@dataclass(frozen=True)
class _Config:
    greeting: str = "default"


class _Distribution:
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version


class _EntryPoint:
    def __init__(self, definition: AppDefinition, distribution: str) -> None:
        self.name = definition.metadata.app_id
        self.value = f"{distribution}:APP"
        self.dist = _Distribution(distribution, "1.2.3")
        self._definition = definition

    def load(self) -> AppDefinition:
        return self._definition


def _definition(
    app_id: str,
    *,
    access: AccessLevel = AccessLevel.PUBLIC,
    include_api: bool = False,
) -> AppDefinition:
    def build(context):
        blueprint = Blueprint(app_id, __name__, url_prefix=f"/{app_id}")

        @blueprint.get("/")
        def index():
            return context.config.greeting

        if include_api:
            blueprint.add_url_rule(
                "/api/runs",
                "create_run",
                lambda: "created",
                methods=["POST"],
            )

        return blueprint

    return AppDefinition(
        metadata=AppMetadata(
            app_id=app_id,
            display_name=app_id.title(),
            access=access,
            navigation=Navigation(label=app_id.title(), icon="bi-eye-fill"),
        ),
        requires_contract=ContractRange(
            minimum=CURRENT_CONTRACT,
            before=ContractVersion(2),
        ),
        config_type=_Config,
        build=build,
    )


def test_installed_apps_are_configured_registered_and_reported_deterministically():
    app = Flask(__name__)
    entries = (
        _EntryPoint(_definition("zeta"), "nabicat-zeta"),
        _EntryPoint(_definition("alpha"), "nabicat-alpha"),
    )

    registry = install_apps(
        app,
        entry_points=entries,
        config_overrides={"alpha": {"greeting": "configured"}},
        capability_provider=lambda _app_id, _config: {},
    )

    assert tuple(app.blueprints) == ("alpha", "zeta")
    assert app.test_client().get("/alpha/").text == "configured"
    assert registry.configs["alpha"] == _Config(greeting="configured")
    assert [item.endpoint for item in registry.navigation()] == [
        "alpha.index",
        "zeta.index",
    ]
    assert registry.health() == (
        {
            "app_id": "alpha",
            "config_type": "_Config",
            "contract": ">=1.0,<2.0",
            "distribution": "nabicat-alpha",
            "status": "loaded",
            "version": "1.2.3",
        },
        {
            "app_id": "zeta",
            "config_type": "_Config",
            "contract": ">=1.0,<2.0",
            "distribution": "nabicat-zeta",
            "status": "loaded",
            "version": "1.2.3",
        },
    )


def test_duplicate_installed_app_ids_fail_before_any_blueprint_is_registered():
    app = Flask(__name__)
    entries = (
        _EntryPoint(_definition("alpha"), "first-package"),
        _EntryPoint(_definition("alpha"), "second-package"),
    )

    with pytest.raises(InstalledAppError, match="duplicate app id 'alpha'"):
        install_apps(
            app,
            entry_points=entries,
            capability_provider=lambda _app_id, _config: {},
        )

    assert "alpha" not in app.blueprints


class _WebUser(UserMixin):
    def __init__(self, user_id: str, *, elevated: bool = False) -> None:
        self.id = user_id
        self.is_admin = False
        self.is_elevated = elevated

    def has_elevated_access(self) -> bool:
        return self.is_elevated


def test_metadata_access_guards_pages_and_api_writes():
    app = Flask(__name__)
    app.secret_key = "test"
    app.add_url_rule("/", "home", lambda: "home")
    users = {
        "plain": _WebUser("plain"),
        "elevated": _WebUser("elevated", elevated=True),
    }
    login_manager = LoginManager(app)
    login_manager.user_loader(users.get)
    install_apps(
        app,
        entry_points=(
            _EntryPoint(
                _definition(
                    "alpha",
                    access=AccessLevel.ELEVATED,
                    include_api=True,
                ),
                "nabicat-alpha",
            ),
        ),
        capability_provider=lambda _app_id, _config: {},
    )

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "plain"
    assert client.get("/alpha/").status_code == 302
    assert client.post("/alpha/api/runs").status_code == 403

    with client.session_transaction() as session:
        session["_user_id"] = "elevated"
    assert client.get("/alpha/").text == "default"


def test_host_access_guard_runs_before_app_defined_request_hooks():
    def build(_context):
        blueprint = Blueprint("alpha", __name__, url_prefix="/alpha")

        @blueprint.before_request
        def app_hook():
            return "app hook", 418

        blueprint.add_url_rule("/", "index", lambda: "index")
        return blueprint

    definition = AppDefinition(
        metadata=AppMetadata(
            app_id="alpha",
            display_name="Alpha",
            access=AccessLevel.ELEVATED,
            navigation=Navigation(label="Alpha"),
        ),
        requires_contract=ContractRange(CURRENT_CONTRACT, ContractVersion(2)),
        config_type=_Config,
        build=build,
    )
    app = Flask(__name__)
    app.secret_key = "test"
    app.add_url_rule("/", "home", lambda: "home")
    users = {
        "plain": _WebUser("plain"),
        "elevated": _WebUser("elevated", elevated=True),
    }
    login_manager = LoginManager(app)
    login_manager.user_loader(users.get)
    install_apps(
        app,
        entry_points=(_EntryPoint(definition, "nabicat-alpha"),),
        capability_provider=lambda _app_id, _config: {},
    )
    client = app.test_client()

    with client.session_transaction() as session:
        session["_user_id"] = "plain"
    assert client.get("/alpha/").status_code == 302

    with client.session_transaction() as session:
        session["_user_id"] = "elevated"
    assert client.get("/alpha/").status_code == 418


def test_metadata_access_boundary_precedes_app_wide_hooks_and_preprocessors():
    observed: list[str] = []

    def build(_context):
        blueprint = Blueprint("alpha", __name__, url_prefix="/alpha")

        @blueprint.app_url_value_preprocessor
        def app_preprocessor(_endpoint, _values):
            if request.path.startswith("/alpha"):
                observed.append("preprocessor")

        @blueprint.before_app_request
        def app_wide_hook():
            if request.path.startswith("/alpha"):
                observed.append("before-app")
                return "bypassed", 200
            return None

        blueprint.add_url_rule("/", "index", lambda: "index")
        blueprint.add_url_rule(
            "/api/runs",
            "create_run",
            lambda: "created",
            methods=["POST"],
        )
        return blueprint

    definition = AppDefinition(
        metadata=AppMetadata(
            app_id="alpha",
            display_name="Alpha",
            access=AccessLevel.ELEVATED,
            navigation=Navigation(label="Alpha"),
        ),
        requires_contract=ContractRange(CURRENT_CONTRACT, ContractVersion(2)),
        config_type=_Config,
        build=build,
    )
    app = Flask(__name__)
    app.secret_key = "test"
    app.add_url_rule("/", "home", lambda: "home")
    users = {"plain": _WebUser("plain")}
    login_manager = LoginManager(app)
    login_manager.user_loader(users.get)
    install_apps(
        app,
        entry_points=(_EntryPoint(definition, "nabicat-alpha"),),
        capability_provider=lambda _app_id, _config: {},
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "plain"

    assert client.get("/alpha/").status_code == 302
    assert client.post("/alpha/api/runs").status_code == 403
    assert observed == []


def test_installed_app_route_collisions_fail_with_the_conflicting_route():
    app = Flask(__name__)
    app.add_url_rule("/alpha/", "legacy_alpha", lambda: "legacy")

    with pytest.raises(InstalledAppError, match=r"route collision.*GET /alpha/"):
        install_apps(
            app,
            entry_points=(_EntryPoint(_definition("alpha"), "nabicat-alpha"),),
            capability_provider=lambda _app_id, _config: {},
        )

    assert "alpha" not in app.blueprints


def test_route_collision_ignores_variable_names_but_respects_route_shape():
    app = Flask(__name__)
    app.add_url_rule("/alpha/<item>", "legacy_item", lambda item: item)

    def build(_context):
        blueprint = Blueprint("alpha", __name__, url_prefix="/alpha")
        blueprint.add_url_rule("/<run_id>", "index", lambda run_id: run_id)
        return blueprint

    definition = AppDefinition(
        metadata=AppMetadata(
            app_id="alpha",
            display_name="Alpha",
            access=AccessLevel.PUBLIC,
            navigation=Navigation(label="Alpha"),
        ),
        requires_contract=ContractRange(CURRENT_CONTRACT, ContractVersion(2)),
        config_type=_Config,
        build=build,
    )

    with pytest.raises(InstalledAppError, match="route collision"):
        install_apps(
            app,
            entry_points=(_EntryPoint(definition, "nabicat-alpha"),),
            capability_provider=lambda _app_id, _config: {},
        )


def test_unknown_app_config_override_is_a_startup_error():
    app = Flask(__name__)

    with pytest.raises(InstalledAppError, match="unknown installed app.*typo"):
        install_apps(
            app,
            entry_points=(_EntryPoint(_definition("alpha"), "nabicat-alpha"),),
            config_overrides={"typo": {"greeting": "ignored"}},
            capability_provider=lambda _app_id, _config: {},
        )

    assert "alpha" not in app.blueprints


def test_production_state_is_ready_before_installed_app_capabilities_are_built():
    calls = []

    with (
        patch(
            "web_app.redis_client.ensure_local_redis",
            side_effect=lambda: calls.append("redis"),
        ),
        patch(
            "web_app.app_capabilities.production_capabilities",
            side_effect=lambda _app_id, _config: calls.append("capabilities") or {},
        ),
    ):
        install_apps(
            Flask(__name__),
            entry_points=(_EntryPoint(_definition("alpha"), "nabicat-alpha"),),
        )

    assert calls == ["redis", "capabilities"]


def test_blueprint_and_endpoint_conflicts_fail_before_registering_any_app():
    for conflict in ("blueprint", "endpoint"):
        app = Flask(f"conflict-{conflict}")
        if conflict == "blueprint":
            existing = Blueprint("alpha", __name__, url_prefix="/legacy-alpha")
            existing.add_url_rule("/", "legacy", lambda: "legacy")
            app.register_blueprint(existing)
        else:
            app.add_url_rule("/legacy-alpha", "alpha.index", lambda: "legacy")

        entries = (
            _EntryPoint(_definition("beta"), "nabicat-beta"),
            _EntryPoint(_definition("alpha"), "nabicat-alpha"),
        )
        with pytest.raises(InstalledAppError, match=conflict):
            install_apps(
                app,
                entry_points=entries,
                capability_provider=lambda _app_id, _config: {},
            )

        assert "beta" not in app.blueprints


def test_navigation_must_name_an_endpoint_constructed_by_the_app():
    def build(_context):
        blueprint = Blueprint("alpha", __name__, url_prefix="/alpha")
        blueprint.add_url_rule("/other", "other", lambda: "other")
        return blueprint

    definition = AppDefinition(
        metadata=AppMetadata(
            app_id="alpha",
            display_name="Alpha",
            access=AccessLevel.PUBLIC,
            navigation=Navigation(label="Alpha", endpoint=".index"),
        ),
        requires_contract=ContractRange(CURRENT_CONTRACT, ContractVersion(2)),
        config_type=_Config,
        build=build,
    )

    with pytest.raises(InstalledAppError, match="navigation endpoint 'alpha.index'"):
        install_apps(
            Flask(__name__),
            entry_points=(_EntryPoint(definition, "nabicat-alpha"),),
            capability_provider=lambda _app_id, _config: {},
        )


def test_backup_copies_only_loaded_app_namespaces(tmp_path):
    app = Flask(__name__)
    install_apps(
        app,
        entry_points=(_EntryPoint(_definition("alpha"), "nabicat-alpha"),),
        capability_provider=lambda _app_id, _config: {},
    )
    config = ConfigManager()
    previous_debug = config.debug_mode
    previous_root = config.debug_data_root
    config.debug_mode = True
    config.debug_data_root = tmp_path / "data"
    (config.save_data_path / "alpha").mkdir(parents=True)
    (config.save_data_path / "alpha" / "report.json").write_text("report")
    (config.save_data_path / "not-installed").mkdir()
    (config.save_data_path / "not-installed" / "private.txt").write_text("private")

    try:
        backup_installed_app_data(tmp_path / "backup", flask_app=app)
    finally:
        config.debug_mode = previous_debug
        config.debug_data_root = previous_root

    assert (tmp_path / "backup" / "alpha" / "report.json").read_text() == "report"
    assert not (tmp_path / "backup" / "not-installed").exists()


def test_installed_static_prefix_and_distribution_version_are_host_visible(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()

    def build(_context):
        blueprint = Blueprint(
            "alpha",
            __name__,
            url_prefix="/alpha",
            static_folder=str(static_dir),
            static_url_path="/assets",
        )
        blueprint.add_url_rule("/", "index", lambda: "index")
        return blueprint

    definition = AppDefinition(
        metadata=AppMetadata(
            app_id="alpha",
            display_name="Alpha",
            access=AccessLevel.PUBLIC,
            navigation=Navigation(label="Alpha"),
        ),
        requires_contract=ContractRange(CURRENT_CONTRACT, ContractVersion(2)),
        config_type=_Config,
        build=build,
    )
    registry = install_apps(
        Flask(__name__),
        entry_points=(_EntryPoint(definition, "nabicat-alpha"),),
        capability_provider=lambda _app_id, _config: {},
    )

    assert registry.static_prefixes() == ("/alpha/assets/",)
    assert registry.static_version("alpha.static") == "1.2.3"
