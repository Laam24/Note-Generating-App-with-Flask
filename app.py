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

# Configuration (should use environment variables in production)
SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://jbzjvydgdyfezsxxlphv.supabase.co')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')  # No default value for security

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Constants
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {'wav', 'mp3', 'ogg', 'flac'}
WHISPER_API_URL = 'https://api-inference.huggingface.co/models/openai/whisper-large'
FALCON_API_URL = 'https://api-inference.huggingface.co/models/tiiuae/falcon-7b-instruct'
HF_HEADERS = {'Authorization': f'Bearer {HF_API_TOKEN}'}

# Helper decorator for authentication
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({'error': 'Authorization header missing or invalid'}), 401
            
        token = auth_header.split(" ")[1]
        try:
            # Verify token with Supabase
            user = supabase.auth.get_user(token)
            if not user.user:
                return jsonify({'error': 'Invalid token'}), 401
                
            kwargs['user_id'] = user.user.id
            return f(*args, **kwargs)
            
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return jsonify({'error': 'Invalid token'}), 401
            
    return decorated

# Improved file validation
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})

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
        return jsonify({'error': 'Invalid file type'}), 400

    try:
        # Save file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp:
            audio_file.save(tmp)
            tmp_path = tmp.name

        # Check file size
        file_size = os.path.getsize(tmp_path)
        if file_size > MAX_FILE_SIZE:
            return jsonify({'error': f'File too large (max {MAX_FILE_SIZE/1024/1024}MB)'}), 400

        # Upload to Supabase Storage
        file_name = f"recordings/{user_id}/{datetime.now().strftime('%Y%m%d-%H%M%S')}_{secure_filename(audio_file.filename)}"
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
            "status": "uploaded"
        }
        
        data, count = supabase.table("recordings").insert(recording_data).execute()
        
        return jsonify({
            'status': 'success',
            'recording_id': data[1][0]['id'],
            'file_name': file_name,
            'file_size': file_size,
            'message': 'File uploaded successfully'
        })

    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.route('/api/transcribe/<recording_id>', methods=['POST'])
@token_required
def transcribe_audio(user_id, recording_id):
    try:
        # Verify recording belongs to user
        recording = supabase.table("recordings")\
            .select("*")\
            .eq("id", recording_id)\
            .eq("user_id", user_id)\
            .execute()
            
        if not recording.data:
            return jsonify({'error': 'Recording not found'}), 404
            
        # Download audio from storage
        audio_path = recording.data[0]['audio_path']
        audio_bytes = supabase.storage.from_("recordings").download(audio_path)
        
        # Transcribe with Whisper
        response = requests.post(
            WHISPER_API_URL,
            headers=HF_HEADERS,
            data=audio_bytes,
            timeout=60
        )

        if response.status_code != 200:
            error_msg = f"Transcription failed: {response.text}"
            supabase.table("recordings")\
                .update({"status": "failed", "error": error_msg})\
                .eq("id", recording_id)\
                .execute()
            return jsonify({'error': error_msg}), 500

        transcription = response.json().get('text', '')
        
        # Update recording with transcription
        supabase.table("recordings")\
            .update({
                "transcription": transcription,
                "status": "transcribed",
                "transcribed_at": datetime.utcnow().isoformat()
            })\
            .eq("id", recording_id)\
            .execute()
            
        return jsonify({
            'status': 'success',
            'transcription': transcription,
            'recording_id': recording_id
        })

    except requests.exceptions.RequestException as e:
        error_msg = f"API request failed: {str(e)}"
        logger.error(error_msg)
        return jsonify({'error': error_msg}), 500
    except Exception as e:
        error_msg = str(e)
        logger.error(error_msg)
        return jsonify({'error': error_msg}), 500

@app.route('/api/summarize/<recording_id>', methods=['POST'])
@token_required
def summarize_text(user_id, recording_id):
    try:
        # Verify recording belongs to user and has transcription
        recording = supabase.table("recordings")\
            .select("*")\
            .eq("id", recording_id)\
            .eq("user_id", user_id)\
            .execute()
            
        if not recording.data:
            return jsonify({'error': 'Recording not found'}), 404
            
        transcription = recording.data[0].get('transcription')
        if not transcription:
            return jsonify({'error': 'No transcription available'}), 400

        # Summarize with Falcon
        payload = {
            "inputs": transcription,
            "parameters": {"max_new_tokens": 150}
        }

        response = requests.post(
            FALCON_API_URL,
            headers={**HF_HEADERS, 'Content-Type': 'application/json'},
            json=payload,
            timeout=60
        )

        if response.status_code != 200:
            error_msg = f"Summarization failed: {response.text}"
            supabase.table("recordings")\
                .update({"status": "failed", "error": error_msg})\
                .eq("id", recording_id)\
                .execute()
            return jsonify({'error': error_msg}), 500

        result = response.json()
        summary = result[0]['generated_text'] if isinstance(result, list) else result.get('summary', '')
        
        # Update recording with summary
        supabase.table("recordings")\
            .update({
                "summary": summary,
                "status": "summarized",
                "summarized_at": datetime.utcnow().isoformat()
            })\
            .eq("id", recording_id)\
            .execute()
            
        return jsonify({
            'status': 'success',
            'summary': summary,
            'recording_id': recording_id
        })

    except requests.exceptions.RequestException as e:
        error_msg = f"API request failed: {str(e)}"
        logger.error(error_msg)
        return jsonify({'error': error_msg}), 500
    except Exception as e:
        error_msg = str(e)
        logger.error(error_msg)
        return jsonify({'error': error_msg}), 500

@app.route('/api/notes', methods=['GET', 'POST', 'DELETE'])
@token_required
def handle_notes(user_id):
    try:
        if request.method == 'GET':
            # Get notes for current user
            course = request.args.get('course')
            query = supabase.table("notes").select("*").eq("user_id", user_id)
            
            if course:
                query = query.eq("course", course)
                
            data, count = query.execute()
            return jsonify({'notes': data[1]})
            
        elif request.method == 'POST':
            # Create new note
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
                
            required_fields = ['course', 'title', 'content']
            if not all(field in data for field in required_fields):
                return jsonify({'error': 'Missing required fields'}), 400
                
            note = {
                "user_id": user_id,
                "course": data['course'],
                "title": data['title'],
                "content": data['content']
            }
            
            data, count = supabase.table("notes").insert(note).execute()
            return jsonify({'note': data[1][0]})
            
        elif request.method == 'DELETE':
            # Delete note
            note_id = request.args.get('id')
            if not note_id:
                return jsonify({'error': 'Note ID required'}), 400
                
            # Verify note belongs to user
            note = supabase.table("notes")\
                .select("*")\
                .eq("id", note_id)\
                .eq("user_id", user_id)\
                .execute()
                
            if not note.data:
                return jsonify({'error': 'Note not found'}), 404
                
            supabase.table("notes").delete().eq("id", note_id).execute()
            return jsonify({'status': 'deleted'})
            
    except Exception as e:
        error_msg = str(e)
        logger.error(error_msg)
        return jsonify({'error': error_msg}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), 
            debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')
