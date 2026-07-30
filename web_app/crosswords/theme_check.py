"""LLM-backed sanity check that a user-supplied crossword theme
is a real, recognisable word or concept.

Kept separate from the word-bank so the format validator can be
tested without mocking HTTP.
"""
from __future__ import annotations

import logging

from web_app.config import ConfigManager
from web_app.crosswords.word_bank import InvalidThemeError
from web_app.helpers import MeridianError, CodexCLIError, meridian_text, codex_cli_text
from web_app.logging_utils import log_event

_SYSTEM = (
    "You are a strict validator. You are given a single English word and must decide "
    "whether it is a real, recognisable word or common concept that a person in their "
    "20s would know. Reply with exactly one token: 'YES' or 'NO'. No explanation."
)


def is_real_word(theme: str, timeout_s: float | None = None) -> bool:
    """Ask Meridian whether ``theme`` is a real word. Returns True on
    network/parse failure so a flaky Meridian never blocks the user.
    """
    config = ConfigManager()
    try:
        text = meridian_text(
            user_message=theme,
            system=_SYSTEM,
            model=config.llm.model_for(config.crosswords.llm_tier),
            max_tokens=config.crosswords.llm_theme_check_max_tokens,
            timeout_s=timeout_s or config.crosswords.llm_theme_check_timeout_s,
            agent="crosswords-theme",
        )
    except MeridianError as e:
        log_event(
            "crosswords", "crosswords.theme_check_failed_open",
            level=logging.WARNING, provider="meridian",
            exc_info=e, error_type=type(e).__name__,
        )
        return True

    ok = text.strip().upper().startswith("YES")
    log_event(
        "crosswords", "crosswords.theme_checked",
        provider="meridian", theme_length=len(theme), accepted=ok,
    )
    return ok


def is_real_word_codex(theme: str, timeout_s: float | None = None) -> bool | None:
    """Ask Codex whether ``theme`` is a real word.

    Returns None when Codex is unavailable, allowing callers to preserve
    Meridian's existing fail-open behavior.
    """
    config = ConfigManager()
    try:
        text = codex_cli_text(
            user_message=theme,
            instructions=_SYSTEM,
            model=config.llm.model_for(config.crosswords.llm_tier),
            timeout_s=timeout_s or config.crosswords.llm_theme_check_timeout_s,
        )
    except CodexCLIError as e:
        log_event(
            "crosswords", "crosswords.theme_check_unavailable",
            level=logging.WARNING, provider="codex",
            exc_info=e, error_type=type(e).__name__,
        )
        return None

    ok = text.strip().upper().startswith("YES")
    log_event(
        "crosswords", "crosswords.theme_checked",
        provider="codex", theme_length=len(theme), accepted=ok,
    )
    return ok


def require_real_word(theme: str) -> None:
    """Raise InvalidThemeError when the configured provider rejects the theme."""
    config = ConfigManager()
    if config.debug_mode:
        log_event(
            "crosswords", "crosswords.theme_check_skipped",
            theme_length=len(theme), reason="debug_mode",
        )
        return

    if config.llm.api_source == "codex":
        codex_ok = is_real_word_codex(theme)
        if codex_ok is not False:
            return
    elif is_real_word(theme):
        return

    raise InvalidThemeError(
        f"'{theme}' doesn't look like a real word. Try a single common English word."
    )
