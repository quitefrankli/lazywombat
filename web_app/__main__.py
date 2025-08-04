import click
import logging
import flask
import flask_login
import shutil

from typing import * # type: ignore
from pathlib import Path
from flask import render_template, request
from flask_apscheduler import APScheduler
from logging.handlers import RotatingFileHandler

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.helpers import admin_only, get_ip
from web_app.app import app
from web_app.crosswords import crosswords_api
from web_app.todoist2 import todoist2_api
from web_app.cheapify import cheapify_api
from web_app.account_api import account_api


app.register_blueprint(todoist2_api)
app.register_blueprint(crosswords_api)
app.register_blueprint(cheapify_api)
app.register_blueprint(account_api)

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

scheduler = APScheduler()
@scheduler.task('cron', id='scheduled_backup', day_of_week='sun', hour=0, minute=0, misfire_grace_time=3600)
def scheduled_backup():
    logging.info("Running scheduled backup")
    instance = DataInterface()
    instance.backup_data()
    logging.info("Backup complete")
scheduler.start()

@app.before_request
def before_request():
    # Auto-login as admin in debug mode
    if ConfigManager().debug_mode and not flask_login.current_user.is_authenticated:
        user = DataInterface().load_users()["admin"]
        flask_login.login_user(user)

    message = f"Processing request: client={get_ip(request)}, path={request.path}, method={request.method}"

    if request.method == 'POST':
        if request.is_json:
            message += f", json={request.get_json()}"
        elif request.form:
            message += f", form={request.form}"

    logging.info(message)

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/update', methods=['GET'])
@flask_login.login_required
@admin_only('home')
def update():
    # Update the server code
    import subprocess
    
    subprocess.Popen(["bash", "update_server.sh"], close_fds=True)

    flask.flash('Update in progress...', category='success')

    return flask.redirect(flask.url_for('todoist2_api.summary_goals'))

@app.route('/backup', methods=['GET'])
@flask_login.login_required
@admin_only('home')
def backup():
    DataInterface().backup_data()

    flask.flash('Backup complete', category='success')

    return flask.redirect(flask.url_for('todoist2_api.summary_goals')) # TODO change me

def configure_logging(debug: bool) -> None:
    log_path = Path("logs/web_app.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotating_log_handler = RotatingFileHandler(str(log_path),
                                                   maxBytes=int(1e6),
                                                   backupCount=10)
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, 
                        handlers=[] if debug else [rotating_log_handler],
                        format='%(asctime)s %(levelname)s %(message)s')

@click.command()
@click.option('--debug', is_flag=True, help='Run the server in debug mode', default=False)
@click.option('--port', default=80, help='Port to run the server on', type=int)
def cli_start(debug: bool, port: int):
    configure_logging(debug=debug)
    config = ConfigManager()
    config.debug_mode = debug

    # if we are in debug mode, copy the debug data to the save_data_path
    # if the save_data_path does not exist
    if debug:
        if not config.save_data_path.parent.exists():
            logging.info(f"Copying debug data to {config.save_data_path.parent}")
            debug_data_path = Path(f"tests/debug_save/.{config.project_name}")
            shutil.copytree(debug_data_path, config.save_data_path.parent)

    logging.info("Starting server")
    app.run(host='0.0.0.0', port=port, debug=debug)

if __name__ == '__main__':
    cli_start()
else:
    configure_logging(debug=False)
    logging.info("Starting server")
