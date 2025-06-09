import os
import tempfile
import whisper
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase_py import create_client, SupabaseClient
from transformers import pipeline
import torch

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Load environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Initialize Supabase client
try:
    supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
    app.logger.info("Supabase client initialized successfully")
except Exception as e:
    app.logger.error(f"Error initializing Supabase client: {e}")
    raise

# Load Whisper tiny model (to prevent OOM)
app.logger.info("Loading Whisper model...")
whisper_model = whisper.load_model("tiny")
app.logger.info("Whisper model loaded.")

# Load distilled summarization model
app.logger.info("Loading summarization model...")
summarizer = pipeline(
    "summarization",
    model="sshleifer/distilbart-cnn-12-6",
    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    device=0 if torch.cuda.is_available() else -1
)
app.logger.info("Summarization model loaded.")

@app.route("/transcribe", methods=["POST"])
def transcribe_and_summarize():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]

    # Save uploaded audio to a temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        audio_path = tmp.name
        audio_file.save(audio_path)

    try:
        app.logger.info("Transcribing audio...")
        result = whisper_model.transcribe(audio_path)
        transcription = result["text"]

        app.logger.info("Summarizing transcription...")
        summary = summarizer(transcription, max_length=100, min_length=30, do_sample=False)[0]["summary_text"]

        # Optionally store in Supabase
        user_id = request.form.get("user_id", "anonymous")
        supabase.table("transcriptions").insert({
            "user_id": user_id,
            "transcription": transcription,
            "summary": summary
        }).execute()

        return jsonify({"transcription": transcription, "summary": summary})

    except Exception as e:
        app.logger.error(f"Error during processing: {e}")
        return jsonify({"error": "Processing failed"}), 500

    finally:
        os.remove(audio_path)

@app.route("/", methods=["GET"])
def health_check():
    return "OK", 200

if __name__ == "__main__":
    app.run(debug=True)
