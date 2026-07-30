"""Sources of (word, clue) pairs for the crossword generator.

A ``WordSource`` takes (theme, difficulty, count) and returns a list of
(word, clue) pairs. Implementations:

* ``DebugSource`` - deterministic hand-picked fixtures, used in debug mode.
* ``MeridianSource`` - asks the Meridian LLM proxy for themed pairs.
* ``CodexSource`` - asks the local Codex CLI for themed pairs.
* ``FallbackSource`` - shuffled slice of a hardcoded modern pool, used
  when LLM sources are unreachable or return nothing usable.
* ``ChainedSource`` - tries each source in order, moves on if a source
  returns fewer than ``min_pairs`` usable entries.

``default_source()`` returns the chain appropriate to the current
``ConfigManager.debug_mode``.
"""
from __future__ import annotations

import json
import logging
import random
import re
from abc import ABC, abstractmethod
from typing import List

from web_app.config import ConfigManager
from web_app.crosswords.word_bank import (
    DEBUG_SETS,
    FALLBACK_POOL,
    WordClue,
)
from web_app.helpers import MeridianError, CodexCLIError, meridian_text, codex_cli_text
from web_app.logging_utils import log_event


class WordSource(ABC):
    """Abstract source of (word, clue) pairs."""

    @abstractmethod
    def get_pairs(self, theme: str, difficulty: int, count: int) -> List[WordClue]:
        """Return up to ``count`` (word, clue) pairs for the theme+difficulty.
        An empty list means 'no results' and lets a chained caller move on.
        """


class DebugSource(WordSource):
    """Deterministic fixture lookup. Returns [] if no fixture for the key."""

    def get_pairs(self, theme: str, difficulty: int, count: int) -> List[WordClue]:
        pairs = DEBUG_SETS.get((theme.lower(), difficulty))
        if pairs:
            log_event(
                "crosswords", "crosswords.word_source_completed",
                source="DebugSource", pairs=len(pairs),
                theme_length=len(theme), difficulty=difficulty,
            )
            return list(pairs)
        log_event(
            "crosswords", "crosswords.word_source_empty",
            source="DebugSource", theme_length=len(theme), difficulty=difficulty,
        )
        return []


class FallbackSource(WordSource):
    """Shuffled slice of a modern general-purpose pool."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()

    def get_pairs(self, theme: str, difficulty: int, count: int) -> List[WordClue]:
        count = max(2, min(count, len(FALLBACK_POOL)))
        pairs = self._rng.sample(FALLBACK_POOL, count)
        log_event(
            "crosswords", "crosswords.word_source_completed",
            source="FallbackSource", pairs=len(pairs),
        )
        return pairs


class MeridianSource(WordSource):
    """Ask Meridian for themed (word, clue) pairs at the given difficulty.

    Difficulty 1 = easiest, 5 = hardest. We tell the model to keep words
    modern and familiar to people in their 20s, and to avoid archaic
    vocabulary.
    """

    _SYSTEM = (
        "You generate crossword word/clue pairs. Respond with ONLY a JSON array "
        "of objects: [{\"word\": \"...\", \"clue\": \"...\"}, ...]. "
        "Rules:\n"
        "- Words must be single tokens, letters A-Z only (no spaces, hyphens, digits, or accents).\n"
        "- Words must be modern and familiar to a person in their 20s. Avoid archaic or obscure vocabulary.\n"
        "- Clues must be concise (under 80 chars) and not contain the answer word.\n"
        "- Match the requested difficulty:\n"
        "  1 = everyday concrete words, direct clues.\n"
        "  2 = common vocabulary, light wordplay.\n"
        "  3 = moderately tricky, some indirection.\n"
        "  4 = challenging, cryptic-lite, longer words.\n"
        "  5 = hardest: technical or niche-modern terms, cryptic style.\n"
        "- Respond with JSON only. No prose, no code fences."
    )

    def __init__(self, timeout_s: float | None = None) -> None:
        self._timeout = timeout_s

    def get_pairs(self, theme: str, difficulty: int, count: int) -> List[WordClue]:
        config = ConfigManager()
        prompt = (
            f"Theme: {theme}\n"
            f"Difficulty: {difficulty} (1=easiest, 5=hardest)\n"
            f"Return exactly {count} word/clue pairs as a JSON array."
        )
        try:
            text = meridian_text(
                user_message=prompt,
                system=self._SYSTEM,
                model=config.llm.model_for(config.crosswords.llm_tier),
                max_tokens=config.crosswords.llm_generation_max_tokens,
                timeout_s=self._timeout or config.crosswords.llm_generation_timeout_s,
                agent="crosswords",
            )
        except MeridianError as e:
            log_event(
                "crosswords", "crosswords.word_source_failed",
                level=logging.WARNING, source="MeridianSource",
                exc_info=e, error_type=type(e).__name__,
            )
            return []

        pairs = _parse_pairs(text)
        log_event(
            "crosswords", "crosswords.word_source_completed",
            source="MeridianSource", pairs=len(pairs),
            theme_length=len(theme), difficulty=difficulty,
        )
        return pairs


class CodexSource(WordSource):
    """Ask local Codex CLI for themed (word, clue) pairs at the given difficulty."""

    _SYSTEM = MeridianSource._SYSTEM

    def __init__(self, timeout_s: float | None = None) -> None:
        self._timeout = timeout_s

    def get_pairs(self, theme: str, difficulty: int, count: int) -> List[WordClue]:
        config = ConfigManager()
        prompt = (
            f"Theme: {theme}\n"
            f"Difficulty: {difficulty} (1=easiest, 5=hardest)\n"
            f"Return exactly {count} word/clue pairs as a JSON array."
        )
        try:
            text = codex_cli_text(
                user_message=prompt,
                instructions=self._SYSTEM,
                model=config.llm.model_for(config.crosswords.llm_tier),
                timeout_s=self._timeout or config.crosswords.llm_generation_timeout_s,
            )
        except CodexCLIError as e:
            log_event(
                "crosswords", "crosswords.word_source_failed",
                level=logging.WARNING, source="CodexSource",
                exc_info=e, error_type=type(e).__name__,
            )
            return []

        pairs = _parse_pairs(text)
        log_event(
            "crosswords", "crosswords.word_source_completed",
            source="CodexSource", pairs=len(pairs),
            theme_length=len(theme), difficulty=difficulty,
        )
        return pairs


class ChainedSource(WordSource):
    """Try sources in order; first one that yields >= ``min_pairs`` wins."""

    def __init__(self, sources: List[WordSource], min_pairs: int | None = None) -> None:
        self._sources = sources
        self._min_pairs = min_pairs

    def get_pairs(self, theme: str, difficulty: int, count: int) -> List[WordClue]:
        min_pairs = self._min_pairs if self._min_pairs is not None else ConfigManager().crosswords.min_placed_words
        for source in self._sources:
            source_name = source.__class__.__name__
            log_event(
                "crosswords", "crosswords.word_source_started",
                source=source_name, theme_length=len(theme),
                difficulty=difficulty, count=count,
            )
            pairs = source.get_pairs(theme, difficulty, count)
            if len(pairs) >= min_pairs:
                log_event(
                    "crosswords", "crosswords.word_source_selected",
                    source=source_name, pairs=len(pairs),
                )
                return pairs
            log_event(
                "crosswords", "crosswords.word_source_insufficient",
                level=logging.WARNING, source=source_name,
                pairs=len(pairs), required=min_pairs,
            )
        return []


def _parse_pairs(text: str) -> List[WordClue]:
    """Extract (word, clue) pairs from a Meridian JSON-array response."""
    # Strip optional ```json fences defensively.
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)

    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        # Last-ditch: find the first '[' ... matching ']' substring.
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end <= start:
            return []
        try:
            items = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            log_event(
                "crosswords", "crosswords.word_source_parse_failed",
                level=logging.WARNING, source="llm",
            )
            return []

    if not isinstance(items, list):
        return []

    pairs: List[WordClue] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word", "")).strip().upper()
        clue = str(item.get("clue", "")).strip()
        if not word.isalpha() or not clue:
            continue
        pairs.append((word, clue))
    return pairs


def default_source() -> WordSource:
    """Return the configured word source chain."""
    config = ConfigManager()
    if config.debug_mode:
        return ChainedSource([DebugSource(), FallbackSource()])

    if config.llm.api_source == "meridian":
        return ChainedSource([MeridianSource(), FallbackSource()])
    return ChainedSource([CodexSource(), FallbackSource()])
