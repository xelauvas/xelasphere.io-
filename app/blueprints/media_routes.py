"""Media generation routes blueprint.

Routes:
- POST /generate-image - Generate image using Pollinations
- GET /check-generation-status - Check image generation status
- POST /tts/groq - Text-to-speech using Groq
- POST /speech-to-text/groq - Speech-to-text using Groq Whisper
"""

import logging

import requests
from flask import Blueprint, current_app, g, jsonify, request, session

from app.config import Config, db
from app.limiter import limiter, csrf_required
from app.blueprints.shared import login_required, error_handler

logger = logging.getLogger(__name__)

media_bp = Blueprint('media', __name__)

# Supported audio formats for TTS
SUPPORTED_TTS_FORMATS = {
    'wav': 'audio/wav',
    'mp3': 'audio/mpeg',
    'opus': 'audio/opus',
    'flac': 'audio/flac',
    'aac': 'audio/aac',
    'pcm': 'audio/L16'
}


@media_bp.route('/generate-image', methods=['POST'])
@limiter.limit("6 per minute")
@csrf_required
@error_handler
@login_required
def generate_image_endpoint():
    """
    Return a Pollinations image URL for the given prompt using a deterministic seed.
    Avoids local heavy pipelines/torch.
    """
    from app.image_generator import generate_image_result

    user_id = session['user']['uid']
    data = request.get_json() or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'error': 'A text prompt is required to generate an image'}), 400

    try:
        result = generate_image_result(user_id, prompt)
        if not result:
            return jsonify({'error': 'Failed to generate image URL due to an unexpected error'}), 500
        return jsonify({
            'message': 'Image URL generated successfully',
            'image_url': result['image_url'],
            'local_path': result.get('local_path'),
            'seed': result['seed'],
            'prompt': result['prompt'],
            'provider': result.get('provider')
        }), 200
    except Exception as e:
        logger.exception(f"Unexpected error generating image URL: {str(e)}")
        return jsonify({'error': 'Failed to generate image URL due to an unexpected error'}), 500


@media_bp.route('/check-generation-status')
@error_handler
@login_required
def check_generation_status():
    """Check the status of an image generation request."""
    user_id = session['user']['uid']
    request_id = request.args.get('id')

    if not request_id:
        return jsonify({'error': 'No request ID provided'}), 400

    try:
        # Query the database to check the status
        status_ref = db.collection('users').document(user_id).collection('image_requests').document(request_id)
        status_doc = status_ref.get()

        if not status_doc.exists:
            return jsonify({'error': 'Image request not found'}), 404

        status_data = status_doc.to_dict()
        return jsonify({
            'status': status_data.get('status', 'unknown'),
            'progress': status_data.get('progress', 0),
            'image_path': status_data.get('image_path', None),
            'created_at': status_data.get('created_at', None),
            'updated_at': status_data.get('updated_at', None)
        }), 200
    except Exception as e:
        logger.exception(f"Error checking image generation status: {str(e)}")
        return jsonify({'error': 'Failed to check generation status'}), 500


import re as _re


def _pick_voice_for_content(text: str) -> str:
    """Pick the best Gemini voice based on what the text is about."""
    t = text.lower()
    length = len(text)

    # Code/technical — precise, authoritative
    if any(x in t for x in ['```', 'function', 'def ', 'class ', 'import ', 'const ', 'var ', 'error', 'debug', 'api', 'http']):
        return 'Charon'

    # Reasoning/analysis — calm, measured
    if any(x in t for x in ['because', 'therefore', 'however', 'in conclusion', 'step 1', 'first,', 'the reason', 'analysis', 'comparing']):
        return 'Orus'

    # Creative/stories — expressive
    if any(x in t for x in ['once upon', 'story', 'imagine', 'poem', 'chapter', 'narrator', 'fiction']):
        return 'Zephyr'

    # Lists/instructions — crisp, clear
    if _re.search(r'^\s*[\d\-\*•]', text, _re.MULTILINE) and length > 300:
        return 'Kore'

    # Short snappy answers
    if length < 150:
        return 'Puck'

    # Default: warm conversational
    return 'Aoede'


# Gemini TTS voice mapping (Groq voice → Gemini voice)
GEMINI_VOICES = {
    'autumn': 'Kore',
    'alloy': 'Puck',
    'echo': 'Charon',
    'fable': 'Fenrir',
    'onyx': 'Orus',
    'nova': 'Zephyr',
    'shimmer': 'Aoede',
    'coral': 'Leda',
}

# Reverse: Gemini voice → Groq voice (for fallback)
GROQ_VOICES = {v: k for k, v in GEMINI_VOICES.items()}
# Groq only supports: autumn diana hannah austin daniel troy
GROQ_VOICE_FALLBACK = {
    'Kore': 'hannah', 'Puck': 'austin', 'Charon': 'daniel',
    'Fenrir': 'troy', 'Orus': 'daniel', 'Zephyr': 'diana',
    'Aoede': 'autumn', 'Leda': 'hannah',
}


def _generate_gemini_tts(text, voice='Kore'):
    """Generate TTS using Gemini. Returns (audio_bytes, mime_type) or None."""
    import base64
    import struct
    import io
    import wave

    api_key = Config.GEMINI_API_KEY
    if not api_key:
        return None

    # Cap text length — long text causes timeouts
    if len(text) > 3000:
        text = text[:3000]

    gemini_voice = GEMINI_VOICES.get(voice, voice)
    # Capitalize first letter for Gemini
    if gemini_voice and gemini_voice[0].islower():
        gemini_voice = gemini_voice[0].upper() + gemini_voice[1:]

    try:
        import time as _time
        resp = None
        for _attempt in range(2):
            resp = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent",
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": text}]}],
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {"voiceName": gemini_voice}
                            }
                        }
                    }
                },
                timeout=60,
            )
            if resp.status_code == 200:
                break
            if resp.status_code == 500 and _attempt == 0:
                logger.info("Gemini TTS 500, retrying...")
                _time.sleep(0.5)
                continue
            logger.warning(f"Gemini TTS returned {resp.status_code}: {resp.text[:200]}")
            return None

        data = resp.json()
        parts = data.get('candidates', [{}])[0].get('content', {}).get('parts', [])
        for part in parts:
            inline = part.get('inlineData', {})
            if inline.get('data'):
                pcm_bytes = base64.b64decode(inline['data'])
                # Convert raw PCM (L16, 24kHz, mono) to WAV
                wav_buf = io.BytesIO()
                with wave.open(wav_buf, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)  # 16-bit
                    wf.setframerate(24000)
                    wf.writeframes(pcm_bytes)
                return wav_buf.getvalue(), 'audio/wav'

        return None
    except Exception as e:
        logger.warning(f"Gemini TTS failed: {e}")
        return None


@media_bp.route('/tts/groq', methods=['POST'])
@limiter.limit("10 per minute")
@csrf_required
@error_handler
@login_required
def generate_groq_tts():
    """Generate text-to-speech: Gemini (primary, free, no length limit) → Groq (fallback)."""
    payload = request.get_json(silent=True) or {}
    text = (payload.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'Text input is required.'}), 400

    voice = (payload.get('voice') or 'autumn').strip() or 'autumn'

    # ── Dynamic voice based on content ──
    if voice == 'autumn' or voice == 'auto':
        voice = _pick_voice_for_content(text)
        logger.info(f"TTS: dynamic voice → {voice}")

    # ── Try Gemini first (free, no char limit) ──
    gemini_result = _generate_gemini_tts(text, voice)
    if gemini_result:
        audio_bytes, mime_type = gemini_result
        logger.info(f"TTS: Gemini success, {len(audio_bytes)} bytes")
        from flask import Response
        return Response(audio_bytes, mimetype=mime_type, headers={
            'Content-Disposition': 'inline; filename="speech.wav"'
        })

    # ── Fallback: Groq Orpheus (200 char limit) ──
    logger.info("TTS: Gemini failed, falling back to Groq")
    if len(text) > 200:
        text = text[:200]

    requested_format = (payload.get('format') or 'wav').lower()
    if requested_format not in SUPPORTED_TTS_FORMATS:
        requested_format = 'wav'
    mime_type = SUPPORTED_TTS_FORMATS[requested_format]

    api_keys = getattr(g, 'api_keys', {}) if hasattr(g, 'api_keys') else {}
    user_override = api_keys.get('groq') if isinstance(api_keys, dict) else None
    api_key = user_override or Config.GROQ_API_KEY
    if not api_key:
        return jsonify({'error': 'TTS provider not available.'}), 502

    # Map Gemini voice name to Groq-compatible voice
    groq_voice = GROQ_VOICE_FALLBACK.get(voice, 'autumn')
    logger.info(f"TTS: Groq voice mapping {voice} → {groq_voice}")

    groq_endpoint = "https://api.groq.com/openai/v1/audio/speech"
    body = {
        "model": "canopylabs/orpheus-v1-english",
        "input": text,
        "voice": groq_voice,
        "response_format": requested_format
    }

    try:
        response = requests.post(
            groq_endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": mime_type
            },
            json=body,
            timeout=60
        )
    except requests.RequestException as exc:
        logger.error(f"TTS request to Groq failed: {exc}")
        return jsonify({'error': 'Failed to contact text-to-speech provider.'}), 502

    if response.status_code != 200:
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = {'error': response.text}
        logger.error(f"Groq TTS error ({response.status_code}): {error_payload}")
        return jsonify({'error': error_payload.get('error', 'Groq TTS request failed.')}), response.status_code

    audio_bytes = response.content
    if not audio_bytes:
        logger.error("Groq TTS returned an empty audio payload.")
        return jsonify({'error': 'Received empty audio payload from Groq.'}), 502

    audio_length = len(audio_bytes)
    header = audio_bytes[:12]
    logger.info(f"Groq TTS success: {audio_length} bytes, header={header!r}")

    # Repair RIFF chunk sizes when provider streams placeholder values
    repaired_bytes = bytearray(audio_bytes)
    if audio_length >= 8:
        riff_size = audio_length - 8
        repaired_bytes[4:8] = riff_size.to_bytes(4, byteorder='little', signed=False)
    data_index = audio_bytes.find(b'data')
    if data_index != -1 and (data_index + 8) <= audio_length:
        data_size = audio_length - (data_index + 8)
        repaired_bytes[data_index + 4:data_index + 8] = data_size.to_bytes(4, byteorder='little', signed=False)
    audio_bytes = bytes(repaired_bytes)
    logger.info(f"Groq TTS repaired header: {audio_bytes[:12]!r}")

    response = current_app.response_class(
        audio_bytes,
        mimetype=mime_type
    )
    response.headers['X-Audio-Format'] = requested_format
    return response


@media_bp.route('/speech-to-text/groq', methods=['POST'])
@limiter.limit("10 per minute")
@csrf_required
@error_handler
@login_required
def groq_speech_to_text():
    """
    Transcribe audio to text using Groq's Whisper API.
    Accepts multipart form data with an 'audio' file.
    Uses whisper-large-v3-turbo with fallback to whisper-large-v3.
    """
    audio_file = request.files.get('audio')
    if not audio_file or not audio_file.filename:
        return jsonify({'error': 'No audio file provided.'}), 400

    # Get API key with user override support
    api_keys = getattr(g, 'api_keys', {}) if hasattr(g, 'api_keys') else {}
    user_override = api_keys.get('groq') if isinstance(api_keys, dict) else None
    api_key = user_override or Config.GROQ_API_KEY
    if not api_key:
        return jsonify({'error': 'Groq API key is not configured.'}), 400

    groq_endpoint = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    # Models to try in order
    models = ['whisper-large-v3-turbo', 'whisper-large-v3']

    # Read audio data once
    audio_data = audio_file.read()
    filename = audio_file.filename or 'audio.webm'
    content_type = audio_file.content_type or 'audio/webm'

    last_error = None
    for model in models:
        try:
            files = {
                'file': (filename, audio_data, content_type)
            }
            data = {
                'model': model
            }

            response = requests.post(
                groq_endpoint,
                headers=headers,
                files=files,
                data=data,
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                text = result.get('text', '').strip()
                logger.info(f"Groq STT success with {model}: {len(text)} chars")
                return jsonify({
                    'text': text,
                    'language': result.get('language'),
                    'model': model,
                    'status': 'success'
                }), 200
            else:
                try:
                    error_payload = response.json()
                    # Handle nested error structure
                    if 'error' in error_payload and isinstance(error_payload['error'], dict):
                        last_error = error_payload['error'].get('message', str(error_payload['error']))
                    else:
                        last_error = error_payload.get('error', error_payload.get('message', response.text))
                except ValueError:
                    last_error = response.text
                logger.warning(f"Groq STT with {model} failed ({response.status_code}): {last_error}")
                logger.warning(f"Audio info: filename={filename}, content_type={content_type}, size={len(audio_data)} bytes")
                # Continue to next model

        except requests.RequestException as exc:
            last_error = str(exc)
            logger.warning(f"Groq STT request with {model} failed: {exc}")
            # Continue to next model

    # All models failed
    logger.error(f"All Groq STT models failed. Last error: {last_error}")
    return jsonify({
        'error': last_error or 'Transcription failed with all available models.',
        'audio_size': len(audio_data),
        'content_type': content_type
    }), 400
