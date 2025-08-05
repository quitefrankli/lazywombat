import base64
import gzip
import subprocess
import logging

from flask import request, jsonify, Blueprint, Response
from web_app.data_interface import DataInterface
from io import BytesIO


GITHUB_EVENT_HEADER = 'X-GitHub-Event'
api_api = Blueprint('api_api', __name__, url_prefix='/api')

def authenticate_user(username: str, password: str) -> bool:
    if not username or not password:
        return False
    users = DataInterface().load_users()
    user = users.get(username)
    if not user or user.password != password:
        return False
    return user.is_admin

def handle_github_webhook():
    if request.headers.get(GITHUB_EVENT_HEADER) != 'push':
        return jsonify({"status": "ignored"}), 200
        
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        return jsonify({'error': 'Missing or invalid Authorization header'}), 401

    encoded_credentials = auth_header.split(" ")[1]
    decoded_bytes = base64.b64decode(encoded_credentials)
    decoded_credentials = decoded_bytes.decode("utf-8")

    try:
        username, password = decoded_credentials.split(":", 1)
    except ValueError:
        return jsonify({'error': 'Invalid credentials format'}), 400
    
    if not authenticate_user(username, password):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    subprocess.Popen(["bash", 
                      "update_server.sh", 
                      "&>>", 
                      "logs/shell_logs.log"], close_fds=True)

    return jsonify({
        'success': True, 
    })

@api_api.route('/update', methods=['POST'])
def api_update() -> Response:
    content_type = request.headers.get('Content-Type', '')
    if content_type.startswith('application/json'):
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({'error': 'Invalid JSON data'}), 400
    elif content_type.startswith('application/x-www-form-urlencoded'):
        data = request.form.to_dict()
    else:
        return jsonify({'error': 'Unsupported content type'}), 415

    if GITHUB_EVENT_HEADER in request.headers:
        return handle_github_webhook()

    # check if the request contains username and password in body
    # or if the username and password are provided in the Authorization header
    username = data.get('username', None)
    password = data.get('password', None)
    if not authenticate_user(username, password):
        return jsonify({'error': 'Invalid credentials'}), 401
    patch = data.get('patch', None)
    if not patch:
        return jsonify({'error': 'Missing patch data'}), 400
    try:
        # Test decode and decompress, don't actually apply the patch here
        # Just checking if the content can be decoded and decompressed
        compressed_bytes = base64.b64decode(patch)
        with gzip.GzipFile(fileobj=BytesIO(compressed_bytes)) as gz:
            original_data = gz.read()
    except Exception as e:
        return jsonify({'error': f'Failed to decode and decompress: {str(e)}'}), 400

    subprocess.Popen(["bash", 
                      "update_server.sh", 
                      "-p",
                      f"\"{patch}\"", 
                      "&>>", 
                      "logs/shell_logs.log"], close_fds=True)
    
    return jsonify({
        'success': True, 
        'patch_size': len(original_data),
    })
    
