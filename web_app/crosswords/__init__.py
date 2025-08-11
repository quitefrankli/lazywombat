from flask import Blueprint, render_template, redirect, url_for
from typing import * # type: ignore
from crossword_generator import generate_crossword as mcts_gen
import os

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROK_API_KEY = os.getenv("GROK_API_KEY", "")
if not OPENAI_API_KEY or not GROK_API_KEY:
    raise ValueError("API keys for OpenAI and Groq must be set in environment variables.")

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

def generate_crossword() -> list:
    import tempfile
    import pandas as pd
    # Use mcts_gen from crossword_generator

    words_and_hints = generate_words_and_hints()
    print(f"Generated words and hints: {words_and_hints}")
    words = [word for word, _ in words_and_hints]
    hints = [hint for _, hint in words_and_hints]
    # Write words and hints to a temporary CSV file
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.csv', delete=False) as f:
        f.write(",answer\n")
        for idx, word in enumerate(words):
            f.write(f"{idx},{word}\n")
        words_csv_path = f.name

    # Output directory for results
    with tempfile.TemporaryDirectory() as output_dir:
        # Call the crossword generator
        max_length = max(len(word) for word in words)
        mcts_gen(
            path_to_words=words_csv_path,
            num_rows=max_length*2,
            num_cols=max_length*2,
            output_path=output_dir,
            max_num_words=20,
            max_mcts_iterations=1000,
        )
        # Find the generated layout CSV
        import glob
        layout_files = glob.glob(f"{output_dir}/*_layout_en.csv")
        if layout_files:
            grid_df = pd.read_csv(layout_files[0], index_col=0)
            grid = grid_df.values.tolist()
        else:
            grid = None
    return grid, words_and_hints


def generate_words_and_hints() -> List[Tuple[str, str]]:
    message = \
"""
Generate a list of 10 words and their hints for a crossword puzzle.
Each hint should be less than 50 characters long.
Your response MUST be exactly a list of words with a SINGLE colon after each word, like this:
"
word1: hint1
word2: hint2
word3: hint3
...
"
You must NOT include any additional texts or explanations.
"""
    
    raw_output = gen_with_groq(message)
    lines = raw_output.strip().split('\n')

    words_and_hints = []
    for line in lines:
        if ':' in line:
            word, hint = line.split(':', 1)
            words_and_hints.append((word.strip(), hint.strip()))

    return words_and_hints

@crosswords_api.route('/new', methods=['POST'])
def new_crossword():
    grid = generate_crossword()
    return render_template('crosswords_index.html', 
                           crossword_generated=True, 
                           crossword_grid=grid)


def gen_with_openai():
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)

    response = client.responses.create(
    model="gpt-4o-mini",
    input="write a haiku about ai",
    store=True,
    )

    return response.output_text

def gen_with_groq(message: str):
    from groq import Groq
    client = Groq(api_key=GROK_API_KEY)
    completion = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[
        {
            "role": "user",
            "content": message
        }
        ],
        temperature=1,
        max_completion_tokens=8192,
        top_p=1,
        reasoning_effort="medium",
        stream=True,
        stop=None
    )

    response = "".join(chunk.choices[0].delta.content or "" for chunk in completion)
    return response
    # for chunk in completion:
    #     print(chunk.choices[0].delta.content or "", end="")

def gen_with_gemini(message: str):
    from google.generativeai import Client
    client = Client()
    response = client.chat.completions.create(
        model="gemini-1.5-flash",
        messages=[
            {"role": "user", "content": message}
        ],
        temperature=1,
        max_output_tokens=8192,
        top_p=1,
        reasoning_effort="medium"
    )
    return response.candidates[0].content

# gen_with_openai()
# gen_with_groq()
# print(generate_crossword())