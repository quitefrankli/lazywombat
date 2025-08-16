import flask
import flask_login

from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps

from web_app.app import app
from web_app.data_interface import DataInterface
from web_app.users import User
from web_app.errors import *


login_manager = flask_login.LoginManager()
login_manager.init_app(app)
@login_manager.user_loader
def user_loader(username: str) -> User | None:
    users = DataInterface().load_users()
    return users.get(username, None)

@login_manager.request_loader
def request_loader(request: flask.Request) -> User | None:
    username = request.form.get('username')
    if not username:
        return None
    existing_users = DataInterface().load_users()
    return existing_users.get(username, None)

@login_manager.unauthorized_handler
def unauthorized_handler():
    flask.flash('Log in required', category='error')
    return flask.redirect(flask.url_for('account_api.login', 
                                        next=flask.request.path))

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

def admin_only(failure_redirect: str):
    def _admin_only(func):
        @wraps(func)
        def decorated_view(*args, **kwargs):
            if not flask_login.current_user.is_admin:
                flask.flash('You must be an admin to access this page', category='error')
                return flask.redirect(flask.url_for(failure_redirect))

            return func(*args, **kwargs)

        return decorated_view
    return _admin_only

def get_ip() -> str:
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0]
    else:
        return request.remote_addr if request.remote_addr else "Unknown IP"
    
def cur_user() -> User:
    if not isinstance(flask_login.current_user, User):
        raise TypeError("Current user is not an instance of User")
    return flask_login.current_user

def authenticate_user(username: str, password: str, require_admin: bool = True) -> bool:
    if not username or not password:
        return False
    users = DataInterface().load_users()
    user = users.get(username)
    if not user or user.password != password:
        return False

    if require_admin and not user.is_admin:
        return False
    
    return True

def parse_request(require_login: bool = True, require_admin: bool = True) -> dict:
    if require_admin:
        require_login = True
    
    content_type = request.headers.get('Content-Type', '')
    if content_type.startswith('application/json'):
        request_body = request.get_json(silent=True)
        if request_body is None:
            raise APIError("Invalid JSON request_body")
    elif content_type.startswith('application/x-www-form-urlencoded'):
        request_body = request.form.to_dict()
    elif content_type.startswith('multipart/form-data'):
        request_body = {key: value for key, value in request.form.items()}
    else:
        raise APIError("Unsupported content type")
    
    if require_login:
        username = request_body.get("username", "")
        password = request_body.get("password", "")
        if not authenticate_user(username, password, require_admin=require_admin):
            raise AuthenticationError("Invalid credentials")
    
    return request_body