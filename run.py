# GEVENT: monkey-patch MUST be the first thing executed so that the
# stdlib (socket, ssl, threading) is cooperatively patched before any
# other module imports it. thread=False keeps gRPC (Firestore) happy.
from gevent import monkey
monkey.patch_all(thread=False)

from dotenv import load_dotenv
import os

# Load environment variables from .env file before importing the app
load_dotenv()

from app import create_app

app = create_app()

# Note: Embedding model loads lazily on first request (~40s for jina-embeddings-v3)
# Subsequent requests are fast. Use min-instances=1 to avoid cold starts if needed.

if __name__ == "__main__":
    flask_env = os.getenv("FLASK_ENV", "production")
    default_port = 5000 if flask_env != "production" else 5001
    port = int(os.getenv("FLASK_RUN_PORT", os.getenv("PORT", default_port)))
    app.run(debug=(flask_env != "production"), port=port)
