"""Update the yt-dlp minimum version in requirements.txt from PyPI."""

import argparse
import json
import re
import sys

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packaging.version import InvalidVersion, Version

from web_app.config import ConfigManager


class RequirementError(ValueError):
    """The yt-dlp requirement is missing, ambiguous, or invalid."""


class PyPIResponseError(ValueError):
    """PyPI did not return a usable stable yt-dlp version."""


@dataclass(frozen=True)
class UpdateResult:
    current_version: str
    latest_version: str
    changed: bool


def _stable_version(raw_version: Any) -> Version | None:
    if not isinstance(raw_version, str):
        return None
    try:
        version = Version(raw_version)
    except InvalidVersion:
        return None
    if version.is_prerelease or version.is_devrelease:
        return None
    return version


def _latest_stable_version(payload: Any) -> Version:
    if not isinstance(payload, dict):
        raise PyPIResponseError("PyPI response must be a JSON object")

    releases = payload.get("releases")
    candidates: list[Version] = []
    if isinstance(releases, dict):
        for raw_version, files in releases.items():
            version = _stable_version(raw_version)
            if version is None:
                continue
            if not isinstance(files, list) or not any(
                isinstance(file_data, dict)
                and file_data.get("yanked") is not True
                for file_data in files
            ):
                continue
            candidates.append(version)
    else:
        info = payload.get("info")
        if isinstance(info, dict):
            info_version = _stable_version(info.get("version"))
            if info_version is not None:
                candidates.append(info_version)

    if not candidates:
        raise PyPIResponseError("PyPI response contains no stable yt-dlp release")
    return max(candidates)


def _current_requirement(requirements: str, pattern: str) -> tuple[re.Match, Version]:
    matches = list(re.finditer(pattern, requirements, flags=re.MULTILINE))
    if len(matches) != 1:
        raise RequirementError(
            "requirements.txt must contain exactly one "
            "yt-dlp[default]>=<version> requirement"
        )

    match = matches[0]
    raw_version = match.group("version")
    try:
        version = Version(raw_version)
    except InvalidVersion as error:
        raise RequirementError(
            f"invalid yt-dlp requirement version: {raw_version!r}"
        ) from error
    if version.is_prerelease or version.is_devrelease:
        raise RequirementError(
            f"yt-dlp requirement floor must be stable: {raw_version!r}"
        )
    return match, version


def _fetch_latest_stable_version(
    opener: Callable[..., Any],
    config: ConfigManager,
) -> Version:
    request = Request(
        config.ytdlp_pypi_url,
        headers={"Accept": "application/json"},
    )
    with opener(request, timeout=config.ytdlp_update_timeout_s) as response:
        try:
            payload = json.load(response)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PyPIResponseError("PyPI returned invalid JSON") from error
    return _latest_stable_version(payload)


def update_ytdlp_requirement(
    requirements_path: Path,
    *,
    opener: Callable[..., Any] = urlopen,
) -> UpdateResult:
    """Raise on invalid input; otherwise update only a newer stable floor."""
    config = ConfigManager()
    requirements = requirements_path.read_text(encoding="utf-8")
    match, current = _current_requirement(
        requirements,
        config.ytdlp_requirement_pattern,
    )
    latest = _fetch_latest_stable_version(opener, config)

    if latest <= current:
        return UpdateResult(str(current), str(latest), changed=False)

    version_start, version_end = match.span("version")
    updated_requirements = (
        requirements[:version_start]
        + str(latest)
        + requirements[version_end:]
    )
    requirements_path.write_text(updated_requirements, encoding="utf-8")
    return UpdateResult(str(current), str(latest), changed=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update the yt-dlp requirement floor to PyPI's latest stable release."
    )
    parser.add_argument(
        "requirements",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "requirements.txt",
    )
    args = parser.parse_args()

    result = update_ytdlp_requirement(args.requirements)
    if result.changed:
        print(
            f"Updated yt-dlp requirement from {result.current_version} "
            f"to {result.latest_version}."
        )
    else:
        print(f"yt-dlp requirement is current at {result.current_version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
