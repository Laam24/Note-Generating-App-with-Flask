from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import jwt
import os
from functools import wraps
from werkzeug.utils import secure_filename
import tempfile
from datetime import datetime, timedelta
from supabase import create_client, Client
import logging

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration (using your existing values)
HF_API_TOKEN = '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'
SUPABASE_URL = 'https://jbzjvydgdyfezsxxlphv.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Impiemp2eWRnZHlmZXpzeHhscGh2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDg3Nzg3MDUsImV4cCI6MjA2NDM1NDcwNX0.HTENgOfFk3VBlCKGUm3JOjEJK4-tgR6SuWJtkCYtlwE'

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Constants
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB (increased from 10MB)
ALLOWED_EXTENSIONS = {'wav', 'mp3', 'ogg', 'flac', 'm4a', 'aac'}  # Added more formats
WHISPER_API_URL = 'https://api-inference.huggingface.co/models/openai/whisper-large'
FALCON_API_URL = 'https://api-inference.huggingface.co/models/tiiuae/falcon-7b-instruct'
HF_HEADERS = {'Authorization': f'Bearer {HF_API_TOKEN}'}

# Helper decorator for authentication (unchanged)
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

# Improved file validation with more extensions
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})

# Modified upload endpoint to handle both recorded and uploaded files
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
        return jsonify({'error': f'Invalid file type. Allowed: {", ".join(ALLOWED_EXTENSIONS)}'}), 400

    try:
        # Save file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp:
            audio_file.save(tmp)
            tmp_path = tmp.name

        # Check file size
        file_size = os.path.getsize(tmp_path)
        if file_size > MAX_FILE_SIZE:
            return jsonify({
                'error': f'File too large (max {MAX_FILE_SIZE/1024/1024}MB)',
                'max_size_mb': MAX_FILE_SIZE/1024/1024,
                'actual_size_mb': file_size/1024/1024
            }), 400

        # Generate unique filename
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        file_ext = audio_file.filename.rsplit('.', 1)[1].lower()
        file_name = f"recordings/{user_id}/{timestamp}_{secure_filename(audio_file.filename)}"

        # Upload to Supabase Storage
        with open(tmp_path, 'rb') as f:
            res = supabase.storage.from_("recordings").upload(file_name, f)
            if res.status_code != 200:
                raise Exception(f"Storage upload failed: {res.error}")

        # Create database record
        recording_data = {
            "user_id": user_id,
            "course_code": course_code,
            "title": title,
            "audio_path": file_name,
            "file_size": file_size,
            "file_type": file_ext,  # Store file extension
            "status": "uploaded"
        }
        
        data, count = supabase.table("recordings").insert(recording_data).execute()
        
        return jsonify({
            'status': 'success',
            'recording_id': data[1][0]['id'],
            'file_name': file_name,
            'file_size': file_size,
            'file_type': file_ext,
            'message': 'File uploaded successfully'
        })

    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)

# Unchanged endpoints below this line...
# [Keep all your existing /transcribe, /summarize, and /notes endpoints exactly as they are]

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), 
            debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')
