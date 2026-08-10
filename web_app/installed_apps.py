from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from types import MappingProxyType
from typing import Any

import flask
from flask import Blueprint, Flask
from nabicat_app_sdk import (
    CURRENT_CONTRACT,
    ENTRY_POINT_GROUP,
    AccessDenied,
    AccessLevel,
    AppDefinition,
)


class InstalledAppError(RuntimeError):
    """An installed package cannot be integrated safely into NabiCat."""


@dataclass(frozen=True, slots=True)
class InstalledApp:
    definition: AppDefinition
    blueprint: Blueprint
    config: object
    distribution: str
    version: str

    def health(self) -> dict[str, str]:
        return {
            "app_id": self.definition.metadata.app_id,
            "config_type": type(self.config).__name__,
            "contract": str(self.definition.requires_contract),
            "distribution": self.distribution,
            "status": "loaded",
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class InstalledNavigation:
    app_id: str
    label: str
    endpoint: str
    icon: str | None
    order: int
    access: AccessLevel


@dataclass(frozen=True, slots=True)
class InstalledAppRegistry:
    apps: tuple[InstalledApp, ...]

    def health(self) -> tuple[dict[str, str], ...]:
        return tuple(installed.health() for installed in self.apps)

    @property
    def configs(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                installed.definition.metadata.app_id: installed.config
                for installed in self.apps
            }
        )

    def navigation(self) -> tuple[InstalledNavigation, ...]:
        items = []
        for installed in self.apps:
            navigation = installed.definition.metadata.navigation
            if navigation is None:
                continue
            items.append(
                InstalledNavigation(
                    app_id=installed.definition.metadata.app_id,
                    label=navigation.label,
                    endpoint=f"{installed.blueprint.name}{navigation.endpoint}",
                    icon=navigation.icon,
                    order=navigation.order,
                    access=installed.definition.metadata.access,
                )
            )
        return tuple(
            sorted(items, key=lambda item: (item.order, item.label, item.app_id))
        )

    def static_version(self, endpoint: str) -> str | None:
        for installed in self.apps:
            if endpoint == f"{installed.blueprint.name}.static":
                return installed.version
        return None

    def static_prefixes(self) -> tuple[str, ...]:
        prefixes = []
        for installed in self.apps:
            blueprint = installed.blueprint
            if blueprint.static_folder is None:
                continue
            static_url_path = blueprint.static_url_path or "/static"
            prefixes.append(f"{blueprint.url_prefix}{static_url_path.rstrip('/')}/")
        return tuple(sorted(prefixes))


def _entry_points() -> Iterable[Any]:
    return importlib_metadata.entry_points(group=ENTRY_POINT_GROUP)


def _configure(
    definition: AppDefinition,
    overrides: Mapping[str, object] | None,
) -> object:
    return definition.configure(overrides)


_ROUTE_VARIABLE = re.compile(r"<(?:(?P<converter>[^:<>]+):)?[^<>]+>")


def _canonical_route(rule: str) -> str:
    return _ROUTE_VARIABLE.sub(
        lambda match: f"<{match.group('converter') or 'string'}>",
        rule,
    )


def _metadata_access_denial(definition: AppDefinition):
    access = definition.metadata.access
    if access is AccessLevel.PUBLIC:
        return None

    from web_app.app_capabilities import HostAccessControl

    try:
        HostAccessControl().require(access)
        return None
    except AccessDenied:
        pass

    from web_app.config import ConfigManager
    from web_app.logging_utils import log_event

    app_id = definition.metadata.app_id
    log_event(
        app_id,
        "installed_app.access_denied",
        level=logging.WARNING,
        reason="insufficient_access",
        required_access=access.value,
    )
    api_prefix = f"/{app_id}/api/"
    if flask.request.method != "GET" or flask.request.path.startswith(api_prefix):
        flask.abort(403)

    config = ConfigManager()
    if access is AccessLevel.ADMIN:
        message = config.admin_access_denied_message
    elif access is AccessLevel.ELEVATED:
        message = config.elevated_access_denied_message
    else:
        message = "Log in required"
    flask.flash(message, category="error")
    return flask.redirect(flask.url_for(config.access_denied_redirect_endpoint))


def _add_access_guard(blueprint: Blueprint, definition: AppDefinition) -> None:
    if definition.metadata.access is AccessLevel.PUBLIC:
        return

    blueprint.before_request_funcs.setdefault(None, []).insert(
        0,
        lambda: _metadata_access_denial(definition),
    )


def _install_access_boundary(
    app: Flask, installed_apps: Iterable[InstalledApp]
) -> None:
    """Authorize installed routes before Flask runs any request preprocessor."""
    definitions = app.extensions.setdefault("nabicat_app_access_definitions", {})
    definitions.update(
        (installed.blueprint.name, installed.definition) for installed in installed_apps
    )
    if app.extensions.get("nabicat_app_access_boundary"):
        return

    original_preprocess_request = app.preprocess_request

    def host_preprocess_request():
        definition = next(
            (
                definitions[blueprint_name]
                for blueprint_name in flask.request.blueprints
                if blueprint_name in definitions
            ),
            None,
        )
        if definition is not None:
            denial = _metadata_access_denial(definition)
            if denial is not None:
                return denial
        return original_preprocess_request()

    app.preprocess_request = host_preprocess_request
    app.extensions["nabicat_app_access_boundary"] = True


def install_apps(
    app: Flask,
    *,
    entry_points: Iterable[Any] | None = None,
    config_overrides: Mapping[str, Mapping[str, object]] | None = None,
    capability_provider: Callable[[str, object], Mapping[Any, object]] | None = None,
) -> InstalledAppRegistry:
    """Discover and construct installed NabiCat apps in deterministic order."""
    overrides = config_overrides or {}
    uses_production_capabilities = capability_provider is None
    if capability_provider is None:
        from web_app.app_capabilities import production_capabilities

        provide = production_capabilities
    else:
        provide = capability_provider
    loaded: list[InstalledApp] = []
    discovered: list[tuple[Any, AppDefinition]] = []
    seen_app_ids: set[str] = set()

    for entry_point in sorted(
        entry_points if entry_points is not None else _entry_points(),
        key=lambda candidate: (candidate.name, candidate.value),
    ):
        definition = entry_point.load()
        if not isinstance(definition, AppDefinition):
            raise InstalledAppError(
                f"entry point {entry_point.name!r} did not load an AppDefinition"
            )
        app_id = definition.metadata.app_id
        if app_id in seen_app_ids:
            raise InstalledAppError(f"duplicate app id {app_id!r}")
        seen_app_ids.add(app_id)
        discovered.append((entry_point, definition))

    unknown_overrides = set(overrides) - seen_app_ids
    if unknown_overrides:
        names = ", ".join(repr(name) for name in sorted(unknown_overrides))
        raise InstalledAppError(f"unknown installed app config override(s): {names}")

    if discovered and uses_production_capabilities:
        from web_app.redis_client import ensure_local_redis

        ensure_local_redis()

    prepared: list[InstalledApp] = []
    for entry_point, definition in discovered:
        app_id = definition.metadata.app_id
        config = _configure(definition, overrides.get(app_id))
        blueprint = definition.create(
            contract=CURRENT_CONTRACT,
            config=config,
            capabilities=provide(app_id, config),
        )
        if not isinstance(blueprint, Blueprint):
            raise InstalledAppError(
                f"app {app_id!r} did not construct a Flask Blueprint"
            )
        if blueprint.name != app_id:
            raise InstalledAppError(
                f"app {app_id!r} constructed blueprint {blueprint.name!r}"
            )
        if blueprint.url_prefix != f"/{app_id}":
            raise InstalledAppError(
                f"app {app_id!r} blueprint must use url prefix '/{app_id}'"
            )
        _add_access_guard(blueprint, definition)
        distribution = getattr(entry_point, "dist", None)
        prepared.append(
            InstalledApp(
                definition=definition,
                blueprint=blueprint,
                config=config,
                distribution=getattr(distribution, "name", entry_point.name),
                version=getattr(distribution, "version", "unknown"),
            )
        )

    occupied_blueprints = set(app.blueprints)
    occupied_endpoints = set(app.view_functions)
    occupied = {
        (method, _canonical_route(rule.rule))
        for rule in app.url_map.iter_rules()
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    for installed in prepared:
        app_id = installed.definition.metadata.app_id
        blueprint_name = installed.blueprint.name
        if blueprint_name in occupied_blueprints:
            raise InstalledAppError(
                f"blueprint collision for installed app {app_id!r}: {blueprint_name!r}"
            )
        occupied_blueprints.add(blueprint_name)
        probe = Flask(
            f"nabicat_installed_{installed.definition.metadata.app_id}",
            static_folder=None,
        )
        probe.register_blueprint(installed.blueprint)
        probe_endpoints = {rule.endpoint for rule in probe.url_map.iter_rules()}
        endpoint_conflicts = occupied_endpoints & probe_endpoints
        if endpoint_conflicts:
            conflict = min(endpoint_conflicts)
            raise InstalledAppError(
                f"endpoint collision for installed app {app_id!r}: {conflict!r}"
            )
        navigation = installed.definition.metadata.navigation
        navigation_endpoint = (
            f"{blueprint_name}{navigation.endpoint}" if navigation is not None else None
        )
        if (
            navigation_endpoint is not None
            and navigation_endpoint not in probe_endpoints
        ):
            raise InstalledAppError(
                f"installed app {app_id!r} navigation endpoint "
                f"{navigation_endpoint!r} does not exist"
            )
        occupied_endpoints.update(probe_endpoints)
        for rule in probe.url_map.iter_rules():
            for method in rule.methods - {"HEAD", "OPTIONS"}:
                route = (method, _canonical_route(rule.rule))
                if route in occupied:
                    raise InstalledAppError(
                        f"route collision for installed app "
                        f"{installed.definition.metadata.app_id!r}: {method} {rule.rule}"
                    )
                occupied.add(route)

    for installed in prepared:
        app.register_blueprint(installed.blueprint)
        loaded.append(installed)

    _install_access_boundary(app, loaded)

    registry = InstalledAppRegistry(tuple(loaded))
    app.extensions["nabicat_apps"] = registry
    return registry
