from __future__ import annotations

import math
import os
import re
import secrets
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import flask_login
from atomicwrites import atomic_write
from flask_limiter.util import get_remote_address
from nabicat_app_sdk import (
    ACCESS_CONTROL,
    DOCUMENTS,
    EVENTS,
    LEASES,
    RATE_LIMITS,
    STATE,
    TEXT_GENERATION,
    WEB_TARGETS,
    AccessDenied,
    AccessLevel,
    Blob,
    Codec,
    Document,
    EventLevel,
    Lease,
    Principal,
    RateLimit,
    RateLimitScope,
    TextGenerationRequest,
    WebTarget,
)
from redis.exceptions import WatchError

_LOGICAL_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*\Z")


def _validate_logical_name(name: str) -> None:
    if _LOGICAL_NAME.fullmatch(name) is None or any(
        part in {".", ".."} for part in name.split("/")
    ):
        raise ValueError("name must be a safe logical name, not a filesystem path")


class FileDocuments:
    """Atomic, app-scoped persistent documents and binary blobs."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._editing = threading.local()
        self._lock_name = f"installed-app-documents:{self._root}"

    def document[T](
        self,
        name: str,
        *,
        codec: Codec[T],
        default: Callable[[], T],
    ) -> Document[T]:
        return _FileDocument(self, name, codec, default)

    def blob(self, name: str, /) -> Blob:
        return _FileBlob(self, name)

    def names(self, prefix: str = "", /) -> tuple[str, ...]:
        if prefix:
            _validate_logical_name(prefix)
        from web_app.redis_client import rmw_lock

        with rmw_lock(self._lock_name):
            if not self._root.exists():
                return ()
            tree_prefix = f"{prefix}/" if prefix else ""
            names = (
                path.relative_to(self._root).as_posix()
                for path in self._root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            return tuple(
                sorted(
                    name
                    for name in names
                    if not prefix or name == prefix or name.startswith(tree_prefix)
                )
            )

    def delete_tree(self, prefix: str, /) -> int:
        target = self._path(prefix)
        from web_app.redis_client import rmw_lock

        with rmw_lock(self._lock_name):
            if target.is_file() or target.is_symlink():
                target.unlink()
                self._remove_empty_parents(target.parent)
                return 1
            if not target.exists():
                return 0
            files = [
                path
                for path in target.rglob("*")
                if path.is_file() or path.is_symlink()
            ]
            for path in files:
                path.unlink()
            for directory in sorted(
                (path for path in target.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            ):
                directory.rmdir()
            target.rmdir()
            self._remove_empty_parents(target.parent)
            return len(files)

    def backup_to(self, destination: Path) -> None:
        """Hard-link a same-filesystem point-in-time snapshot under the document lock."""
        from web_app.redis_client import rmw_lock

        temporary_root: Path | None = None
        published = False
        try:
            with rmw_lock(self._lock_name):
                if not self._root.is_dir():
                    return
                destination.parent.mkdir(parents=True, exist_ok=True)
                if self._root.stat().st_dev != destination.parent.stat().st_dev:
                    raise OSError(
                        "installed-app backups require a same-filesystem destination"
                    )
                if destination.exists():
                    raise FileExistsError(
                        f"installed-app backup destination already exists: {destination}"
                    )
                temporary_root = Path(
                    tempfile.mkdtemp(
                        prefix=f".{destination.name}.nabicat-backup-",
                        dir=destination.parent,
                    )
                )
                shutil.copytree(
                    self._root,
                    temporary_root / "snapshot",
                    copy_function=os.link,
                    symlinks=True,
                )

            assert temporary_root is not None
            with rmw_lock(self._lock_name):
                if destination.exists():
                    raise FileExistsError(
                        f"installed-app backup destination already exists: {destination}"
                    )
                (temporary_root / "snapshot").rename(destination)
                published = True
            temporary_root.rmdir()
            temporary_root = None
        except BaseException:
            if published:
                shutil.rmtree(destination, ignore_errors=True)
            raise
        finally:
            if temporary_root is not None:
                shutil.rmtree(temporary_root, ignore_errors=True)

    def _path(self, name: str) -> Path:
        _validate_logical_name(name)
        path = (self._root / name).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("name must remain inside the app document namespace")
        return path

    def _active_edits(self) -> set[str]:
        active = getattr(self._editing, "names", None)
        if active is None:
            active = set()
            self._editing.names = active
        return active

    def _remove_empty_parents(self, directory: Path) -> None:
        while directory != self._root and directory.is_relative_to(self._root):
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent


class _FileBlob:
    def __init__(self, store: FileDocuments, name: str) -> None:
        self._store = store
        self._path = store._path(name)

    def read(self) -> bytes | None:
        from web_app.redis_client import rmw_lock

        with rmw_lock(self._store._lock_name):
            return self._path.read_bytes() if self._path.is_file() else None

    def write(self, value: bytes, /) -> None:
        if not isinstance(value, bytes):
            raise TypeError("blob values must be bytes")
        from web_app.config import ConfigManager
        from web_app.redis_client import rmw_lock

        with rmw_lock(self._store._lock_name):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with atomic_write(self._path, overwrite=True, mode="wb") as destination:
                destination.write(value)
            self._path.chmod(ConfigManager().installed_app_file_mode)

    def delete(self) -> bool:
        from web_app.redis_client import rmw_lock

        with rmw_lock(self._store._lock_name):
            if not self._path.is_file() and not self._path.is_symlink():
                return False
            self._path.unlink()
            self._store._remove_empty_parents(self._path.parent)
            return True


class _FileDocument[T]:
    def __init__(
        self,
        store: FileDocuments,
        name: str,
        codec: Codec[T],
        default: Callable[[], T],
    ) -> None:
        self._store = store
        self._name = name
        self._codec = codec
        self._default = default
        self._blob = _FileBlob(store, name)

    def read(self) -> T:
        encoded = self._blob.read()
        return self._default() if encoded is None else self._codec.decode(encoded)

    def write(self, value: T, /) -> None:
        self._blob.write(self._codec.encode(value))

    @contextmanager
    def edit(self) -> Iterator[T]:
        active = self._store._active_edits()
        if self._name in active:
            raise RuntimeError(
                f"nested edit for document {self._name!r} is not allowed"
            )
        from web_app.redis_client import rmw_lock

        with rmw_lock(self._store._lock_name):
            active.add(self._name)
            try:
                encoded = self._blob.read()
                value = (
                    self._default() if encoded is None else self._codec.decode(encoded)
                )
                before = self._codec.encode(value)
                yield value
                after = self._codec.encode(value)
                if after != before:
                    self._blob.write(after)
            finally:
                active.remove(self._name)

    def delete(self) -> bool:
        return self._blob.delete()


def _expiry_ms(expires_in: timedelta) -> int:
    if expires_in <= timedelta(0):
        raise ValueError("expires_in must be positive")
    return max(1, math.ceil(expires_in.total_seconds() * 1000))


class RedisState:
    """Namespaced ephemeral byte state shared by every web worker."""

    def __init__(self, client: Any, app_id: str) -> None:
        from web_app.config import ConfigManager

        _validate_logical_name(app_id)
        self._client = client
        self._prefix = ConfigManager().installed_app_state_key_prefix.format(
            app_id=app_id
        )

    def _key(self, name: str) -> str:
        _validate_logical_name(name)
        return f"{self._prefix}{name}"

    def get(self, key: str, /) -> bytes | None:
        return self._client.get(self._key(key))

    def put(
        self,
        key: str,
        value: bytes,
        /,
        *,
        expires_in: timedelta | None = None,
        if_absent: bool = False,
    ) -> bool:
        if not isinstance(value, bytes):
            raise TypeError("state values must be bytes")
        options: dict[str, object] = {"nx": if_absent}
        if expires_in is not None:
            options["px"] = _expiry_ms(expires_in)
        return bool(self._client.set(self._key(key), value, **options))

    def delete(self, key: str, /) -> bool:
        return bool(self._client.delete(self._key(key)))

    def replace(self, key: str, value: bytes, /) -> bool:
        if not isinstance(value, bytes):
            raise TypeError("state values must be bytes")
        return bool(
            self._client.set(
                self._key(key),
                value,
                xx=True,
                keepttl=True,
            )
        )


class RedisLeases:
    """Token-owned, expiring leases shared by every web worker."""

    def __init__(self, client: Any, app_id: str) -> None:
        from web_app.config import ConfigManager

        _validate_logical_name(app_id)
        self._client = client
        self._prefix = ConfigManager().installed_app_lease_key_prefix.format(
            app_id=app_id
        )

    def _key(self, name: str) -> str:
        _validate_logical_name(name)
        return f"{self._prefix}{name}"

    def try_acquire(
        self,
        name: str,
        /,
        *,
        expires_in: timedelta,
    ) -> Lease | None:
        token = secrets.token_urlsafe(32)
        acquired = self._client.set(
            self._key(name),
            token.encode(),
            nx=True,
            px=_expiry_ms(expires_in),
        )
        return Lease(name=name, token=token) if acquired else None

    def renew(
        self,
        lease: Lease,
        /,
        *,
        expires_in: timedelta,
    ) -> bool:
        key = self._key(lease.name)
        token = lease.token.encode()
        ttl_ms = _expiry_ms(expires_in)
        return self._token_owned_update(
            key,
            token,
            lambda pipeline: pipeline.pexpire(key, ttl_ms),
        )

    def release(self, lease: Lease, /) -> bool:
        key = self._key(lease.name)
        return self._token_owned_update(
            key,
            lease.token.encode(),
            lambda pipeline: pipeline.delete(key),
        )

    def _token_owned_update(
        self,
        key: str,
        token: bytes,
        update: Callable[[Any], object],
    ) -> bool:
        while True:
            with self._client.pipeline() as pipeline:
                try:
                    pipeline.watch(key)
                    if pipeline.get(key) != token:
                        pipeline.unwatch()
                        return False
                    pipeline.multi()
                    update(pipeline)
                    return bool(pipeline.execute()[0])
                except WatchError:
                    continue


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
    return {
        ACCESS_CONTROL: HostAccessControl(),
        EVENTS: HostEvents(app_id),
        RATE_LIMITS: HostRateLimits(app_id),
        DOCUMENTS: FileDocuments(config.save_data_path / app_id),
        STATE: RedisState(redis, app_id),
        LEASES: RedisLeases(redis, app_id),
        TEXT_GENERATION: HostTextGeneration(app_id),
        WEB_TARGETS: HostWebTargets(
            allow_local=bool(getattr(app_config, "allow_local_targets", False))
        ),
    }
