from flask import Flask, request, jsonify
import requests
import jwt
import os

app = Flask(__name__)

# Set your Hugging Face and Supabase tokens (use env vars or hardcode during testing)
HF_API_TOKEN = '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'
SUPABASE_JWT_SECRET =  'W6a/msACjENgLOX0vHMJEEDJ3f+XkFNt/Xpj6u0IVjQQkftZNryVz6oSNa+UbRiix5nW1Opi6PxhZion+3o3zA==' # Get from Supabase settings
SUPABASE_URL = 'https://jbzjvydgdyfezsxxlphv.supabase.co'
SUPABASE_DB_API = f'{SUPABASE_URL}/rest/v1/notes'
SUPABASE_API_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Impiemp2eWRnZHlmZXpzeHhscGh2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDg3Nzg3MDUsImV4cCI6MjA2NDM1NDcwNX0.HTENgOfFk3VBlCKGUm3JOjEJK4-tgR6SuWJtkCYtlwE'  # Needed for DB calls

# Set common headers for Hugging Face API
HF_HEADERS = {
    'Authorization': f'Bearer {HF_API_TOKEN}'
}

# Helper: Verify Supabase JWT
def verify_token(auth_header):
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"])
        return payload['sub']  # user ID
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

# Route: Transcribe audio using Whisper
@app.route('/transcribe', methods=['POST'])
def transcribe():
    user_id = verify_token(request.headers.get("Authorization"))
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file uploaded'}), 400

    audio_file = request.files['audio']
    files = {'file': (audio_file.filename, audio_file.stream, audio_file.mimetype)}

    response = requests.post(
        'https://api-inference.huggingface.co/models/openai/whisper-large',
        headers=HF_HEADERS,
        files=files
    )

    if response.status_code != 200:
        return jsonify({'error': 'Transcription failed', 'details': response.text}), 500

    result = response.json()
    return jsonify({'transcription': result.get('text', '')})

# Route: Summarize text using Falcon
@app.route('/summarize', methods=['POST'])
def summarize():
    user_id = verify_token(request.headers.get("Authorization"))
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400

    payload = {
        "inputs": data['text'],
        "parameters": {
            "max_new_tokens": 150
        }
    }

    response = requests.post(
        'https://api-inference.huggingface.co/models/tiiuae/falcon-7b-instruct',
        headers={**HF_HEADERS, 'Content-Type': 'application/json'},
        json=payload
    )

    if response.status_code != 200:
        return jsonify({'error': 'Summarization failed', 'details': response.text}), 500

    result = response.json()
    summary = result[0]['generated_text'] if isinstance(result, list) else result.get('summary', '')
    return jsonify({'summary': summary})

# Route: GET, POST, DELETE notes
@app.route('/notes', methods=['GET', 'POST', 'DELETE'])
def notes():
    user_id = verify_token(request.headers.get("Authorization"))
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    headers = {
        "apikey": SUPABASE_API_KEY,
        "Authorization": f"Bearer {SUPABASE_API_KEY}",
        "Content-Type": "application/json"
    }

    if request.method == 'GET':
        course = request.args.get('course')
        query = f"?user_id=eq.{user_id}"
        if course:
            query += f"&course=eq.{course}"

        response = requests.get(SUPABASE_DB_API + query, headers=headers)
        return jsonify(response.json())

    elif request.method == 'POST':
        data = request.get_json()
        note = {
            "user_id": user_id,
            "course": data.get("course"),
            "title": data.get("title"),
            "content": data.get("content")
        }
        response = requests.post(SUPABASE_DB_API, headers=headers, json=note)
        return jsonify({'status': 'saved', 'response': response.json()})

    elif request.method == 'DELETE':
        note_id = request.args.get('id')
        if not note_id:
            return jsonify({'error': 'Note ID required'}), 400
        response = requests.delete(SUPABASE_DB_API + f"?id=eq.{note_id}", headers=headers)
        return jsonify({'status': 'deleted'})

    return jsonify({'error': 'Invalid method'}), 405

# Run the Flask app
if __name__ == '__main__':
    app.run(debug=True)
