import os
import tempfile
import time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import jwt
import json

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configuration
SUPABASE_URL = "https://jbzjvydgdyfezsxxlphv.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Impiemp2eWRnZHlmZXpzeHhscGh2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDg3Nzg3MDUsImV4cCI6MjA2NDM1NDcwNX0.HTENgOfFk3VBlCKGUm3JOjEJK4-tgR6SuWJtkCYtlwE"
ASSEMBLYAI_API_KEY = "74f837e474a64d6c809a087992bb498b"

def get_user_id_from_token(token):
    """Extract user ID from JWT token"""
    try:
        # Remove 'Bearer ' prefix if present
        if token.startswith('Bearer '):
            token = token[7:]
        
        # Decode JWT token (we don't verify signature for simplicity)
        decoded = jwt.decode(token, options={"verify_signature": False})
        return decoded.get('sub')
    except Exception as e:
        app.logger.error(f"Error decoding token: {e}")
        return None

def transcribe_with_assemblyai(audio_file_path):
    """Transcribe audio using AssemblyAI API"""
    try:
        # Step 1: Upload audio file
        upload_url = "https://api.assemblyai.com/v2/upload"
        headers = {"authorization": ASSEMBLYAI_API_KEY}
        
        with open(audio_file_path, 'rb') as f:
            response = requests.post(upload_url, headers=headers, files={'file': f})
        
        if response.status_code != 200:
            raise Exception(f"Upload failed: {response.text}")
        
        upload_response = response.json()
        audio_url = upload_response['upload_url']
        
        # Step 2: Request transcription
        transcript_url = "https://api.assemblyai.com/v2/transcript"
        transcript_request = {
            "audio_url": audio_url,
            "auto_chapters": True,  # This helps with summarization
            "summarization": True,
            "summary_model": "informative",
            "summary_type": "bullets"
        }
        
        response = requests.post(transcript_url, json=transcript_request, headers=headers)
        
        if response.status_code != 200:
            raise Exception(f"Transcription request failed: {response.text}")
        
        transcript_response = response.json()
        transcript_id = transcript_response['id']
        
        # Step 3: Poll for completion
        polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        
        while True:
            response = requests.get(polling_url, headers=headers)
            result = response.json()
            
            if result['status'] == 'completed':
                return {
                    'transcription': result.get('text', ''),
                    'summary': result.get('summary', 'No summary available'),
                    'chapters': result.get('chapters', [])
                }
            elif result['status'] == 'error':
                raise Exception(f"Transcription failed: {result.get('error', 'Unknown error')}")
            
            # Wait 3 seconds before polling again
            time.sleep(3)
            
    except Exception as e:
        app.logger.error(f"AssemblyAI transcription error: {e}")
        raise

def create_summary_from_text(text):
    """Create a summary using AssemblyAI's LeMUR for additional processing"""
    try:
        headers = {
            "authorization": ASSEMBLYAI_API_KEY,
            "content-type": "application/json"
        }
        
        # Use LeMUR for better summarization
        lemur_url = "https://api.assemblyai.com/lemur/v3/generate/summary"
        
        data = {
            "transcript_ids": [],  # We'll use input_text instead
            "input_text": text,
            "answer_format": "Key points and main concepts from the lecture",
            "context": "This is a lecture transcript that needs to be summarized into study notes"
        }
        
        response = requests.post(lemur_url, json=data, headers=headers)
        
        if response.status_code == 200:
            result = response.json()
            return result.get('response', text[:500] + "...")
        else:
            # Fallback: create simple summary
            sentences = text.split('.')
            if len(sentences) > 3:
                return '. '.join(sentences[:3]) + '...'
            return text
            
    except Exception as e:
        app.logger.error(f"Summary creation error: {e}")
        # Fallback summary
        sentences = text.split('.')
        if len(sentences) > 3:
            return '. '.join(sentences[:3]) + '...'
        return text

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy", "message": "Smart Lecture Notes API is running"}), 200

@app.route("/transcribe", methods=["POST"])
def transcribe_audio():
    """Transcribe uploaded audio file"""
    try:
        # Check if audio file is provided
        if "audio" not in request.files:
            return jsonify({"error": "No audio file provided"}), 400
        
        audio_file = request.files["audio"]
        if audio_file.filename == '':
            return jsonify({"error": "No audio file selected"}), 400
        
        # Get user authentication
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({"error": "Authorization header required"}), 401
        
        user_id = get_user_id_from_token(auth_header)
        if not user_id:
            return jsonify({"error": "Invalid token"}), 401
        
        # Save uploaded audio to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp_file:
            audio_path = tmp_file.name
            audio_file.save(audio_path)
        
        try:
            # Transcribe using AssemblyAI
            app.logger.info("Starting transcription with AssemblyAI...")
            result = transcribe_with_assemblyai(audio_path)
            
            app.logger.info("Transcription completed successfully")
            return jsonify({
                "transcription": result['transcription'],
                "summary": result['summary'],
                "chapters": result.get('chapters', [])
            })
            
        finally:
            # Clean up temporary file
            try:
                os.unlink(audio_path)
            except:
                pass
                
    except Exception as e:
        app.logger.error(f"Transcription endpoint error: {e}")
        return jsonify({"error": f"Transcription failed: {str(e)}"}), 500

@app.route("/summarize", methods=["POST"])
def summarize_text():
    """Summarize provided text"""
    try:
        # Get user authentication
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({"error": "Authorization header required"}), 401
        
        user_id = get_user_id_from_token(auth_header)
        if not user_id:
            return jsonify({"error": "Invalid token"}), 401
        
        # Get text from request
        data = request.get_json()
        if not data or 'text' not in data:
            return jsonify({"error": "No text provided"}), 400
        
        text = data['text']
        if not text.strip():
            return jsonify({"error": "Empty text provided"}), 400
        
        # Create summary
        app.logger.info("Creating summary...")
        summary = create_summary_from_text(text)
        
        return jsonify({"summary": summary})
        
    except Exception as e:
        app.logger.error(f"Summarization endpoint error: {e}")
        return jsonify({"error": f"Summarization failed: {str(e)}"}), 500

@app.route("/notes", methods=["GET", "POST", "DELETE"])
def handle_notes():
    """Handle notes CRUD operations"""
    try:
        # Get user authentication
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({"error": "Authorization header required"}), 401
        
        user_id = get_user_id_from_token(auth_header)
        if not user_id:
            return jsonify({"error": "Invalid token"}), 401
        
        # Supabase headers
        supabase_headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
        
        if request.method == "GET":
            # Get all notes for user
            url = f"{SUPABASE_URL}/rest/v1/notes?user_id=eq.{user_id}&select=*"
            response = requests.get(url, headers=supabase_headers)
            
            if response.status_code == 200:
                notes = response.json()
                return jsonify(notes)
            else:
                return jsonify({"error": "Failed to fetch notes"}), 500
        
        elif request.method == "POST":
            # Create new note
            data = request.get_json()
            if not data:
                return jsonify({"error": "No data provided"}), 400
            
            note_data = {
                "user_id": user_id,
                "course": data.get('course', ''),
                "title": data.get('title', ''),
                "content": data.get('content', ''),
                "created_at": "now()"
            }
            
            url = f"{SUPABASE_URL}/rest/v1/notes"
            response = requests.post(url, json=note_data, headers=supabase_headers)
            
            if response.status_code == 201:
                return jsonify({"message": "Note created successfully"})
            else:
                app.logger.error(f"Supabase error: {response.text}")
                return jsonify({"error": "Failed to create note"}), 500
        
        elif request.method == "DELETE":
            # Delete note
            note_id = request.args.get('id')
            if not note_id:
                return jsonify({"error": "Note ID required"}), 400
            
            url = f"{SUPABASE_URL}/rest/v1/notes?id=eq.{note_id}&user_id=eq.{user_id}"
            response = requests.delete(url, headers=supabase_headers)
            
            if response.status_code == 204:
                return jsonify({"message": "Note deleted successfully"})
            else:
                return jsonify({"error": "Failed to delete note"}), 500
                
    except Exception as e:
        app.logger.error(f"Notes endpoint error: {e}")
        return jsonify({"error": f"Notes operation failed: {str(e)}"}), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
