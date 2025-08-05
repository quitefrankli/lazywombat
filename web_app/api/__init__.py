import base64
import gzip
import subprocess
import logging

from flask import request, jsonify, Blueprint
from web_app.data_interface import DataInterface
from web_app.helpers import get_ip
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

def handle_github_webhook(request_body: dict):
    if request.headers.get(GITHUB_EVENT_HEADER) != 'push':
        logging.info(f"Ignoring GitHub webhook event: {request.headers.get(GITHUB_EVENT_HEADER)}")
        return jsonify({"status": "ignored"}), 200
        
    # Step 3: Check if push was to the main branch
    ref = request_body.get('ref')
    if ref != "refs/heads/main":
        logging.info(f"Ignoring push event for non-main branch: {ref}")
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
    
    logging.info(f"Applying GitHub push webhook update")
    subprocess.Popen("bash update_server.sh &>> logs/shell_logs.log", shell=True, close_fds=True)

    return jsonify({
        'success': True, 
    }), 200

def retrieve_body_from_post() -> dict:
    content_type = request.headers.get('Content-Type', '')
    if content_type.startswith('application/json'):
        request_body = request.get_json(silent=True)
        if request_body is None:
            raise ValueError("Invalid JSON request_body")
    elif content_type.startswith('application/x-www-form-urlencoded'):
        request_body = request.form.to_dict()
    elif content_type.startswith('multipart/form-data'):
        request_body = {key: value for key, value in request.form.items()}
    else:
        raise ValueError("Unsupported content type")
    
    return request_body

@api_api.route('/update', methods=['POST'])
def api_update():
    logging.info(f"Received update request from {get_ip()}")
    try:
        request_body = retrieve_body_from_post()
    except ValueError as e:
        logging.error(f"Error processing request: {str(e)}")
        return jsonify({'error': str(e)}), 400

    if GITHUB_EVENT_HEADER in request.headers:
        return handle_github_webhook(request_body)

    # check if the request contains username and password in body
    # or if the username and password are provided in the Authorization header
    username = request_body.get('username', "")
    password = request_body.get('password', "")
    if not authenticate_user(username, password):
        return jsonify({'error': 'Invalid credentials'}), 401
    patch = request_body.get('patch', None)
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

    logging.info(f"Updating with patch of size {len(original_data)} bytes")
    subprocess.Popen(f"bash update_server.sh -p \"{patch}\" &>> logs/shell_logs.log", shell=True, close_fds=True)
    
    return jsonify({
        'success': True, 
        'patch_size': len(original_data),
    }), 200

@api_api.route('/backup', methods=['POST'])
def api_backup():
    logging.info(f"Received backup request from {get_ip()}")

    try:
        request_body = retrieve_body_from_post()
    except ValueError as e:
        logging.error(f"Error processing request: {str(e)}")
        return jsonify({'error': str(e)}), 400

    username = request_body.get('username', "")
    password = request_body.get('password', "")
    if not authenticate_user(username, password):
        return jsonify({'error': 'Invalid credentials'}), 401
    DataInterface().backup_data()
    logging.info("Backup complete")

    return jsonify({'success': True, 'message': 'Backup complete'})

