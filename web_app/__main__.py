import os
import click
import logging
import flask
import flask_login

from signal import signal, SIGTERM
from typing import * # type: ignore
from pathlib import Path
from flask import render_template, request
from flask_bootstrap import Bootstrap5
from flask_apscheduler import APScheduler
from logging.handlers import RotatingFileHandler

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.helpers import limiter, admin_only, get_ip
from web_app.app import app
from web_app.todoist2.todoist2_api import todoist2_api

app.secret_key = os.urandom(24)
app.register_blueprint(todoist2_api)

bootstrap = Bootstrap5(app)

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


def get_default_redirect():
    return flask.redirect(flask.url_for('home'))

@app.before_request
def before_request():
    message = f"Processing request: client={get_ip(request)}, path={request.path}, method={request.method}"

    if request.method == 'POST':
        if request.is_json:
            message += f", json={request.get_json()}"
        elif request.form:
            message += f", form={request.form}"

    logging.info(message)

def graceful_shutdown(signum=None, frame=None):
    logging.info("Shutting down server")
    exit(0)

@app.route('/shutdown')
@flask_login.login_required
@admin_only('home')
def shutdown():
    graceful_shutdown()
    return "Shutting down..."

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
def main(debug: bool, port: int):
    configure_logging(debug=debug)
    signal(SIGTERM, graceful_shutdown)
    ConfigManager().debug_mode = debug

    logging.info("Starting server")
    app.run(host='0.0.0.0', port=port, debug=debug)

scheduler = APScheduler()

@scheduler.task('cron', id='scheduled_backup', day_of_week='sun', hour=0, minute=0, misfire_grace_time=3600)
def scheduled_backup():
    logging.info("Running scheduled backup")
    instance = DataInterface()
    instance.backup_data()
    logging.info("Backup complete")

scheduler.start()

if __name__ == '__main__':
    main()
else:
    configure_logging(debug=False)
    logging.info("Starting server")
