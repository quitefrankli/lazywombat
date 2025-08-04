from flask import request, jsonify, Blueprint
from web_app.data_interface import DataInterface
import base64
import gzip
import subprocess
from io import BytesIO


api_api = Blueprint('api_api', __name__, url_prefix='/api')

@api_api.route('/update_with_patch', methods=['POST'])
def api_update_with_patch():
    username = request.form.get('username')
    password = request.form.get('password')
    content = request.form.get('content')
    
    if not username or not password or not content:
        return jsonify({'error': 'Invalid request'}), 400
    users = DataInterface().load_users()
    user = users.get(username)
    if not user or user.password != password:
        return jsonify({'error': 'Invalid credentials'}), 401
    if not user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    # Decode base64 and decompress gzip
    try:
        compressed_bytes = base64.b64decode(content)
        with gzip.GzipFile(fileobj=BytesIO(compressed_bytes)) as gz:
            original_data = gz.read()
    except Exception as e:
        return jsonify({'error': f'Failed to decode and decompress: {str(e)}'}), 400

    print(f"Original data: {original_data}")

    subprocess.Popen(["bash", "update_server.sh", original_data.decode('utf-8')], close_fds=True)

    return jsonify({
        'success': True, 
        'patch_size': len(original_data),
    })