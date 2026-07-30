import logging

from flask import Blueprint, jsonify, render_template, request

from web_app.config import ConfigManager
from web_app.crosswords.generator import build_crossword
from web_app.crosswords.theme_check import require_real_word
from web_app.crosswords.word_bank import (
    InvalidThemeError,
    clamp_difficulty,
    theme_criteria,
    validate_theme,
)
from web_app.crosswords.word_source import default_source
from web_app.helpers import register_app_name
from web_app.logging_utils import log_event

crosswords_api = Blueprint(
    'crosswords',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/crosswords'
)


register_app_name(crosswords_api, 'Crosswords')


@crosswords_api.route('/')
def index():
    cfg = ConfigManager()
    return render_template(
        'crosswords_index.html',
        default_theme=cfg.crosswords.default_theme,
        default_difficulty=cfg.crosswords.default_difficulty,
        difficulty_min=cfg.crosswords.difficulty_min,
        difficulty_max=cfg.crosswords.difficulty_max,
        theme_min_len=cfg.crosswords.theme_min_len,
        theme_max_len=cfg.crosswords.theme_max_len,
        theme_criteria=theme_criteria(),
    )


@crosswords_api.route('/api/new', methods=['POST'])
def new_crossword():
    cfg = ConfigManager()
    payload = request.get_json(silent=True) or {}
    try:
        theme = validate_theme(payload.get('theme'))
        require_real_word(theme)
    except InvalidThemeError as e:
        log_event(
            "crosswords", "crosswords.generation_rejected",
            level=logging.WARNING, reason="invalid_theme",
            error_type=type(e).__name__,
        )
        return jsonify({'error': str(e), 'criteria': theme_criteria()}), 400

    difficulty = clamp_difficulty(payload.get('difficulty'))
    count = cfg.crosswords.word_count
    log_event(
        "crosswords", "crosswords.generation_started",
        theme_length=len(theme), difficulty=difficulty,
        source=cfg.llm.api_source, count=count,
    )
    pairs = default_source().get_pairs(theme=theme, difficulty=difficulty, count=count)
    if not pairs:
        log_event(
            "crosswords", "crosswords.generation_failed",
            level=logging.WARNING, theme_length=len(theme), difficulty=difficulty,
            source=cfg.llm.api_source, reason="no_word_pairs",
        )
        return jsonify({'error': 'Could not generate words for that theme. Try another.'}), 503

    puzzle = build_crossword(pairs)
    puzzle['theme'] = theme
    puzzle['difficulty'] = difficulty
    placed = len(puzzle['clues']['across']) + len(puzzle['clues']['down'])
    log_event(
        "crosswords", "crosswords.generation_completed",
        theme_length=len(theme), difficulty=difficulty, source=cfg.llm.api_source,
        pairs=len(pairs), placed=placed,
        rows=puzzle["rows"], cols=puzzle["cols"],
    )
    return jsonify(puzzle)
