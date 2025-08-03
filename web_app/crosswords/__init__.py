from flask import Blueprint, render_template, redirect, url_for

crosswords_api = Blueprint(
    'crosswords',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/crosswords'
)

@crosswords_api.route('/')
def index():
    return render_template('crosswords_index.html')

def generate_crossword():
    # Simple static crossword for demonstration
    # 5x5 grid with two words: HELLO (across), WORLD (down)
    grid = [
        ['H', 'W', '', '', ''],
        ['E', 'O', '', '', ''],
        ['L', 'R', '', '', ''],
        ['L', 'L', '', '', ''],
        ['O', 'D', '', '', ''],
    ]
    return grid

@crosswords_api.route('/new', methods=['POST'])
def new_crossword():
    grid = generate_crossword()
    return render_template('crosswords_index.html', crossword_generated=True, crossword_grid=grid)
