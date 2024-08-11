import os
import random
import click
import logging

from typing import *
from pathlib import Path
from flask import Flask, render_template, send_from_directory, request
from flask_bootstrap import Bootstrap5
from datetime import datetime
from logging.handlers import RotatingFileHandler

from desktop_app.client import ClientV2
from desktop_app.goal import Goal


app = Flask(__name__)
bootstrap = Bootstrap5(app)

def get_random_image() -> Path:
    BASE_DIR = Path(os.environ["RANDOM_IMAGES_DIR"] if "RANDOM_IMAGES_DIR" in os.environ else "resources")
    print(BASE_DIR.absolute())
    images = list(BASE_DIR.glob("*.jpeg"))

    return random.choice(images).absolute()

def get_summary_goals() -> List[Tuple[str, List[Goal]]]:
    now = datetime.now()
    def should_render(goal: Goal) -> bool:
        # reserve rendering of children to later
        if goal.parent != -1:
            return False
        if goal.backlogged:
            return False
        if goal.repeat:
            return False
        # hides goals that have been completed for a while
        days_since_completion = 0 if not goal.state else (now - goal.metadata.completion_date).days
        if days_since_completion > 2:
            return False
        
        return True
    
    goals = ClientV2.instance().fetch_goals()
    goals = [goal for goal in goals if should_render(goal)]
    goals.sort(key=lambda goal: goal.metadata.creation_date.timestamp())

    goal_blocks = []
    last_date_label = Goal.NULL_DATE.date()
    for goal in goals:
        goal_date = goal.metadata.creation_date.date()
        if last_date_label != goal_date:
            last_date_label = goal_date
            goal_blocks.append((last_date_label.strftime("%d/%m/%Y"), [goal]))
        else:
            goal_blocks[-1] = (goal_blocks[-1][0], goal_blocks[-1][1] + [goal])

    return goal_blocks
    
def save_goals():
    ClientV2.instance().save_goals()

@app.route('/')
def index():
    dated_goal_blocks = get_summary_goals()
    dated_goal_blocks.reverse()
    return render_template('index.html', dated_goal_blocks=dated_goal_blocks)

@app.route('/debug')
def debug():
    return render_template('debug.html')

@app.route('/resources/<path:filename>')
def static_file(filename):
    image = get_random_image()
    logging.info(f"Sending {image} for {filename}")
    return send_from_directory(directory=image.parent, path=image.name)

@app.route('/goals/toggle_goal_state', methods=['POST'])
def toggle_goal_state():
    data = request.get_json()
    ClientV2.instance().toggle_goal_state(data['goal_id'])
    save_goals()

    return "OK"

@app.before_request
def before_request():
    message = f"Processing request: client={request.remote_addr}, path={request.path}, method={request.method}"

    if request.method == 'POST':
        message += f", form={request.form}"

    logging.debug(message)

@click.command()
@click.option('--debug', is_flag=True, help='Run the server in debug mode', default=False)
def main(debug: bool):
    ClientV2.create_instance(debug)
    log_path = Path("logs/web_app.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotating_log_handler = RotatingFileHandler(str(log_path),
                                               maxBytes=int(1e6),
                                               backupCount=10)
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, 
                        handlers=[] if debug else [rotating_log_handler])
    app.run(host='0.0.0.0', port=80, debug=debug)

if __name__ == '__main__':
    main()