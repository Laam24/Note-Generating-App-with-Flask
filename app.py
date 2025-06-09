from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from datetime import datetime
from supabase import create_client, Client
import logging
from werkzeug.utils import secure_filename
import tempfile
from mimetypes import guess_type
import whisper
from transformers import pipeline
import torch

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://jbzjvydgdyfezsxxlphv.supabase.co')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')  # Must be the service key

# Initialize Supabase client - FIXED VERSION
try:
    # Simple initialization without custom options (recommended fix)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("Supabase client initialized successfully")
except Exception as e:
    logger.error(f"Error initializing Supabase client: {e}")
    raise

# Initialize AI models
logger.info("Loading AI models...")
# Load Whisper model for transcription (choose size based on your server capacity)
# Options: tiny, base, small, medium, large
whisper_model = whisper.load_model("base")  # Good balance of speed and accuracy

# Load summarization model
summarizer = pipeline(
    "summarization",
    model="facebook/bart-large-cnn",  # Free, high-quality summarization
    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    device=0 if torch.cuda.is_available() else -1
)
logger.info("AI models loaded successfully!")

# Constants
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXTENSIONS = {
    'wav': 'audio/wav',
    'mp3': 'audio/mpeg',
    'm4a': 'audio/mp4',
    'aac': 'audio/aac',
    'ogg': 'audio/ogg',
    'flac': 'audio/flac',
    'mp4': 'video/mp4'
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
    from functools import wraps
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
            res = supabase.storage.from_("recordings").upload(
                file_name, 
                f,
                file_options={"content-type": content_type}
            )
            if hasattr(res, 'error') and res.error:
                logger.error(f"Storage upload failed: {res.error}")
                return jsonify({'error': f"Storage upload failed: {res.error}"}), 500

        # Transcribe audio using Whisper
        logger.info("Starting transcription...")
        result = whisper_model.transcribe(tmp_path)
        transcription = result["text"]
        logger.info("Transcription completed")

        # Summarize transcription
        logger.info("Starting summarization...")
        summary = ""
        if transcription.strip():
            # Split long text into chunks if needed (BART has token limits)
            max_chunk_length = 1000  # characters
            if len(transcription) > max_chunk_length:
                chunks = [transcription[i:i+max_chunk_length] 
                         for i in range(0, len(transcription), max_chunk_length)]
                summaries = []
                for chunk in chunks:
                    if len(chunk.strip()) > 50:  # Only summarize meaningful chunks
                        chunk_summary = summarizer(chunk, max_length=150, min_length=50, do_sample=False)[0]['summary_text']
                        summaries.append(chunk_summary)
                summary = " ".join(summaries)
            else:
                if len(transcription.strip()) > 50:
                    summary = summarizer(transcription, max_length=150, min_length=50, do_sample=False)[0]['summary_text']
        logger.info("Summarization completed")

        # Create database record with user_id to match RLS
        recording_data = {
            "user_id": user_id,
            "course_code": course_code,
            "title": title,
            "audio_path": file_name,
            "file_size": file_size,
            "file_type": file_ext,
            "mime_type": content_type,
            "status": "processed",
            "transcription": transcription,
            "summary": summary
        }
        
        data, count = supabase.table("recordings").insert(recording_data).execute()
        if count is None:
            logger.error("Failed to insert recording into database")
            return jsonify({'error': 'Failed to insert recording into database'}), 500
        
        return jsonify({
            'status': 'success',
            'recording_id': data[1][0]['id'],
            'file_name': file_name,
            'file_size': file_size,
            'file_type': file_ext,
            'mime_type': content_type,
            'transcription': transcription,
            'summary': summary,
            'message': 'File uploaded and processed successfully'
        })

    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': f"Upload failed: {str(e)}", 'statuscode': 400}), 400
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)

# Separate transcription endpoint (if needed)
@app.route('/api/transcribe', methods=['POST'])
@token_required
def transcribe_audio(user_id):
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
        
    audio_file = request.files['audio']
    
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            audio_file.save(tmp)
            tmp_path = tmp.name

        result = whisper_model.transcribe(tmp_path)
        transcription = result["text"]
        
        return jsonify({
            'status': 'success',
            'transcription': transcription
        })
        
    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        return jsonify({'error': f"Transcription failed: {str(e)}"}), 500
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)

# Separate summarization endpoint
@app.route('/api/summarize', methods=['POST'])
@token_required
def summarize_text(user_id):
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    
    text = data['text'].strip()
    if not text:
        return jsonify({'error': 'Empty text provided'}), 400
    
    try:
        # Handle long text by chunking
        max_chunk_length = 1000
        if len(text) > max_chunk_length:
            chunks = [text[i:i+max_chunk_length] 
                     for i in range(0, len(text), max_chunk_length)]
            summaries = []
            for chunk in chunks:
                if len(chunk.strip()) > 50:
                    chunk_summary = summarizer(chunk, max_length=150, min_length=50, do_sample=False)[0]['summary_text']
                    summaries.append(chunk_summary)
            summary = " ".join(summaries)
        else:
            if len(text) > 50:
                summary = summarizer(text, max_length=150, min_length=50, do_sample=False)[0]['summary_text']
            else:
                summary = text  # Return original if too short to summarize
        
        return jsonify({
            'status': 'success',
            'summary': summary
        })
        
    except Exception as e:
        logger.error(f"Summarization error: {str(e)}")
        return jsonify({'error': f"Summarization failed: {str(e)}"}), 500

# Get recordings/notes endpoint
@app.route('/api/recordings', methods=['GET'])
@token_required
def get_recordings(user_id):
    try:
        data, count = supabase.table("recordings").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
        
        recordings = []
        for record in data[1]:
            recordings.append({
                'id': record['id'],
                'course': record['course_code'],
                'title': record['title'],
                'content': f"Transcription:\n{record.get('transcription', '')}\n\nSummary:\n{record.get('summary', '')}",
                'created_at': record['created_at'],
                'status': record['status']
            })
        
        return jsonify(recordings)
        
    except Exception as e:
        logger.error(f"Get recordings error: {str(e)}")
        return jsonify({'error': f"Failed to fetch recordings: {str(e)}"}), 500

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

# Root endpoint
@app.route('/', methods=['GET'])
def root():
    return jsonify({
        'message': 'Note Generating App API',
        'version': '1.0.0',
        'endpoints': [
            '/health',
            '/api/recordings (GET, POST)',
            '/api/transcribe',
            '/api/summarize'
        ]
    })

if __name__ == '__main__':
    # Ensure we bind to the correct port for Render
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
