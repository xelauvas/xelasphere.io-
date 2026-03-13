"""
Firestore-backed Flask session interface.
Stores sessions in Firestore for persistence across Cloud Run instances.
"""
import pickle
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from flask.sessions import SessionInterface, SessionMixin
from werkzeug.datastructures import CallbackDict


class FirestoreSession(CallbackDict, SessionMixin):
    """Session object backed by Firestore."""

    def __init__(self, initial=None, sid=None, new=False):
        def on_update(self):
            self.modified = True
        CallbackDict.__init__(self, initial, on_update)
        self.sid = sid
        self.new = new
        self.modified = False


class FirestoreSessionInterface(SessionInterface):
    """
    Flask session interface using Firestore as the backend.
    Sessions are stored in a 'flask_sessions' collection.
    """

    def __init__(self, db, collection='flask_sessions', expire_seconds=86400):
        """
        Initialize the Firestore session interface.

        Args:
            db: Firestore client instance
            collection: Name of the Firestore collection for sessions
            expire_seconds: Session expiration time in seconds (default 24h)
        """
        self.db = db
        self.collection = collection
        self.expire_seconds = expire_seconds

    def _get_session_doc(self, sid):
        """Get reference to session document."""
        return self.db.collection(self.collection).document(sid)

    def open_session(self, app, request):
        """Load session from Firestore or create a new one."""
        sid = request.cookies.get(app.config.get('SESSION_COOKIE_NAME', 'session'))

        if sid:
            try:
                doc = self._get_session_doc(sid).get()
                if doc.exists:
                    data = doc.to_dict()
                    # Check expiration
                    expires_at = data.get('expires_at')
                    if expires_at and expires_at > datetime.now(timezone.utc):
                        session_data = pickle.loads(data.get('data', b''))
                        return FirestoreSession(session_data, sid=sid)
                    else:
                        # Session expired, delete it
                        self._get_session_doc(sid).delete()
            except Exception as e:
                app.logger.warning(f"Error loading session {sid}: {e}")

        # Create new session
        sid = str(uuid4())
        return FirestoreSession(sid=sid, new=True)

    def save_session(self, app, session, response):
        """Save session to Firestore."""
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        cookie_name = app.config.get('SESSION_COOKIE_NAME', 'session')

        # If session is empty, delete it
        if not session:
            if session.sid:
                try:
                    self._get_session_doc(session.sid).delete()
                except Exception:
                    pass
            response.delete_cookie(cookie_name, domain=domain, path=path)
            return

        # Calculate expiration
        httponly = self.get_cookie_httponly(app)
        secure = self.get_cookie_secure(app)
        samesite = self.get_cookie_samesite(app)
        expires = datetime.now(timezone.utc) + timedelta(seconds=self.expire_seconds)

        # Only save if modified or new
        if session.modified or session.new:
            try:
                session_data = {
                    'data': pickle.dumps(dict(session)),
                    'expires_at': expires,
                    'updated_at': datetime.now(timezone.utc)
                }
                self._get_session_doc(session.sid).set(session_data)
            except Exception as e:
                app.logger.error(f"Error saving session {session.sid}: {e}")
                return

        # Set cookie
        response.set_cookie(
            cookie_name,
            session.sid,
            expires=expires,
            httponly=httponly,
            domain=domain,
            path=path,
            secure=secure,
            samesite=samesite
        )


def cleanup_expired_sessions(db, collection='flask_sessions'):
    """
    Delete expired sessions from Firestore.
    Call this periodically (e.g., via Cloud Scheduler).
    """
    now = datetime.now(timezone.utc)
    sessions_ref = db.collection(collection)
    expired = sessions_ref.where('expires_at', '<', now).stream()

    batch = db.batch()
    count = 0
    for doc in expired:
        batch.delete(doc.reference)
        count += 1
        if count >= 500:  # Firestore batch limit
            batch.commit()
            batch = db.batch()
            count = 0

    if count > 0:
        batch.commit()
