"""Media generation routes blueprint.

Routes:
- POST /generate-image - Generate image using Pollinations
- GET /check-generation-status - Check image generation status
- POST /tts/groq - Text-to-speech (Kokoro → Gemini → Groq)
- POST /speech-to-text/groq - Speech-to-text using Groq Whisper
"""

import logging

import requests
from flask import Blueprint, current_app, g, jsonify, request, session

from app.config import Config, db
from app.limiter import limiter, csrf_required
from app.blueprints.shared import login_required, error_handler

logger = logging.getLogger(__name__)

# ── Kokoro TTS via sidecar (127.0.0.1:8001) ──
KOKORO_SIDECAR_URL = 'http://127.0.0.1:8001/synthesize'
KOKORO_SIDECAR_TIMEOUT = 45  # seconds — fail fast, let Gemini fallback handle it

# Language code mapping: ISO 639-1 → Kokoro lang_code
KOKORO_LANG_MAP = {
    'en': 'a', 'fr': 'f', 'ja': 'j', 'zh': 'z',
    'es': 'e', 'hi': 'h', 'it': 'i', 'pt': 'p',
}

# Default voice per language (used when voice='auto')
KOKORO_LANG_DEFAULT_VOICE = {
    'a': 'af_heart',     # American English
    'b': 'bf_emma',      # British English
    'f': 'ff_siwis',     # French
    'j': 'jf_alpha',     # Japanese
    'z': 'zf_xiaobei',   # Mandarin
    'e': 'ef_dora',      # Spanish
    'h': 'hf_alpha',     # Hindi
    'i': 'if_sara',      # Italian
    'p': 'pf_dora',      # Portuguese
}


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
    """Pick the best Gemini voice based on content type and emotional tone."""
    t = text.lower()
    length = len(text)

    # ── Emotional / sentiment detection (check first — overrides topic) ──

    # Empathy / comfort — gentle, caring
    if any(x in t for x in ["i'm sorry", 'sorry to hear', 'that must be', 'i understand how',
                             'take care', 'hang in there', 'condolence', 'my heart goes out']):
        return 'Vindemiatrix'

    # Excitement / celebration
    if any(x in t for x in ['congratulations', 'amazing!', 'awesome!', 'fantastic!', 'incredible',
                             'great news', "that's wonderful", 'well done', 'bravo']):
        return 'Fenrir'

    # Humor / playful
    if any(x in t for x in ['haha', 'lol', 'funny', 'joke', 'just kidding', 'plot twist', 'no pun intended']):
        return 'Puck'

    # Warning / caution — serious tone
    if any(x in t for x in ['warning', 'caution', 'danger', 'critical', 'do not', "don't ever",
                             'be careful', 'important:', 'security risk', 'vulnerability']):
        return 'Alnilam'

    # ── Topic-based detection ──

    # Code / technical — precise, informative
    if any(x in t for x in ['```', 'function', 'def ', 'class ', 'import ', 'const ', 'var ',
                             'error', 'debug', 'api', 'http', 'database', 'server', 'deploy',
                             'repository', 'commit', 'merge', 'branch', 'compile', 'runtime']):
        return 'Charon'

    # Math / science — clear, knowledgeable
    if any(x in t for x in ['equation', 'formula', 'theorem', 'calculate', 'probability',
                             'hypothesis', 'experiment', 'molecule', 'algorithm', 'derivative']):
        return 'Sadaltager'

    # Instructions / how-to — firm, clear (check before reasoning to avoid overlap on "first,")
    if any(x in t for x in ['step 1', 'step 2', 'follow these', 'instructions', 'tutorial',
                             'how to ', 'make sure to', 'you need to']):
        return 'Kore'
    if _re.search(r'(?:first|next|then|finally),\s', t) and any(x in t for x in ['next,', 'then,', 'finally,']):
        return 'Kore'

    # Gentle explanation / teaching — patient, easy-going
    if any(x in t for x in ['let me explain', 'think of it like', 'in simple terms',
                             'basically', 'for example', 'the idea is', 'to put it simply',
                             'what this means', 'in other words']):
        return 'Callirrhoe'

    # Creative / stories — expressive, bright
    if any(x in t for x in ['once upon', 'story', 'imagine', 'poem', 'chapter', 'narrator',
                             'fiction', 'character', 'adventure', 'fairy tale', 'legend']):
        return 'Zephyr'

    # Motivational / inspirational
    if any(x in t for x in ['you can do', 'believe in', 'never give up', 'keep going',
                             'inspire', 'dream big', 'potential', 'courage', 'strength within']):
        return 'Sadachbia'

    # History / storytelling — mature, gravelly
    if any(x in t for x in ['century', 'historical', 'ancient', 'era', 'civilization',
                             'dynasty', 'world war', 'revolution', 'empire']):
        return 'Gacrux'

    # News / reporting — even, professional
    if any(x in t for x in ['breaking', 'reported', 'according to', 'officials said',
                             'announcement', 'press release', 'update:', 'sources say']):
        return 'Rasalgethi'

    # Intimate / personal — soft, breathy
    if any(x in t for x in ['between us', 'personally', 'secret', 'confession',
                             'quiet moment', 'just you', 'private']):
        return 'Enceladus'

    # Reasoning / analysis — calm, measured
    if any(x in t for x in ['because', 'therefore', 'however', 'in conclusion',
                             'the reason', 'analysis', 'comparing', 'on the other hand',
                             'pros and cons', 'trade-off', 'it depends', 'nuance']):
        return 'Orus'

    # Lists — crisp, organized
    if _re.search(r'^\s*[\d\-\*•]', text, _re.MULTILINE) and length > 300:
        return 'Kore'

    # ── Length-based fallbacks ──

    # Very short — snappy
    if length < 80:
        return 'Puck'

    # Short — friendly
    if length < 250:
        return 'Achird'

    # Medium conversational
    if length < 600:
        return 'Sulafat'

    # Long-form — smooth, easy to listen to
    return 'Algieba'


# ── Audio Profiles for expressive TTS ──
# Full director-style prompts: character, scene, style, pacing, accent guidance.
# Gemini TTS treats these as performance direction for a virtual voice actor.

VOICE_STYLE_DIRECTIONS = {

    # ── Conversational / default ──

    'Sulafat': """# AUDIO PROFILE: Helpful Assistant
## THE SCENE
A bright, quiet workspace. A knowledgeable assistant is answering questions naturally — warm but professional, like a colleague you trust.
### DIRECTOR'S NOTES
Style: Warm, natural, conversational. Friendly but not overly casual. Confident without being stiff. The listener should feel helped, not coddled.
Pacing: Natural cadence. Not rushed, not dragging. Let important words breathe. Slight pauses before key points for emphasis.
Tone: Approachable, grounded, competent. Warm enough to be pleasant, professional enough for any topic.
### TRANSCRIPT""",

    'Achird': """# AUDIO PROFILE: Friendly Neighbor
## THE SCENE
Standing at the front door, casual catch-up. Easy, informal, no pressure.
### DIRECTOR'S NOTES
Style: Friendly and approachable. Casual but clear. Like bumping into someone you like and having a quick, genuine chat.
Pacing: Natural conversational speed. Not overthinking, just talking.
### TRANSCRIPT""",

    # ── Technical / code ──

    'Charon': """# AUDIO PROFILE: Senior Engineer
## THE SCENE
A quiet office, screen-sharing a code review. The engineer is walking through a technical concept with calm authority. No jargon for jargon's sake — every word is chosen to clarify.
### DIRECTOR'S NOTES
Style: Clear, informative, precise. Authoritative but never condescending. The confidence of someone who has debugged this exact problem before.
Pacing: Steady and measured. Slightly slower on key technical terms. Brief pauses between logical steps so the listener can follow.
Tone: Professional, grounded, trustworthy. Think senior engineer at a whiteboard, not a lecture hall.
### TRANSCRIPT""",

    'Sadaltager': """# AUDIO PROFILE: Science Communicator
## THE SCENE
A well-lit studio, recording a science explainer. The host makes complex ideas click without dumbing them down.
### DIRECTOR'S NOTES
Style: Knowledgeable and clear. The enthusiasm of someone who finds their subject genuinely fascinating. Authoritative but accessible — never dry.
Pacing: Moderate with emphasis on key numbers and terms. Slight acceleration on connecting phrases, deliberate slowdown on the "aha" moments.
Tone: Curious, engaged, confident. Like a great science YouTuber.
### TRANSCRIPT""",

    # ── Reasoning / analysis ──

    'Orus': """# AUDIO PROFILE: Thoughtful Analyst
## THE SCENE
A quiet study, late evening. Thinking through a complex problem aloud, weighing evidence carefully before each statement.
### DIRECTOR'S NOTES
Style: Calm, measured, thoughtful. Each sentence feels considered. The listener should trust that nothing is being said carelessly.
Pacing: Deliberate. Slightly slower than conversational. Meaningful pauses between logical steps — let the reasoning breathe.
Tone: Serious but not heavy. Like a wise mentor working through a problem with you, not at you.
### TRANSCRIPT""",

    # ── Instructions / how-to ──

    'Kore': """# AUDIO PROFILE: Expert Instructor
## THE SCENE
A hands-on workshop. The instructor is walking someone through a process, making each step crystal clear before moving to the next.
### DIRECTOR'S NOTES
Style: Firm, crisp, well-organized. Confident delivery with clear enunciation. Each instruction stands on its own — no mumbling, no ambiguity.
Pacing: Brisk but not rushed. Distinct pause between steps. Slightly slower on action words ("click", "install", "open").
Tone: Decisive and helpful. Like a great workshop leader who has taught this a hundred times and still cares that you get it right.
### TRANSCRIPT""",

    # ── Creative / stories ──

    'Zephyr': """# AUDIO PROFILE: Storyteller
## THE SCENE
A dimly lit stage, single spotlight, an audience leaning in. The storyteller paints worlds with their voice — every sentence has shape and color.
### DIRECTOR'S NOTES
Style: Bright, expressive, engaging. Dynamic range — whisper on the mysterious parts, lift on the exciting moments. This is performance, not reading.
Pacing: Varied and musical. Speed up during action, slow down for atmosphere. Let dramatic moments hang in the air.
Tone: Warm, imaginative, captivating. The listener should forget they're listening to a machine.
### TRANSCRIPT""",

    # ── Short / snappy ──

    'Puck': """# AUDIO PROFILE: Quick Wit
## THE SCENE
Mid-conversation, jumping in with a fast, sharp reply. No preamble, no wind-up — just the answer.
### DIRECTOR'S NOTES
Style: Upbeat, snappy, friendly. A natural "vocal smile." Punchy consonants. No filler words, no hedging.
Pacing: Fast and confident. Get in, land the point, done. Every word earns its place.
Tone: Playful, sharp, likeable.
### TRANSCRIPT""",

    # ── Excitement / celebration ──

    'Fenrir': """# AUDIO PROFILE: Hype Person
## THE SCENE
Just got incredible news. Can barely contain it. The energy is infectious — the listener should feel the excitement through the audio.
### DIRECTOR'S NOTES
Style: Genuinely excited, infectious energy. Not shouting — projecting. The enthusiasm is real, not performed. "Vocal smile" cranked to 11.
Pacing: Fast, energetic, bouncing. Words tumble out with natural momentum. Brief pauses only for emphasis before the biggest moments.
Tone: Electric, warm, celebratory.
### TRANSCRIPT""",

    # ── Empathy / comfort ──

    'Vindemiatrix': """# AUDIO PROFILE: Compassionate Friend
## THE SCENE
A quiet room, sitting close. Someone needs to hear that it's going to be okay. Every word is chosen with care.
### DIRECTOR'S NOTES
Style: Gentle, genuine kindness. Soft dynamics — never loud, never sharp. The warmth of someone who truly listens before they speak.
Pacing: Slow, unhurried. Long pauses between thoughts. Let the silence hold space. No rushing to fill gaps.
Tone: Tender, caring, grounding. Like a hand on the shoulder.
### TRANSCRIPT""",

    # ── Warning / serious ──

    'Alnilam': """# AUDIO PROFILE: Authority Voice
## THE SCENE
A serious briefing. Something important needs to be communicated clearly, with weight. No room for misunderstanding.
### DIRECTOR'S NOTES
Style: Firm, authoritative, grounded. Every word carries weight. No softening, no hedging. Direct and serious.
Pacing: Measured and deliberate. Slower than conversational. Emphatic stress on critical words ("never", "must", "immediately").
Tone: Grave but controlled. Like a safety officer delivering a critical briefing.
### TRANSCRIPT""",

    # ── Teaching / explaining ──

    'Callirrhoe': """# AUDIO PROFILE: Patient Teacher
## THE SCENE
One-on-one tutoring. The teacher adapts to the student's pace, makes complex things feel simple, and checks in naturally.
### DIRECTOR'S NOTES
Style: Easy-going, patient, clear. Uses natural emphasis to highlight the important bits. Never talks down — just makes things click.
Pacing: Relaxed. Slightly slower on new concepts, natural speed on connecting phrases. Brief pauses after key ideas to let them land.
Tone: Encouraging, warm, patient. The listener should feel smart, not stupid.
### TRANSCRIPT""",

    # ── History / narrative ──

    'Gacrux': """# AUDIO PROFILE: Historian
## THE SCENE
A wood-paneled study, firelight. A historian recounts events with the gravity they deserve, making the past feel alive and immediate.
### DIRECTOR'S NOTES
Style: Mature, seasoned, commanding. Rich vocal texture. Each sentence paints a picture. The weight of history in every phrase.
Pacing: Unhurried, stately. Let dates and names land with significance. Building rhythm — short phrases for drama, longer ones for context.
Tone: Deep, thoughtful, authoritative. Like a documentary narrator who has spent decades with this material.
### TRANSCRIPT""",

    # ── News / reporting ──

    'Rasalgethi': """# AUDIO PROFILE: Correspondent
## THE SCENE
Live from the field. Professional, composed, delivering facts with clarity and urgency without sensationalism.
### DIRECTOR'S NOTES
Style: Clear, informative, professional. Neutral but engaged. Facts first, always. No editorializing in the delivery.
Pacing: Moderate-to-brisk. Steady rhythm. Slight emphasis on names, numbers, and key facts.
Tone: Composed, credible, alert. Like a trusted news voice.
### TRANSCRIPT""",

    # ── Motivational ──

    'Sadachbia': """# AUDIO PROFILE: Coach
## THE SCENE
Halftime in the locker room. The team is down but not out. Every word is fuel.
### DIRECTOR'S NOTES
Style: Lively, animated, motivating. Rising intensity through the delivery. Build from steady conviction to peak energy.
Pacing: Dynamic — start measured, accelerate into the key message. Punchy phrases. Power pauses before the big lines.
Tone: Fiery but genuine. The listener should feel their chest swell.
### TRANSCRIPT""",

    # ── Intimate / personal ──

    'Enceladus': """# AUDIO PROFILE: Confidant
## THE SCENE
Late night, low light. A private conversation. Words spoken softly, meant only for the person listening.
### DIRECTOR'S NOTES
Style: Soft, breathy, intimate. Close-mic feel — proximity effect. Every word is a quiet offering.
Pacing: Very slow. Words drift. Long pauses between thoughts. Silence is part of the performance.
Tone: Tender, private, reflective.
### TRANSCRIPT""",

    # ── Long-form / smooth ──

    'Algieba': """# AUDIO PROFILE: Narrator
## THE SCENE
Recording booth, premium headphones, producing an audiobook or long-form essay. The voice carries the listener effortlessly through extended material.
### DIRECTOR'S NOTES
Style: Smooth, polished, refined. Effortless flow — sentences connect like music. Rich but never heavy. The voice you could listen to for hours.
Pacing: Moderate and consistent. Gentle variation to maintain interest. Subtle emphasis, never theatrical. Let the writing do the heavy lifting.
Tone: Elegant, composed, engaging. Late-night radio quality.
### TRANSCRIPT""",

    # ── Remaining voices ──

    'Aoede': """# AUDIO PROFILE: Afternoon Host
## THE SCENE
A sunlit café patio, gentle breeze. Two people catching up over iced drinks. Everything is easy.
### DIRECTOR'S NOTES
Style: Breezy, easy-going, light. Effortless and pleasant — no tension, no agenda. The "vocal smile" is subtle but constant.
Pacing: Natural and flowing. Unhurried but not slow. Sentences drift naturally into each other.
Tone: Airy, relaxed, likeable.
### TRANSCRIPT""",

    'Leda': """# AUDIO PROFILE: Young Professional
## THE SCENE
A modern co-working space. Sharp, energetic, no-nonsense. Getting things done with style.
### DIRECTOR'S NOTES
Style: Youthful, clear, fresh. Direct and genuine with natural energy. No affectation, no filler. Says what needs to be said.
Pacing: Quick but articulate. Young energy without sloppiness. Confident delivery.
Tone: Crisp, modern, authentic.
### TRANSCRIPT""",

    'Autonoe': """# AUDIO PROFILE: Morning Show Host
## THE SCENE
A bright radio studio, early morning. The host radiates warmth and energy — the kind of voice that makes people smile on their commute.
### DIRECTOR'S NOTES
Style: Bright, cheerful, articulate. Warm without being saccharine. Genuine enthusiasm, not performed.
Pacing: Upbeat, natural rhythm. Good energy without being frantic.
Tone: Sunny, welcoming, clear.
### TRANSCRIPT""",

    'Iapetus': """# AUDIO PROFILE: Keynote Speaker
## THE SCENE
A packed conference hall. The speaker commands the room with clarity and confidence. Every sentence is polished.
### DIRECTOR'S NOTES
Style: Clear, crisp, confident. Good projection and clean diction. Professional polish without stiffness.
Pacing: Measured, deliberate. Strategic pauses for emphasis. Builds momentum through key points.
Tone: Authoritative, engaging, polished.
### TRANSCRIPT""",

    'Umbriel': """# AUDIO PROFILE: Podcast Host
## THE SCENE
A home studio with good acoustics. Mid-episode, deep in a topic the host loves. No rush, no deadline — just pure exploration.
### DIRECTOR'S NOTES
Style: Laid-back, comfortable, unhurried. The ease of someone who genuinely enjoys what they're talking about. Thoughtful without being heavy.
Pacing: Slow and easy. Lets ideas develop. Comfortable with brief silences.
Tone: Relaxed, genuine, engaging.
### TRANSCRIPT""",

    'Despina': """# AUDIO PROFILE: Luxury Brand Voice
## THE SCENE
A high-end product reveal. Everything is polished, considered, beautiful. The voice matches the aesthetic.
### DIRECTOR'S NOTES
Style: Smooth, flowing, pleasant. Graceful delivery with polished edges. Every syllable is intentional.
Pacing: Even and elegant. Unhurried, letting each phrase land with precision.
Tone: Refined, composed, premium.
### TRANSCRIPT""",

    'Erinome': """# AUDIO PROFILE: News Anchor
## THE SCENE
A professional broadcast desk. Red light on. Millions watching. Every word matters.
### DIRECTOR'S NOTES
Style: Clear, precise, well-articulated. Confident and authoritative. Zero hesitation, zero filler. Flawless delivery.
Pacing: Steady, professional cadence. Moderate speed with emphasis on names, numbers, and key facts.
Tone: Composed, credible, sharp.
### TRANSCRIPT""",

    'Algenib': """# AUDIO PROFILE: Seasoned Raconteur
## THE SCENE
A dimly lit bar, late evening. An old hand telling stories from a life well-lived. The gravelly voice adds character to every word.
### DIRECTOR'S NOTES
Style: Gravelly, textured, distinctive. Natural authority with character and depth. Think grizzled storyteller who has seen it all.
Pacing: Unhurried and deliberate. Letting the texture of the voice do the work. Pauses for effect.
Tone: Weathered, authentic, compelling.
### TRANSCRIPT""",

    'Laomedeia': """# AUDIO PROFILE: Team Lead
## THE SCENE
A standup meeting. The lead is rallying the team — positive energy, clear direction, genuine belief in the work.
### DIRECTOR'S NOTES
Style: Upbeat, positive, encouraging. Bright energy without being over the top. Sincere, not performative.
Pacing: Lively, forward-moving. Good momentum. Emphasis on action items and wins.
Tone: Optimistic, clear, motivating.
### TRANSCRIPT""",

    'Achernar': """# AUDIO PROFILE: Meditation Guide
## THE SCENE
A silent room. Candles. The voice arrives like a whisper from far away, guiding the listener gently inward.
### DIRECTOR'S NOTES
Style: Soft, gentle, quiet. Barely above a whisper at times. Calm above all else. Each word is an offering, not a demand.
Pacing: Very slow, floating. Long pauses between phrases. Words dissolve into stillness. No urgency exists here.
Tone: Serene, peaceful, grounding.
### TRANSCRIPT""",

    'Schedar': """# AUDIO PROFILE: Reliable Narrator
## THE SCENE
A clean, well-lit room. No drama, no flair — just honest, clear communication. The voice you'd trust to read anything.
### DIRECTOR'S NOTES
Style: Even, balanced, steady. Reliable and neutral with quiet warmth. Never distracting, never boring. The "Swiss watch" of voices.
Pacing: Moderate, consistent. Predictable in the best way.
Tone: Steady, trustworthy, versatile.
### TRANSCRIPT""",

    'Pulcherrima': """# AUDIO PROFILE: Executive
## THE SCENE
A boardroom. The speaker has the floor and knows exactly what they want to say. No wasted words.
### DIRECTOR'S NOTES
Style: Forward, confident, direct. Assertive delivery with clarity and purpose. Commands attention without raising the volume.
Pacing: Brisk and decisive. Efficient use of every second. Strategic emphasis on conclusions.
Tone: Commanding, sharp, purposeful.
### TRANSCRIPT""",

    'Zubenelgenubi': """# AUDIO PROFILE: Buddy
## THE SCENE
A back porch, two friends, easy conversation. Nothing formal, nothing forced. Just talking.
### DIRECTOR'S NOTES
Style: Casual, relaxed, natural. Like explaining something to a friend without thinking about how you sound. Authentic above all.
Pacing: Easy, conversational. Natural rhythm with casual asides. No formality whatsoever.
Tone: Easygoing, genuine, comfortable.
### TRANSCRIPT""",
}


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

# Kokoro voice mapping (Gemini voice name → Kokoro voice ID)
# af_ = American Female, am_ = American Male, bf_ = British Female, bm_ = British Male
KOKORO_VOICES = {
    # Conversational
    'Sulafat': 'af_heart',       # warm default
    'Achird': 'af_bella',        # friendly
    'Aoede': 'af_sky',           # breezy
    # Technical / authoritative
    'Charon': 'am_michael',      # precise
    'Sadaltager': 'am_eric',     # knowledgeable
    'Alnilam': 'am_onyx',        # firm
    # Reasoning
    'Orus': 'am_adam',           # measured
    # Instructions
    'Kore': 'af_kore',          # crisp (same name!)
    # Creative
    'Zephyr': 'af_nova',        # bright
    # Short / snappy
    'Puck': 'am_puck',          # upbeat (same name!)
    # Excitement
    'Fenrir': 'am_fenrir',      # energetic (same name!)
    # Empathy
    'Vindemiatrix': 'af_sarah',  # gentle
    # Teaching
    'Callirrhoe': 'af_jessica',  # patient
    # History
    'Gacrux': 'bm_george',      # mature British
    # News
    'Rasalgethi': 'bm_daniel',  # professional British
    # Intimate
    'Enceladus': 'af_nicole',   # soft
    # Motivational
    'Sadachbia': 'af_river',    # lively
    # Long-form
    'Algieba': 'bf_emma',       # smooth British
    # Others
    'Leda': 'af_alloy',
    'Autonoe': 'af_aoede',
    'Iapetus': 'am_liam',
    'Umbriel': 'am_echo',
    'Despina': 'bf_isabella',
    'Erinome': 'bf_lily',
    'Algenib': 'bm_lewis',
    'Laomedeia': 'bf_alice',
    'Achernar': 'af_sky',
    'Schedar': 'am_adam',
    'Pulcherrima': 'am_onyx',
    'Zubenelgenubi': 'am_echo',
}

# Reverse: Gemini voice → Groq voice (for fallback)
GROQ_VOICES = {v: k for k, v in GEMINI_VOICES.items()}
# Groq only supports: autumn diana hannah austin daniel troy
GROQ_VOICE_FALLBACK = {
    # Primary
    'Sulafat': 'autumn', 'Charon': 'daniel', 'Orus': 'daniel',
    'Zephyr': 'diana', 'Kore': 'hannah', 'Puck': 'austin',
    'Fenrir': 'troy', 'Aoede': 'autumn', 'Leda': 'hannah',
    # Extended
    'Enceladus': 'diana', 'Algieba': 'daniel', 'Sadachbia': 'austin',
    'Vindemiatrix': 'autumn', 'Achird': 'austin', 'Callirrhoe': 'autumn',
    'Autonoe': 'diana', 'Iapetus': 'daniel', 'Umbriel': 'austin',
    'Despina': 'diana', 'Erinome': 'hannah', 'Algenib': 'troy',
    'Rasalgethi': 'daniel', 'Laomedeia': 'austin', 'Achernar': 'diana',
    'Alnilam': 'daniel', 'Schedar': 'autumn', 'Gacrux': 'troy',
    'Pulcherrima': 'hannah', 'Zubenelgenubi': 'austin', 'Sadaltager': 'daniel',
}


def _detect_language(text, hint=''):
    """Detect language from text. Uses hint from Whisper if available, otherwise detects from script/common words."""
    # If Whisper gave us a non-English hint and it's a supported language, trust it
    if hint and hint != 'en' and hint in KOKORO_LANG_MAP:
        return hint

    # Script-based detection (covers CJK, Devanagari, etc.)
    for ch in text[:200]:
        cp = ord(ch)
        # Japanese (Hiragana, Katakana, CJK)
        if 0x3040 <= cp <= 0x309F or 0x30A0 <= cp <= 0x30FF:
            return 'ja'
        # Chinese (CJK Unified Ideographs — only if no Japanese kana found)
        if 0x4E00 <= cp <= 0x9FFF:
            # Could be Japanese or Chinese; check for kana presence
            if any(0x3040 <= ord(c) <= 0x30FF for c in text[:200]):
                return 'ja'
            return 'zh'
        # Devanagari (Hindi)
        if 0x0900 <= cp <= 0x097F:
            return 'hi'

    # Common word detection for Latin-script languages
    t = text.lower()
    # Italian markers (check first — share some words with Spanish)
    if any(w in t for w in ['ciao', ' questo ', ' quella ', ' perché ', ' molto ', ' anche ',
                             ' sono ', ' essere ', ' della ', ' delle ', ' gli ', ' buon ',
                             ' stai ', ' bene ', ' grazie ', ' cosa ', ' tutto ']):
        return 'it'
    # Portuguese — check BEFORE French because Portuguese uses ç, ã, õ, ê like French,
    # and has many unambiguous words
    if any(ch in text for ch in 'ãõÃÕ') or \
       any(w in t for w in ['olá', 'obrigado', 'obrigada', 'português', 'português.',
                             ' você ', ' não ', ' muito ', ' para ', ' com ', ' isso ',
                             ' porque ', ' eu ', ' estou ', ' sou ', ' posso ', ' sim ',
                             ' agora ', ' também ', ' mas ', ' então ', ' precisar ',
                             ' assistência ', ' traduções ']):
        return 'pt'
    # French — uses œ uniquely, plus many common words (no ç — shared with Portuguese)
    if any(ch in text for ch in 'œŒ') or \
       any(w in t for w in [' je ', ' tu ', ' vous ', ' nous ', " c'est ", " l'", " d'", " j'",
                             'bonjour', 'merci', 'français', 'comment ça', ' oui ', ' non ',
                             ' les ', ' des ', ' est ', ' une ', ' sont ', ' dans ', ' avec ',
                             ' très ', ' être ', ' bien ', ' peux ', ' suis ', ' prêt ',
                             ' avez ', ' pour ', ' aussi ']):
        return 'fr'
    # Spanish — require Spanish-specific markers (ñ, ¿, ¡, or unambiguous words)
    # Don't rely on accented vowels alone since French uses them too.
    if any(ch in text for ch in 'ñÑ¿¡') or \
       any(w in t for w in ['hola', 'gracias', 'español', 'hablaré', 'buenos días',
                             ' está ', ' tiene ', ' también ', ' puede ', ' hacer ',
                             ' los ', ' las ', ' pero ', ' por ', ' para ', ' con ',
                             ' esto ', ' soy ', ' estoy ', ' puedo ', ' quiero ',
                             ' sí ', ' ayudarte ']):
        return 'es'
    # Romanian markers (diacritics ă â î ș ț or common words)
    if any(ch in text for ch in 'ăâîșțĂÂÎȘȚ') or \
       any(w in t for w in [' sunt ', ' este ', ' pentru ', ' foarte ', ' bună ', ' mulțumesc ',
                             ' salut ', ' acest ', ' aceasta ', ' care ', ' când ', ' unde ',
                             ' ce ', ' și ', ' nu ', ' da ', ' eu ', ' tu ', ' noi ', ' voi ']):
        return 'ro'
    # German markers (umlauts or common words)
    if any(ch in text for ch in 'äöüßÄÖÜ') or \
       any(w in t for w in [' und ', ' der ', ' die ', ' das ', ' ist ', ' nicht ', ' sie ',
                             ' auch ', ' aber ', ' wenn ', ' sehr ', ' schon ', ' hallo ']):
        return 'de'
    return 'en'


def _detect_speech_speed(text):
    """Detect emotional tone → adjust Kokoro speed (0.85–1.15)."""
    t = text.lower()

    # Excited / urgent → faster
    if any(w in t for w in ['!', 'amazing', 'incredible', 'urgent', 'quickly',
                             'immediately', 'wow', 'hurry', 'awesome', 'fantastic',
                             'breaking', 'alert', 'critical']):
        return 1.12

    # Lists / instructions → slightly faster, crisp
    if any(w in t for w in ['step 1', 'first,', 'next,', 'finally,', 'instructions']):
        return 1.06

    # Empathetic / comforting → slower
    if any(w in t for w in ['i understand', 'sorry', 'difficult', 'condolence',
                             'take care', 'hang in there', 'my heart']):
        return 0.85

    # Thoughtful / reflective → slower
    if any(w in t for w in ['consider', 'perhaps', 'carefully', 'nuance',
                             'on the other hand', 'it depends', 'reflect']):
        return 0.90

    # Storytelling / creative → slightly slower for drama
    if any(w in t for w in ['once upon', 'imagine', 'picture this', 'long ago']):
        return 0.92

    # Short snappy → slightly faster
    if len(text) < 30:
        return 1.08

    return 1.0


def _generate_kokoro_tts(text, voice='Sulafat', lang='en'):
    """Generate TTS via Kokoro sidecar. Returns (audio_bytes, mime_type) or None."""
    import time as _time

    # Determine Kokoro language code
    lang_code = KOKORO_LANG_MAP.get(lang, 'a')

    # Map voice name to Kokoro voice ID
    if voice and '_' in voice and voice.split('_')[0] in ('af', 'am', 'bf', 'bm', 'ef', 'em', 'ff', 'jf', 'jm', 'zf', 'zm', 'hf', 'hm', 'if', 'im', 'pf', 'pm'):
        kokoro_voice = voice
    elif lang_code != 'a' and voice in ('auto', 'Sulafat', 'Achird', 'Puck'):
        kokoro_voice = KOKORO_LANG_DEFAULT_VOICE.get(lang_code, 'af_heart')
    else:
        kokoro_voice = KOKORO_VOICES.get(voice, 'af_heart')

    # Match phonemizer to voice — British voices need British English phonemizer
    voice_prefix = kokoro_voice.split('_')[0] if '_' in kokoro_voice else ''
    if voice_prefix in ('bf', 'bm'):
        lang_code = 'b'
    elif voice_prefix in ('af', 'am') and lang_code == 'b':
        lang_code = 'a'

    # Emotion-aware speed
    speed_val = _detect_speech_speed(text)

    try:
        start = _time.time()
        resp = requests.post(
            KOKORO_SIDECAR_URL,
            json={
                'text': text,
                'voice': kokoro_voice,
                'speed': speed_val,
                'lang': lang_code,
            },
            timeout=KOKORO_SIDECAR_TIMEOUT,
        )
        elapsed = _time.time() - start

        if resp.status_code == 200:
            audio_bytes = resp.content
            logger.info(f"Kokoro sidecar voice={kokoro_voice} speed={speed_val:.2f} {len(text)}ch -> {len(audio_bytes)} bytes in {elapsed:.1f}s")
            return audio_bytes, 'audio/wav'
        else:
            logger.warning(f"Kokoro sidecar returned {resp.status_code}: {resp.text[:200]}")
            return None
    except requests.Timeout:
        logger.warning(f"Kokoro sidecar timeout ({KOKORO_SIDECAR_TIMEOUT}s)")
        return None
    except requests.ConnectionError:
        logger.warning("Kokoro sidecar not reachable")
        return None
    except Exception as e:
        logger.warning(f"Kokoro sidecar error: {e}")
        return None


def _generate_gemini_tts(text, voice='Kore'):
    """Generate TTS using Gemini. Returns (audio_bytes, mime_type) or None.
    Uses Pro model for longer/important content, Flash for short replies."""
    import base64
    import struct
    import io
    import wave

    api_key = Config.GEMINI_API_KEY
    if not api_key:
        return None

    # Cap at 8K chars — TTS model supports 32K tokens but diminishing returns past 8K
    if len(text) > 8000:
        text = text[:8000]

    gemini_voice = GEMINI_VOICES.get(voice, voice)
    # Capitalize first letter for Gemini
    if gemini_voice and gemini_voice[0].islower():
        gemini_voice = gemini_voice[0].upper() + gemini_voice[1:]

    # Prepend style direction for expressive TTS
    style = VOICE_STYLE_DIRECTIONS.get(gemini_voice, '')
    prompted_text = f'{style}\n\n{text}' if style else text

    # Model selection — content-aware, not just length:
    # Pro voices: reasoning, teaching, creative, history, long-form narration
    # Flash voices: quick answers, casual chat, humor, short factual replies
    _PRO_VOICES = {'Orus', 'Callirrhoe', 'Zephyr', 'Gacrux', 'Algieba', 'Sadaltager',
                   'Vindemiatrix', 'Enceladus', 'Sadachbia', 'Rasalgethi', 'Alnilam'}
    if gemini_voice in _PRO_VOICES:
        model = 'gemini-2.5-pro-preview-tts'
    else:
        model = 'gemini-2.5-flash-preview-tts'

    # Log input token estimate (1 token ≈ 4 chars)
    input_chars = len(prompted_text)
    est_tokens = input_chars // 4
    logger.info(f"TTS: model={model.split('gemini-')[1].split(':')[0]} voice={gemini_voice} "
                f"text={len(text)}ch prompt={input_chars}ch ~{est_tokens}tok")

    try:
        import time as _time
        resp = None
        for _attempt in range(3):
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompted_text}]}],
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
            if resp.status_code in (429, 500, 503) and _attempt < 2:
                wait = (1.5 if resp.status_code == 429 else 0.5) * (_attempt + 1)
                logger.info(f"Gemini TTS {resp.status_code}, retrying in {wait}s (attempt {_attempt + 1}/3)...")
                _time.sleep(wait)
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
                logger.info(f"Gemini TTS [{model.split('-tts')[0].split('gemini-')[1]}] voice={gemini_voice}, {len(pcm_bytes)} PCM bytes")
                return wav_buf.getvalue(), 'audio/wav'

        return None
    except Exception as e:
        logger.warning(f"Gemini TTS [{model}] failed: {e}")
        return None


@media_bp.route('/tts/groq', methods=['POST'])
@limiter.limit("30 per minute")
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
    lang_hint = (payload.get('lang') or '').strip()
    voice_context = (payload.get('voice_context') or '').strip()  # Read Aloud sends full text for smart picking

    # ── Auto-detect language from the actual text ──
    # Don't blindly trust the hint — it may come from the user prompt, not the AI response.
    # Always run detection; use the hint only as a fallback signal.
    pick_text = voice_context or text
    detected = _detect_language(pick_text, lang_hint)
    # If detection finds a non-English language, use it (stronger signal than hint).
    # Otherwise fall back to the hint if valid, else English.
    if detected != 'en':
        lang = detected
    elif lang_hint in ('en', 'fr', 'ja', 'zh', 'es', 'hi', 'it', 'ro', 'de', 'pt'):
        lang = lang_hint
    else:
        lang = 'en'

    # ── Dynamic voice based on content ──
    if voice == 'autumn' or voice == 'auto':
        voice = _pick_voice_for_content(pick_text)
        logger.info(f"TTS: dynamic voice → {voice}, lang={lang}")

    # ── 1. Try Kokoro first (local, free, unlimited) — only for supported languages ──
    kokoro_result = _generate_kokoro_tts(text, voice, lang) if lang in KOKORO_LANG_MAP else None
    if kokoro_result:
        audio_bytes, mime_type = kokoro_result
        logger.info(f"TTS: Kokoro success, {len(audio_bytes)} bytes")
        from flask import Response
        return Response(audio_bytes, mimetype=mime_type, headers={
            'Content-Disposition': 'inline; filename="speech.wav"',
        })

    # ── 2. Try Gemini (cloud, rate-limited) ──
    gemini_result = _generate_gemini_tts(text, voice)
    if gemini_result:
        audio_bytes, mime_type = gemini_result
        logger.info(f"TTS: Gemini success, {len(audio_bytes)} bytes")
        from flask import Response
        return Response(audio_bytes, mimetype=mime_type, headers={
            'Content-Disposition': 'inline; filename="speech.wav"',
        })

    # ── 3. Fallback: Groq Orpheus (200 char limit) ──
    logger.info("TTS: Kokoro+Gemini failed, falling back to Groq")
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
                detected_lang = result.get('language', '?')
                logger.info(f"Groq STT success with {model}: {len(text)} chars, lang={detected_lang}")
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
