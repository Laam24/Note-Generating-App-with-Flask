from flask import Flask, request, jsonify
import requests
import jwt
from supabase import create_client, Client
import os
from werkzeug.utils import secure_filename
import tempfile

app = Flask(__name__)

# Configuration
SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://jbzjvydgdyfezsxxlphv.supabase.co')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Impiemp2eWRnZHlmZXpzeHhscGh2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDg3Nzg3MDUsImV4cCI6MjA2NDM1NDcwNX0.HTENgOfFk3VBlCKGUm3JOjEJK4-tgR6SuWJtkCYtlwE')
HF_API_TOKEN = os.getenv('HF_API_TOKEN', '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')

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Helper to verify JWT
def verify_jwt(auth_header):
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ")[1]
    try:
        # Get the user from Supabase to verify the token
        user = supabase.auth.get_user(token)
        return user.user.id if user else None
    except Exception as e:
        print(f"JWT verification error: {e}")
        return None

@app.route('/api/recordings', methods=['POST'])
def upload_recording():
    # Verify user
    user_id = verify_jwt(request.headers.get("Authorization"))
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    # Get form data
    course_code = request.form.get("course_code")
    title = request.form.get("title")
    audio_file = request.files.get("audio")
    
    if not all([course_code, title, audio_file]):
        return jsonify({"error": "Missing required fields"}), 400
    
    try:
        # Save file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            audio_file.save(tmp)
            tmp_path = tmp.name
        
        # Upload to Supabase Storage
        file_name = f"{user_id}/{secure_filename(audio_file.filename)}"
        with open(tmp_path, 'rb') as f:
            res = supabase.storage.from_("recordings").upload(file_name, f)
        
        # Get public URL
        audio_url = supabase.storage.from_("recordings").get_public_url(file_name)
        
        # Save to database
        recording_data = {
            "user_id": user_id,
            "course_code": course_code,
            "title": title,
            "audio_path": file_name
        }
        data, _ = supabase.table("recordings").insert(recording_data).execute()
        recording_id = data[1][0]['id']
        
        return jsonify({
            "id": recording_id,
            "audio_url": audio_url,
            "message": "Recording uploaded successfully"
        }), 201
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.route('/api/transcribe/<recording_id>', methods=['POST'])
def transcribe_recording(recording_id):
    user_id = verify_jwt(request.headers.get("Authorization"))
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        # Get recording from database
        recording = supabase.table("recordings").select("*").eq("id", recording_id).eq("user_id", user_id).execute()
        if not recording.data:
            return jsonify({"error": "Recording not found"}), 404
        
        # Get audio from storage
        audio_path = recording.data[0]['audio_path']
        audio_bytes = supabase.storage.from_("recordings").download(audio_path)
        
        # Transcribe with Whisper
        headers = {"Authorization": f"Bearer {HF_API_TOKEN}"}
        response = requests.post(
            "https://api-inference.huggingface.co/models/openai/whisper-large",
            headers=headers,
            data=audio_bytes
        )
        
        if response.status_code != 200:
            return jsonify({"error": "Transcription failed", "details": response.text}), 500
        
        transcription = response.json().get("text", "")
        
        # Update recording with transcription
        supabase.table("recordings").update({"transcription": transcription}).eq("id", recording_id).execute()
        
        return jsonify({"transcription": transcription})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/summarize/<recording_id>', methods=['POST'])
def summarize_recording(recording_id):
    user_id = verify_jwt(request.headers.get("Authorization"))
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        # Get recording with transcription
        recording = supabase.table("recordings").select("*").eq("id", recording_id).eq("user_id", user_id).execute()
        if not recording.data or not recording.data[0]['transcription']:
            return jsonify({"error": "No transcription available"}), 400
        
        transcription = recording.data[0]['transcription']
        
        # Summarize with Falcon
        headers = {
            "Authorization": f"Bearer {HF_API_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "inputs": transcription,
            "parameters": {"max_new_tokens": 150}
        }
        
        response = requests.post(
            "https://api-inference.huggingface.co/models/tiiuae/falcon-7b-instruct",
            headers=headers,
            json=payload
        )
        
        if response.status_code != 200:
            return jsonify({"error": "Summarization failed", "details": response.text}), 500
        
        summary = response.json()[0]['generated_text'] if isinstance(response.json(), list) else ""
        
        # Update recording with summary
        supabase.table("recordings").update({"summary": summary}).eq("id", recording_id).execute()
        
        return jsonify({"summary": summary})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
