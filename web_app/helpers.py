import flask_login

from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from web_app.app import app


login_manager = flask_login.LoginManager()
login_manager.init_app(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["1 per second"],
    storage_uri="memory://",
    strategy="fixed-window", # or "moving-window"
)

def from_req(key: str) -> str:
    val = request.form[key] if key in request.form else request.args[key]
    return val.encode('ascii', 'ignore').decode('ascii')