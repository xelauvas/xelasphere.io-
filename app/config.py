import os
import firebase_admin
from firebase_admin import credentials, auth, firestore
import logging
from dotenv import load_dotenv

# Ensure .env is loaded whenever the config module is imported (e.g., via Gunicorn)
load_dotenv()

# Define the Config class
class Config:
    # Use environment variables for sensitive keys and secrets
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
    UPLOAD_FOLDER = 'uploads'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB
    APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5000').rstrip('/')
    GROQ_API_KEY = os.getenv('GROQ_API_KEY')
    API_URL = "https://api.groq.com/openai/v1/chat/completions"
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
    OPENROUTER_API_URL = os.getenv('OPENROUTER_API_URL', 'https://openrouter.ai/api/v1/chat/completions')
    OPENROUTER_SITE_URL = os.getenv('OPENROUTER_SITE_URL')
    OPENROUTER_APP_NAME = os.getenv('OPENROUTER_APP_NAME', 'XelaSphere')
    OPENROUTER_IMAGE_MODEL = os.getenv('OPENROUTER_IMAGE_MODEL', 'google/gemini-2.5-flash-image')
    OPENROUTER_IMAGE_URL = os.getenv('OPENROUTER_IMAGE_URL', 'https://openrouter.ai/api/v1/chat/completions')
    OPENROUTER_IMAGE_SIZE = os.getenv('OPENROUTER_IMAGE_SIZE', '1024x1024')
    # DeepSeek API configuration
    DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
    DEEPSEEK_API_URL = os.getenv('DEEPSEEK_API_URL', 'https://api.deepseek.com/v1/chat/completions')
    # Moonshot API configuration (Kimi models)
    MOONSHOT_API_KEY = os.getenv('MOONSHOT_API_KEY')
    MOONSHOT_API_URL = os.getenv('MOONSHOT_API_URL', 'https://api.moonshot.ai/v1/chat/completions')
    # Cerebras API configuration (rate-limit fallback)
    CEREBRAS_API_KEY = os.getenv('CEREBRAS_API_KEY')
    CEREBRAS_API_URL = os.getenv('CEREBRAS_API_URL', 'https://api.cerebras.ai/v1/chat/completions')
    # Gemini API configuration (Google AI Studio — OpenAI-compatible endpoint)
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    GEMINI_API_URL = os.getenv('GEMINI_API_URL', 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions')
    # Metadata-task model stack (title generation, session summaries, memory
    # extraction). Gemini primary, Groq 70B fallback. Env-overridable.
    METADATA_MODEL_PRIMARY = os.getenv('METADATA_MODEL_PRIMARY', 'gemini-2.5-flash')
    METADATA_MODEL_FALLBACK = os.getenv('METADATA_MODEL_FALLBACK', 'llama-3.3-70b-versatile')
    # OpenAI API configuration (GPT-4.1)
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    OPENAI_API_URL = os.getenv('OPENAI_API_URL', 'https://api.openai.com/v1/chat/completions')
    # Ollama local model configuration (no API key required)
    OLLAMA_API_URL = os.getenv('OLLAMA_API_URL', 'http://127.0.0.1:11434/v1/chat/completions')
    OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'gemma4:e2b')
    ALLOWED_ORIGINS = [
        origin.strip() for origin in os.getenv(
            'ALLOWED_ORIGINS',
            f"https://xelas.io,{APP_BASE_URL}"
        ).split(',') if origin.strip()
    ]

    # Web search configuration
    SEARCH_DEFAULT_BACKEND = os.getenv('SEARCH_BACKEND', 'brave')
    BING_SEARCH_API_KEY = os.getenv('BING_SEARCH_API_KEY', '')
    GOOGLE_SEARCH_API_KEY = os.getenv('GOOGLE_SEARCH_API_KEY', '')
    GOOGLE_SEARCH_ENGINE_ID = os.getenv('GOOGLE_SEARCH_ENGINE_ID', '')
    BRAVE_SEARCH_API_KEY = os.getenv('BRAVE_SEARCH_API_KEY', '')
    BRAVE_SEARCH_ENDPOINT = os.getenv('BRAVE_SEARCH_ENDPOINT', 'https://api.search.brave.com/res/v1/web/search')
    YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY', '')

    SEARCH_CONTEXT_BUDGET = {
        'deepseek': 30_000,
        'llama': 20_000,
        'gpt-4': 60_000,
        'kimi': 80_000,
        'qwen': 40_000,
        'default': 40_000,
    }

    SEARCH_PREAPPROVED_DOMAINS = [
        'wikipedia.org', 'en.wikipedia.org', 'bbc.com', 'bbc.co.uk',
        'reuters.com', 'nytimes.com', 'theguardian.com', 'techcrunch.com',
        'github.com', 'stackoverflow.com', 'developer.mozilla.org',
        'docs.python.org', 'arxiv.org', 'weather.com', 'apnews.com',
    ]

    BLOCKED_IMAGE_DOMAINS = []

    FETCH_DEADLINE_MS = 2000

    # Finnhub API for stock news
    FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY', '')

    SEARCH_BLOCKED_DOMAINS = os.getenv('SEARCH_BLOCKED_DOMAINS', 'zhihu.com,baidu.com,weibo.com,qq.com').split(',')
    options_sets = []

    # Path to Tesseract OCR executable (defaults to system PATH-friendly name)
    TESSERACT_CMD = os.getenv('TESSERACT_CMD', 'tesseract')
    # Languages to use for OCR (space-separated, e.g., "eng spa")
    TESSERACT_LANG = os.getenv('TESSERACT_LANG', 'eng')

    # Google OAuth 2.0 credentials - Use environment variables for production
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
    GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', f'{APP_BASE_URL}/auth/google/callback')

    # Microsoft OAuth 2.0 credentials - Use environment variables for production
    MICROSOFT_CLIENT_ID = os.getenv('MICROSOFT_CLIENT_ID')
    MICROSOFT_CLIENT_SECRET = os.getenv('MICROSOFT_CLIENT_SECRET')
    MICROSOFT_REDIRECT_URI = os.getenv('MICROSOFT_REDIRECT_URI', f'{APP_BASE_URL}/auth/microsoft/callback')

    # Microsoft OAuth URIs
    MICROSOFT_AUTH_URI = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    MICROSOFT_TOKEN_URI = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    MICROSOFT_USER_INFO_URI = "https://graph.microsoft.com/v1.0/me"

    # Discord OAuth 2.0 credentials - Use environment variables for production
    DISCORD_CLIENT_ID = os.getenv('DISCORD_CLIENT_ID')
    DISCORD_CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET')
    DISCORD_REDIRECT_URI = os.getenv('DISCORD_REDIRECT_URI', f'{APP_BASE_URL}/auth/discord/callback')

    # Discord OAuth URIs
    DISCORD_AUTH_URI = "https://discord.com/api/oauth2/authorize"
    DISCORD_TOKEN_URI = "https://discord.com/api/oauth2/token"
    DISCORD_USER_INFO_URI = "https://discord.com/api/users/@me"

    # Facebook OAuth 2.0 credentials - Use environment variables for production
    FACEBOOK_CLIENT_ID = os.getenv('FACEBOOK_CLIENT_ID')
    FACEBOOK_CLIENT_SECRET = os.getenv('FACEBOOK_CLIENT_SECRET')
    FACEBOOK_REDIRECT_URI = os.getenv('FACEBOOK_REDIRECT_URI', f'{APP_BASE_URL}/auth/facebook/callback')

    # Facebook OAuth URIs
    FACEBOOK_AUTH_URI = "https://www.facebook.com/v19.0/dialog/oauth"
    FACEBOOK_TOKEN_URI = "https://graph.facebook.com/v19.0/oauth/access_token"
    FACEBOOK_USER_INFO_URI = "https://graph.facebook.com/v19.0/me"

    # Firebase Admin SDK credentials file and storage bucket
    FIREBASE_CRED_PATH = os.getenv('FIREBASE_CRED_PATH')
    FIREBASE_STORAGE_BUCKET = os.getenv('FIREBASE_STORAGE_BUCKET')

    # Session configuration defaults to filesystem to avoid null errors
    SESSION_TYPE = os.getenv('SESSION_TYPE', 'filesystem')
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True
    SESSION_FILE_DIR = os.getenv('SESSION_FILE_DIR', os.path.join(os.getcwd(), 'flask_session'))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')
    SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
    RATELIMIT_STORAGE_URI = os.getenv('RATELIMIT_STORAGE_URI', 'memory://')

    # Reminders M0 — email-only reminder feature (chat utterance → SendGrid)
    REMINDERS_ENABLED = os.getenv('REMINDERS_ENABLED', 'false').lower() == 'true'
    SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY', '')
    SENDGRID_FROM_EMAIL = os.getenv('SENDGRID_FROM_EMAIL', 'noreply@xelasphere.io')
    SENDGRID_FROM_NAME = os.getenv('SENDGRID_FROM_NAME', 'XelaSphere Reminders')

# Security checks for production environment
if os.getenv('FLASK_ENV') == 'production':
    if not Config.SESSION_COOKIE_SECURE:
        logging.warning("SECURITY WARNING: SESSION_COOKIE_SECURE is not set to 'true' in a production environment.")
    if not Config.GOOGLE_REDIRECT_URI.startswith('https://'):
        logging.warning("SECURITY WARNING: GOOGLE_REDIRECT_URI is not using HTTPS in a production environment.")
    if not Config.MICROSOFT_REDIRECT_URI.startswith('https://'):
        logging.warning("SECURITY WARNING: MICROSOFT_REDIRECT_URI is not using HTTPS in a production environment.")
    if not Config.DISCORD_REDIRECT_URI.startswith('https://'):
        logging.warning("SECURITY WARNING: DISCORD_REDIRECT_URI is not using HTTPS in a production environment.")

# Now initialize Firebase using the Config class
if not Config.FIREBASE_CRED_PATH:
    raise RuntimeError("FIREBASE_CRED_PATH is not set. Provide the service account path via environment variables.")
if not os.path.exists(Config.FIREBASE_CRED_PATH):
    raise RuntimeError(f"FIREBASE_CRED_PATH does not exist: {Config.FIREBASE_CRED_PATH}")

cred = credentials.Certificate(Config.FIREBASE_CRED_PATH)

# Check if Firebase has already been initialized to prevent duplicate initialization
firebase_options = {}
if Config.FIREBASE_STORAGE_BUCKET:
    firebase_options['storageBucket'] = Config.FIREBASE_STORAGE_BUCKET

if not firebase_admin._apps:
    if firebase_options:
        firebase_admin.initialize_app(cred, firebase_options)
    else:
        firebase_admin.initialize_app(cred)

# Shared Firestore client used across the app
db = firestore.client()
