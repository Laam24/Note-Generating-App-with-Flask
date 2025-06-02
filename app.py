from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import jwt
import os
from functools import wraps
from werkzeug.utils import secure_filename
import tempfile
from datetime import datetime
from supabase import create_client, Client
import logging
from mimetypes import guess_type

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
HF_API_TOKEN = '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'
SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://jbzjvydgdyfezsxxlphv.supabase.co')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', 'your-anon-key')
# Initialize Supabase client
from supabase import create_client, Client
import os

# Initialize Supabase client with custom HTTP client
# Initialize Supabase client
# Initialize Supabase client
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)


# Constants
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXTENSIONS = {
    'wav': 'audio/wav',
    'mp3': 'audio/mpeg',
    'm4a': 'audio/mp4',
    'aac': 'audio/aac',
    'ogg': 'audio/ogg',
    'flac': 'audio/flac',
    'mp4': 'video/mp4'  # For video files with audio
}

# Helper functions
def allowed_file(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return ext in ALLOWED_EXTENSIONS

def get_mime_type(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return ALLOWED_EXTENSIONS.get(ext, 'application/octet-stream')

# Authentication decorator
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({'error': 'Authorization header missing or invalid'}), 401
            
        token = auth_header.split(" ")[1]
        try:
            user = supabase.auth.get_user(token)
            if not user.user:
                return jsonify({'error': 'Invalid token'}), 401
                
            kwargs['user_id'] = user.user.id
            return f(*args, **kwargs)
            
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return jsonify({'error': 'Invalid token'}), 401
            
    return decorated

@app.route('/api/recordings', methods=['POST'])
@token_required
def upload_recording(user_id):
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
        
    audio_file = request.files['audio']
    course_code = request.form.get('course_code', '').strip()
    title = request.form.get('title', '').strip()
    
    # Validate inputs
    if not all([audio_file.filename, course_code, title]):
        return jsonify({'error': 'Missing required fields'}), 400
        
    if not allowed_file(audio_file.filename):
        allowed = ', '.join(ALLOWED_EXTENSIONS.keys())
        return jsonify({'error': f'Invalid file type. Allowed: {allowed}'}), 400

    try:
        # Save file temporarily
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            audio_file.save(tmp)
            tmp_path = tmp.name

        # Check file size
        file_size = os.path.getsize(tmp_path)
        if file_size > MAX_FILE_SIZE:
            return jsonify({
                'error': f'File too large (max {MAX_FILE_SIZE/1024/1024:.1f}MB)',
                'max_size_mb': MAX_FILE_SIZE/1024/1024,
                'actual_size_mb': file_size/1024/1024
            }), 400

        # Generate unique filename
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        file_ext = audio_file.filename.rsplit('.', 1)[1].lower()
        file_name = f"recordings/{user_id}/{timestamp}_{secure_filename(audio_file.filename)}"
        content_type = get_mime_type(audio_file.filename)

        # Upload to Supabase Storage with correct content type
        with open(tmp_path, 'rb') as f:
            # Updated upload method with correct parameter name
            res = supabase.storage.from_("recordings").upload(
                file_name, 
                f,
                file_options={"content-type": content_type}  # Changed from 'options' to 'file_options'
            )
            if hasattr(res, 'error') and res.error:
                raise Exception(f"Storage upload failed: {res.error}")

        # Create database record
        recording_data = {
            "user_id": user_id,
            "course_code": course_code,
            "title": title,
            "audio_path": file_name,
            "file_size": file_size,
            "file_type": file_ext,
            "mime_type": content_type,
            "status": "uploaded"
        }
        
        data, count = supabase.table("recordings").insert(recording_data).execute()
        
        return jsonify({
            'status': 'success',
            'recording_id': data[1][0]['id'],
            'file_name': file_name,
            'file_size': file_size,
            'file_type': file_ext,
            'mime_type': content_type,
            'message': 'File uploaded successfully'
        })

    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), 
            debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')
