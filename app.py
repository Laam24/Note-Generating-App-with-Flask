from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import jwt
import os
from functools import wraps
from werkzeug.utils import secure_filename
import tempfile

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configuration (should use environment variables in production)
HF_API_TOKEN = os.getenv('HF_API_TOKEN', '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')
SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://jbzjvydgdyfezsxxlphv.supabase.co')
SUPABASE_API_KEY = os.getenv('SUPABASE_API_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Impiemp2eWRnZHlmZXpzeHhscGh2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDg3Nzg3MDUsImV4cCI6MjA2NDM1NDcwNX0.HTENgOfFk3VBlCKGUm3JOjEJK4-tgR6SuWJtkCYtlwE')

# Constants
SUPABASE_DB_API = f'{SUPABASE_URL}/rest/v1/notes'
HF_HEADERS = {'Authorization': f'Bearer {HF_API_TOKEN}'}
WHISPER_API_URL = 'https://api-inference.huggingface.co/models/openai/whisper-large'
FALCON_API_URL = 'https://api-inference.huggingface.co/models/tiiuae/falcon-7b-instruct'
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {'wav', 'mp3', 'ogg', 'flac'}

# Helper decorator for authentication
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        user_id = verify_token(auth_header)
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(user_id, *args, **kwargs)
    return decorated

# Improved token verification
def verify_token(auth_header):
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ")[1]
    try:
        # In production, verify the signature properly
        payload = jwt.decode(
            token,
            algorithms=["HS256"],
            options={"verify_signature": False}  # Disabled for development only
        )
        return payload.get('sub')  # user ID
    except jwt.PyJWTError:
        return None

# Helper for file validation
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/recordings', methods=['POST'])
@token_required
def upload_recording(user_id):
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
        
    audio_file = request.files['audio']
    
    if audio_file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if not allowed_file(audio_file.filename):
        return jsonify({'error': 'Invalid file type'}), 400

    # Save file temporarily
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp:
            audio_file.save(tmp)
            tmp_path = tmp.name

        # Check file size
        file_size = os.path.getsize(tmp_path)
        if file_size > MAX_FILE_SIZE:
            return jsonify({'error': 'File too large'}), 400

        # Process the file here (e.g., save to storage, transcribe, etc.)
        # For now, we'll just return a success message
        return jsonify({
            'message': 'File uploaded successfully',
            'user_id': user_id,
            'file_size': file_size
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.route('/api/transcribe', methods=['POST'])
@token_required
def transcribe_audio(user_id):
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    audio_file = request.files['audio']
    
    try:
        files = {'file': (secure_filename(audio_file.filename), audio_file.stream, audio_file.mimetype)}
        
        response = requests.post(
            WHISPER_API_URL,
            headers=HF_HEADERS,
            files=files,
            timeout=30  # Add timeout
        )

        if response.status_code != 200:
            return jsonify({
                'error': 'Transcription failed',
                'details': response.text
            }), 500

        result = response.json()
        return jsonify({
            'transcription': result.get('text', ''),
            'user_id': user_id
        })

    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'API request failed: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/summarize', methods=['POST'])
@token_required
def summarize_text(user_id):
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400

    try:
        payload = {
            "inputs": data['text'],
            "parameters": {"max_new_tokens": 150}
        }

        response = requests.post(
            FALCON_API_URL,
            headers={**HF_HEADERS, 'Content-Type': 'application/json'},
            json=payload,
            timeout=30
        )

        if response.status_code != 200:
            return jsonify({
                'error': 'Summarization failed',
                'details': response.text
            }), 500

        result = response.json()
        summary = result[0]['generated_text'] if isinstance(result, list) else result.get('summary', '')
        return jsonify({
            'summary': summary,
            'user_id': user_id
        })

    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'API request failed: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes', methods=['GET', 'POST', 'DELETE'])
@token_required
def handle_notes(user_id):
    headers = {
        "apikey": SUPABASE_API_KEY,
        "Authorization": request.headers.get("Authorization"),
        "Content-Type": "application/json"
    }

    try:
        if request.method == 'GET':
            course = request.args.get('course')
            query = f"?user_id=eq.{user_id}"
            if course:
                query += f"&course=eq.{course}"

            response = requests.get(SUPABASE_DB_API + query, headers=headers)
            return jsonify(response.json())

        elif request.method == 'POST':
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400

            note = {
                "user_id": user_id,
                "course": data.get("course"),
                "title": data.get("title"),
                "content": data.get("content")
            }
            response = requests.post(SUPABASE_DB_API, headers=headers, json=note)
            return jsonify(response.json())

        elif request.method == 'DELETE':
            note_id = request.args.get('id')
            if not note_id:
                return jsonify({'error': 'Note ID required'}), 400
            response = requests.delete(
                SUPABASE_DB_API + f"?id=eq.{note_id}", 
                headers=headers
            )
            return jsonify({'status': 'deleted'})

    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Supabase request failed: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'error': 'Invalid method'}), 405

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')
