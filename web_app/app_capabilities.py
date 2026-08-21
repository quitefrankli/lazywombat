from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import flask_login
from flask_limiter.util import get_remote_address
from nabicat_app_sdk import (
    ACCESS_CONTROL,
    DOCUMENTS,
    EVENTS,
    LEASES,
    NABICAT_RUNTIME,
    RATE_LIMITS,
    STATE,
    TEXT_GENERATION,
    USER_DOCUMENTS,
    WEB_TARGETS,
    AccessDenied,
    AccessLevel,
    AppData,
    CoupledRuntime,
    EventLevel,
    FilesystemDocuments,
    Principal,
    RateLimit,
    RateLimitScope,
    TextGenerationRequest,
    User,
    UserDocumentScope,
    WebTarget,
)
from nabicat_app_sdk import DataInterface as SdkDataInterface
from nabicat_app_sdk import (
    RedisLeases as SdkRedisLeases,
)
from nabicat_app_sdk import (
    RedisState as SdkRedisState,
)

from web_app.config import ConfigManager

_LOGICAL_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*\Z")
_USER_FOLDER = re.compile(
    rf"{ConfigManager().installed_app_user_folder_pattern}\Z"
)


def _validate_logical_name(name: str) -> None:
    if _LOGICAL_NAME.fullmatch(name) is None or any(
        part in {".", ".."} for part in name.split("/")
    ):
        raise ValueError("name must be a safe logical name, not a filesystem path")


def validate_user_folder(folder: str) -> None:
    if _USER_FOLDER.fullmatch(folder) is None:
        raise ValueError("user folder must be a single safe opaque name")


def _documents_lock_name(root: Path) -> str:
    return f"installed-app-documents:{root.resolve()}"


class FileDocuments(FilesystemDocuments):
    """NabiCat's Redis-locked SDK filesystem document adapter."""

    def __init__(
        self,
        root: Path,
        *,
        lock_name: str | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        from web_app.redis_client import rmw_lock

        super().__init__(
            root,
            lock_factory=rmw_lock,
            lock_name=lock_name or _documents_lock_name(root),
            file_mode=ConfigManager().installed_app_file_mode,
            guard=guard,
        )


class HostUserDocuments:
    """Resolve user documents from the authenticated user's opaque folder."""

    def __init__(self, app_id: str, *, data_root: Path | None = None) -> None:
        _validate_logical_name(app_id)
        self._app_id = app_id
        self._data_root = (data_root or ConfigManager().save_data_path).resolve()

    def current(self) -> UserDocumentScope:
        principal = HostAccessControl().require(AccessLevel.AUTHENTICATED)
        assert principal is not None

        from web_app.data_interface import DataInterface

        active_user = DataInterface().load_users().get(principal.subject)
        if active_user is None or not active_user.folder:
            raise RuntimeError("authenticated user has no active document scope")
        documents = self._scope(active_user.id, active_user.folder)
        return UserDocumentScope(principal, documents)

    def _scope(self, user_id: str, folder: str) -> FileDocuments:
        validate_user_folder(folder)
        app_root = self._data_root / self._app_id
        root = app_root / folder

        def ensure_safe_root() -> None:
            if app_root.is_symlink() or root.is_symlink():
                raise RuntimeError("user document scope must not be a symlink")

        def ensure_active() -> None:
            from web_app.data_interface import DataInterface

            ensure_safe_root()
            active_user = DataInterface().load_users_local().get(user_id)
            if active_user is None or active_user.folder != folder:
                raise RuntimeError("user document scope is no longer active")

        ensure_safe_root()
        return FileDocuments(
            root,
            lock_name=_documents_lock_name(app_root),
            guard=ensure_active,
        )

class RedisState(SdkRedisState):
    """Namespaced ephemeral byte state shared by every web worker."""

    def __init__(self, client: Any, app_id: str) -> None:
        _validate_logical_name(app_id)
        super().__init__(
            client,
            ConfigManager().installed_app_state_key_prefix.format(app_id=app_id),
        )


class RedisLeases(SdkRedisLeases):
    """Token-owned, expiring leases shared by every web worker."""

    def __init__(self, client: Any, app_id: str) -> None:
        _validate_logical_name(app_id)
        super().__init__(
            client,
            ConfigManager().installed_app_lease_key_prefix.format(app_id=app_id),
        )


class HostTextGeneration:
    """Text generation through host-selected transports and model policy."""

    def __init__(
        self,
        app_id: str,
        *,
        transport: Callable[[TextGenerationRequest, tuple[Path, ...]], str]
        | None = None,
    ) -> None:
        _validate_logical_name(app_id)
        self._app_id = app_id
        self._transport = transport or self._generate_with_configured_transport

    def generate(self, request: TextGenerationRequest, /) -> str:
        from web_app.config import ConfigManager

        config = ConfigManager()
        with tempfile.TemporaryDirectory(
            prefix=config.installed_app_text_temp_prefix.format(app_id=self._app_id)
        ) as temp_dir:
            root = Path(temp_dir)
            paths: list[Path] = []
            for index, image in enumerate(request.images):
                try:
                    suffix = config.installed_app_text_image_extensions[
                        image.media_type.lower()
                    ]
                except KeyError as error:
                    raise ValueError(
                        f"unsupported text-generation image media type {image.media_type!r}"
                    ) from error
                path = root / f"image-{index:02d}{suffix}"
                path.write_bytes(image.data)
                path.chmod(config.installed_app_file_mode)
                paths.append(path)
            return self._transport(request, tuple(paths))

    def _generate_with_configured_transport(
        self,
        request: TextGenerationRequest,
        image_paths: tuple[Path, ...],
    ) -> str:
        from web_app.config import ConfigManager
        from web_app.helpers import bedrock_text, codex_cli_text, meridian_text

        config = ConfigManager()
        llm_source = config.llm.api_source
        tier = config.installed_app_llm_tiers.get(
            self._app_id,
            config.installed_app_default_llm_tier,
        )
        model = config.llm.model_for(tier)
        timeout_s = request.timeout.total_seconds()
        if llm_source == "codex":
            model = config.installed_app_codex_models.get(self._app_id, model)
            return codex_cli_text(
                request.user,
                request.system,
                model=model,
                timeout_s=timeout_s,
                image_paths=list(image_paths),
                reasoning_effort=config.installed_app_codex_reasoning_effort.get(
                    self._app_id
                ),
                permissions_profile=config.installed_app_codex_permissions_profile.get(
                    self._app_id
                ),
            )
        if llm_source == "meridian":
            return meridian_text(
                user_message=request.user,
                system=request.system,
                model=model,
                max_tokens=request.max_tokens,
                timeout_s=timeout_s,
                agent=self._app_id,
                image_paths=list(image_paths),
            )
        if llm_source == "bedrock":
            return bedrock_text(
                user_message=request.user,
                system=request.system,
                model=model,
                max_tokens=request.max_tokens,
                timeout_s=timeout_s,
                image_paths=list(image_paths),
            )
        raise RuntimeError(f"unsupported LLM source {llm_source!r}")


class HostAccessControl:
    def current(self) -> Principal | None:
        try:
            user = flask_login.current_user
            if not getattr(user, "is_authenticated", False):
                return None
        except RuntimeError:
            return None
        roles = {"authenticated"}
        if getattr(user, "is_admin", False):
            roles.update(("admin", "elevated"))
        elif getattr(user, "is_elevated", False):
            roles.add("elevated")
        return Principal(subject=str(user.get_id()), roles=frozenset(roles))

    def require(self, access: AccessLevel, /) -> Principal | None:
        principal = self.current()
        if access is AccessLevel.PUBLIC:
            return principal
        if principal is None or not _principal_allows(principal, access):
            raise AccessDenied(access)
        return principal


def _principal_allows(principal: Principal, access: AccessLevel) -> bool:
    if access in (AccessLevel.PUBLIC, AccessLevel.AUTHENTICATED):
        return True
    if access is AccessLevel.ELEVATED:
        return bool(principal.roles & {"elevated", "admin"})
    return "admin" in principal.roles


class HostEvents:
    def __init__(self, app_id: str) -> None:
        _validate_logical_name(app_id)
        self._app_id = app_id

    def emit(
        self,
        name: str,
        /,
        *,
        level: EventLevel = EventLevel.INFO,
        error: BaseException | None = None,
        fields=None,
        actor: Principal | None = None,
    ) -> None:
        import logging

        from web_app.logging_utils import log_event

        levels = {
            EventLevel.DEBUG: logging.DEBUG,
            EventLevel.INFO: logging.INFO,
            EventLevel.WARNING: logging.WARNING,
            EventLevel.ERROR: logging.ERROR,
            EventLevel.CRITICAL: logging.CRITICAL,
        }
        details = dict(fields or {})
        reserved = {"app", "event", "user", "ip", "exc_info", "level"}
        conflicts = reserved & details.keys()
        if conflicts:
            raise ValueError(
                f"event fields use reserved names: {', '.join(sorted(conflicts))}"
            )
        log_event(
            self._app_id,
            name,
            level=levels[level],
            user=actor.subject if actor is not None else None,
            exc_info=error,
            **details,
        )


class HostRateLimits:
    def __init__(self, app_id: str) -> None:
        _validate_logical_name(app_id)
        self._app_id = app_id

    def limit(self, rule: RateLimit, /):
        from web_app.helpers import limiter

        seconds = rule.window.total_seconds()
        period = str(int(seconds)) if seconds.is_integer() else str(seconds)
        rate = f"{rule.requests} per {period} seconds"
        if rule.scope is RateLimitScope.APP:
            key_func = lambda: self._app_id
        elif rule.scope is RateLimitScope.PRINCIPAL:
            key_func = self._principal_or_address
        else:
            key_func = get_remote_address
        return limiter.limit(rate, key_func=key_func)

    @staticmethod
    def _principal_or_address() -> str:
        principal = HostAccessControl().current()
        return principal.subject if principal is not None else get_remote_address()


class HostWebTargets:
    def __init__(self, *, allow_local: bool = False) -> None:
        self._allow_local = allow_local

    def describe(
        self,
        url: str,
        /,
        *,
        additional_hosts=(),
    ) -> WebTarget:
        from web_app.config import ConfigManager
        from web_app.web_targets import resolve_web_target

        resolved = resolve_web_target(
            url,
            additional_hosts=additional_hosts,
            allow_local=self._allow_local,
            blocked_hostnames=ConfigManager().proxy.blocked_metadata_hostnames,
        )
        return WebTarget(
            url=resolved.url,
            allowed_hosts=resolved.allowed_hosts,
            addresses=resolved.addresses,
        )


def production_capabilities(app_id: str, app_config: object) -> dict[object, object]:
    """Construct fresh app-scoped Adapters for one installed definition."""
    from web_app.config import ConfigManager
    from web_app.redis_client import get_redis

    config = ConfigManager()
    redis = get_redis()
    capabilities: dict[object, object] = {
        ACCESS_CONTROL: HostAccessControl(),
        EVENTS: HostEvents(app_id),
        RATE_LIMITS: HostRateLimits(app_id),
        DOCUMENTS: FileDocuments(config.save_data_path / app_id),
        USER_DOCUMENTS: HostUserDocuments(app_id, data_root=config.save_data_path),
        STATE: RedisState(redis, app_id),
        LEASES: RedisLeases(redis, app_id),
        TEXT_GENERATION: HostTextGeneration(app_id),
        WEB_TARGETS: HostWebTargets(
            allow_local=bool(getattr(app_config, "allow_local_targets", False))
        ),
    }

    from web_app.data_interface import DataSyncer
    from web_app.redis_client import rmw_lock

    app_data = AppData(app_id=app_id, data_root=config.save_data_path)
    syncer = DataSyncer.instance()
    data = SdkDataInterface(
        app_data,
        syncer=syncer,
        lock_factory=rmw_lock,
    )
    try:
        from git import Repo

        git = Repo(config.project_dir)
    except Exception:  # noqa: BLE001 - metadata is optional at runtime
        git = None

    def current_user():
        principal = flask_login.current_user
        if not getattr(principal, "is_authenticated", False):
            return None
        if isinstance(principal, User):
            return principal
        return User(
            username=principal.id,
            password=getattr(principal, "password", ""),
            folder=getattr(principal, "folder", ""),
            is_admin=getattr(principal, "is_admin", False),
            is_elevated=getattr(principal, "is_elevated", False),
        )

    capabilities[NABICAT_RUNTIME] = CoupledRuntime(
        app_id=app_id,
        app_config=app_config,
        host_config=config,
        redis=redis,
        data=data,
        sync=syncer,
        app_data=app_data,
        current_user=current_user,
        access=capabilities[ACCESS_CONTROL],
        events=capabilities[EVENTS],
        documents=capabilities[DOCUMENTS],
        user_documents=capabilities[USER_DOCUMENTS],
        leases=capabilities[LEASES],
        rate_limits=capabilities[RATE_LIMITS],
        state=capabilities[STATE],
        text_generation=capabilities[TEXT_GENERATION],
        web_targets=capabilities[WEB_TARGETS],
        git=git,
    )
    return capabilities
