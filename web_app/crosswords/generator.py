"""
Simple crossword grid builder.

Given a pool of (word, clue) pairs this produces a compact grid with
standard Across/Down clue numbering. The algorithm is intentionally
small - good enough for hand-picked word pools of ~10-15 entries.

Output format is JSON-serialisable and consumed directly by the
template/JS:

    {
        "rows": int,
        "cols": int,
        "cells": [[ {"letter": str, "number": int|None} | None, ... ], ...],
        "clues": {
            "across": [{"number": int, "clue": str, "answer": str,
                        "row": int, "col": int, "length": int}, ...],
            "down":   [...],
        },
    }

Cells that are not part of any entry are `None` (rendered as black).
"""
from __future__ import annotations

import random
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from web_app.crosswords.word_bank import WordClue
from web_app.logging_utils import log_event

ACROSS = "across"
DOWN = "down"

_GRID_PAD = 1  # blank border around placed words


@dataclass
class _Placement:
    word: str
    clue: str
    row: int
    col: int
    direction: str  # ACROSS | DOWN


def _fits(grid: Dict[Tuple[int, int], str], word: str, row: int, col: int, direction: str) -> bool:
    dr, dc = (0, 1) if direction == ACROSS else (1, 0)

    # cell before start and after end must be empty (no accidental extensions)
    before = (row - dr, col - dc)
    after = (row + dr * len(word), col + dc * len(word))
    if before in grid or after in grid:
        return False

    for i, letter in enumerate(word):
        r, c = row + dr * i, col + dc * i
        existing = grid.get((r, c))
        if existing is None:
            # side cells must also be empty unless they belong to a crossing word
            # at the crossing cell itself (handled by existing check above).
            side1 = (r + dc, c + dr)
            side2 = (r - dc, c - dr)
            if side1 in grid or side2 in grid:
                return False
            continue
        if existing != letter:
            return False
    return True


def _write(grid: Dict[Tuple[int, int], str], placement: _Placement) -> None:
    dr, dc = (0, 1) if placement.direction == ACROSS else (1, 0)
    for i, letter in enumerate(placement.word):
        grid[(placement.row + dr * i, placement.col + dc * i)] = letter


def _try_place(
    grid: Dict[Tuple[int, int], str],
    placements: List[_Placement],
    word: str,
    clue: str,
) -> Optional[_Placement]:
    if not placements:
        p = _Placement(word=word, clue=clue, row=0, col=0, direction=ACROSS)
        _write(grid, p)
        return p

    for placed in placements:
        p_dr, p_dc = (0, 1) if placed.direction == ACROSS else (1, 0)
        for pi, pl in enumerate(placed.word):
            for wi, wl in enumerate(word):
                if pl != wl:
                    continue
                new_direction = DOWN if placed.direction == ACROSS else ACROSS
                n_dr, n_dc = (0, 1) if new_direction == ACROSS else (1, 0)
                cross_r = placed.row + p_dr * pi
                cross_c = placed.col + p_dc * pi
                row = cross_r - n_dr * wi
                col = cross_c - n_dc * wi
                if _fits(grid, word, row, col, new_direction):
                    p = _Placement(word=word, clue=clue, row=row, col=col, direction=new_direction)
                    _write(grid, p)
                    return p
    return None


def _normalise(placements: List[_Placement]) -> Tuple[int, int, List[_Placement]]:
    min_r = min(p.row for p in placements)
    min_c = min(p.col for p in placements)
    max_r = max(p.row + (len(p.word) - 1 if p.direction == DOWN else 0) for p in placements)
    max_c = max(p.col + (len(p.word) - 1 if p.direction == ACROSS else 0) for p in placements)

    shifted = [
        _Placement(
            word=p.word, clue=p.clue,
            row=p.row - min_r + _GRID_PAD,
            col=p.col - min_c + _GRID_PAD,
            direction=p.direction,
        )
        for p in placements
    ]
    rows = (max_r - min_r) + 1 + 2 * _GRID_PAD
    cols = (max_c - min_c) + 1 + 2 * _GRID_PAD
    return rows, cols, shifted


def _number_clues(rows: int, cols: int, placements: List[_Placement]) -> Tuple[
    List[List[Optional[dict]]], List[dict], List[dict]
]:
    letters: Dict[Tuple[int, int], str] = {}
    for p in placements:
        dr, dc = (0, 1) if p.direction == ACROSS else (1, 0)
        for i, letter in enumerate(p.word):
            letters[(p.row + dr * i, p.col + dc * i)] = letter

    def is_filled(r: int, c: int) -> bool:
        return (r, c) in letters

    across_starts: Dict[Tuple[int, int], _Placement] = {}
    down_starts: Dict[Tuple[int, int], _Placement] = {}
    for p in placements:
        key = (p.row, p.col)
        if p.direction == ACROSS:
            across_starts[key] = p
        else:
            down_starts[key] = p

    cells: List[List[Optional[dict]]] = [[None] * cols for _ in range(rows)]
    across_clues: List[dict] = []
    down_clues: List[dict] = []
    next_num = 1

    for r in range(rows):
        for c in range(cols):
            if not is_filled(r, c):
                continue
            starts_across = (r, c) in across_starts
            starts_down = (r, c) in down_starts
            number: Optional[int] = None
            if starts_across or starts_down:
                number = next_num
                next_num += 1
                if starts_across:
                    p = across_starts[(r, c)]
                    across_clues.append({
                        "number": number, "clue": p.clue, "answer": p.word,
                        "row": r, "col": c, "length": len(p.word),
                    })
                if starts_down:
                    p = down_starts[(r, c)]
                    down_clues.append({
                        "number": number, "clue": p.clue, "answer": p.word,
                        "row": r, "col": c, "length": len(p.word),
                    })
            cells[r][c] = {"letter": letters[(r, c)], "number": number}

    return cells, across_clues, down_clues


def build_crossword(pairs: List[WordClue], rng: Optional[random.Random] = None) -> dict:
    """Build a crossword from word/clue pairs.

    Words that cannot be placed without conflict are skipped - the
    remainder still produces a valid, fully-connected grid.
    """
    rng = rng or random.Random()
    # Place longest first so branches have many crossing options.
    ordered = sorted(pairs, key=lambda wc: -len(wc[0]))

    grid: Dict[Tuple[int, int], str] = {}
    placements: List[_Placement] = []
    for word, clue in ordered:
        word = word.upper()
        if not word.isalpha():
            log_event(
                "crosswords", "crosswords.word_skipped",
                reason="non_alpha", word_length=len(word),
            )
            continue
        placed = _try_place(grid, placements, word, clue)
        if placed is not None:
            placements.append(placed)
        else:
            log_event(
                "crosswords", "crosswords.word_skipped",
                reason="unplaceable", word_length=len(word),
            )

    if not placements:
        raise ValueError("No words could be placed")

    rows, cols, placements = _normalise(placements)
    cells, across, down = _number_clues(rows, cols, placements)
    log_event(
        "crosswords", "crosswords.grid_built",
        input_pairs=len(pairs), placed=len(placements),
        across=len(across), down=len(down), rows=rows, cols=cols,
    )

    # Sort clues by number for stable display.
    across.sort(key=lambda c: c["number"])
    down.sort(key=lambda c: c["number"])

    return {
        "rows": rows,
        "cols": cols,
        "cells": cells,
        "clues": {"across": across, "down": down},
    }
