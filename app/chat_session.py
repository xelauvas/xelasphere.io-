"""ChatSession class for Firestore chat session operations.

This module handles all chat session CRUD operations with Firestore,
including message saving, session history, and session management.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.cloud import firestore as gc_firestore

logger = logging.getLogger(__name__)

# Module-level whitelist of fields safe to PATCH from /chat/<id>
ALLOWED_PATCH_FIELDS = {'starred', 'pinned', 'title', 'tags', 'project_id'}


def _adjust_user_session_total(db, user_id: str, delta: int) -> None:
    """Increment or decrement a user's stored session count."""
    if not user_id or delta == 0:
        return
    try:
        db.collection('users').document(user_id).set(
            {'total_sessions': gc_firestore.Increment(delta)},
            merge=True
        )
    except Exception as exc:
        logger.warning(f"Failed to adjust total_sessions for {user_id}: {exc}")


class ChatSession:
    """Handle chat session operations in Firestore."""

    def __init__(self, db_client: gc_firestore.Client):
        self.db = db_client
        pass

    def create_session(self, user_id: str, session_name: Optional[str] = None, temporary: bool = False, project_id: Optional[str] = None) -> str:
        session_id = f"session_{uuid.uuid4()}"
        session_ref = self._get_session_ref(user_id, session_id)
        now = datetime.utcnow()
        session_data = {
            'session_name': session_name or 'New Chat',
            'messages': [],
            'pages': [],
            'active_page_id': None,
            'created_at': now,
            'status': 'temporary' if temporary else 'active',
            'is_temporary': temporary,
            'last_updated': now,
            'is_pinned': False,
            'pinned_at': None,
            # Unread notification tracking for cross-device sync
            'unread_count': 0,
            'last_message_at': None,
            'last_read_at': now,
            'project_id': project_id,
        }
        session_ref.set(session_data)
        if not temporary:
            _adjust_user_session_total(self.db, user_id, 1)
        return session_id

    def compute_embedding(self, text: str) -> List[float]:
        try:
            from app.memory_v2.embeddings import embed_text
            result = embed_text(text)
            if result is not None:
                return result.tolist()
        except Exception:
            pass
        return []

    def extract_key_info(self, text: str) -> List[str]:
        return [text]

    def update_session_preferences(self, user_id: str, session_id: str, preferences: List[str]) -> None:
        session_ref = self._get_session_ref(user_id, session_id)
        session_doc = session_ref.get()
        if session_doc.exists:
            existing_preferences = session_doc.to_dict().get('preferences', [])
            updated_preferences = list(set(existing_preferences + preferences))
        else:
            updated_preferences = preferences
        session_ref.set({'preferences': updated_preferences}, merge=True)

    def save_messages(self, user_id: str, session_id: str, messages: List[Dict[str, Any]], is_active_session: bool = True) -> None:
        """Save messages to a session.

        Args:
            user_id: The user's ID
            session_id: The session ID to save to
            messages: List of message dictionaries to save
            is_active_session: If False, increment unread_count for cross-device notifications
        """
        if not messages:
            return

        session_ref = self._get_session_ref(user_id, session_id)

        # Process messages for Firestore storage
        processed_messages = []
        for message in messages:
            message_copy = message.copy()

            # Convert SearchResult objects to dictionaries
            if 'search_results' in message_copy:
                if isinstance(message_copy['search_results'], list):
                    from pydantic import BaseModel
                    message_copy['search_results'] = [
                        {
                            'title': result.title,
                            'link': str(result.link),
                            'snippet': result.snippet,
                            'source_domain': result.source_domain,
                            'website_name': result.website_name,
                            'favicon': result.favicon,
                            'relevance_score': float(result.relevance_score),
                            'timestamp': result.timestamp.isoformat()
                        } if isinstance(result, BaseModel) else result
                        for result in message_copy['search_results']
                    ]

            # Add quantized embedding when response text exists
            # Uses int8 quantization to reduce Firestore storage by 4x
            response_text = message_copy.get('response', '')
            if response_text:
                try:
                    from .embedding_utils import embedding_to_storage
                    raw_embedding = self.compute_embedding(response_text)
                    if raw_embedding:
                        message_copy['embedding'] = embedding_to_storage(raw_embedding)
                    else:
                        message_copy['embedding'] = {}
                except ImportError:
                    # Fallback to raw embedding if utils not available
                    message_copy['embedding'] = self.compute_embedding(response_text)
            else:
                message_copy['embedding'] = {}

            # Extract key info
            key_info = self.extract_key_info(message_copy.get('prompt', ''))
            self.update_session_preferences(user_id, session_id, key_info)

            processed_messages.append(message_copy)

        # Update Firestore with fallback for edge cases
        now = datetime.utcnow()
        try:
            update_data = {
                'messages': gc_firestore.ArrayUnion(processed_messages),
                'last_updated': now,
                'last_message_at': now
            }
            # Increment unread_count only if this isn't the active session (for cross-device notifications)
            if not is_active_session:
                update_data['unread_count'] = gc_firestore.Increment(1)

            session_ref.update(update_data)
            logger.info(f"Successfully saved {len(processed_messages)} message(s) to session {session_id}")
        except Exception as update_err:
            logger.warning(f"ArrayUnion update failed for session {session_id}: {update_err}, trying fallback...")
            # Fallback: read current messages and append
            try:
                doc = session_ref.get()
                if doc.exists:
                    current_data = doc.to_dict()
                    current_messages = current_data.get('messages', [])
                    current_messages.extend(processed_messages)
                    fallback_update = {
                        'messages': current_messages,
                        'last_updated': now,
                        'last_message_at': now
                    }
                    if not is_active_session:
                        current_unread = current_data.get('unread_count', 0)
                        fallback_update['unread_count'] = current_unread + 1
                    session_ref.update(fallback_update)
                    logger.info(f"Fallback save succeeded - now {len(current_messages)} messages in session {session_id}")
                else:
                    # Document doesn't exist - create it
                    session_ref.set({
                        'messages': processed_messages,
                        'last_updated': now,
                        'created_at': now,
                        'session_name': 'Chat',
                        'status': 'active',
                        'last_message_at': now,
                        'unread_count': 0 if is_active_session else 1,
                        'last_read_at': now
                    })
                    logger.info(f"Created new document with {len(processed_messages)} message(s)")
            except Exception as fallback_err:
                logger.error(f"Fallback save also failed for session {session_id}: {fallback_err}")

    def get_history(
        self,
        user_id: str,
        page_size: int = 10,
        page_token: Optional[str] = None,
        project_id: Optional[str] = None,
        ungrouped: bool = False,
    ) -> Dict[str, Any]:
        from google.cloud.firestore_v1.base_query import FieldFilter
        sessions_query = self._get_sessions_query(user_id, page_token)
        if project_id is not None:
            sessions_query = sessions_query.where(filter=FieldFilter('project_id', '==', project_id))
        elif ungrouped:
            sessions_query = sessions_query.where(filter=FieldFilter('project_id', '==', None))
        sessions = sessions_query.limit(page_size + 1).stream()

        chat_history = []
        next_token = None

        for idx, session_doc in enumerate(sessions):
            if idx >= page_size:
                next_token = session_doc.id
                break

            session_data = session_doc.to_dict()
            if not isinstance(session_data, dict):
                logger.warning(f"Skipping session with unexpected data format: {session_data}")
                continue

            status = session_data.get('status', 'active')
            is_temporary = session_data.get('is_temporary', status == 'temporary')
            if status in ('archived', 'temporary'):
                continue

            session_name = session_data.get('session_name', f"Chat {idx + 1}")
            created_at = session_data.get('created_at')
            created_at_str = created_at.isoformat() if created_at and isinstance(created_at, datetime) else None
            pinned_at = session_data.get('pinned_at')
            if isinstance(pinned_at, datetime):
                pinned_at = pinned_at.isoformat()
            pages_raw = session_data.get('pages') or []
            normalized_pages = []
            for page in pages_raw:
                if not isinstance(page, dict):
                    continue
                normalized = page.copy()
                created_at = normalized.get('createdAt') or normalized.get('created_at')
                if isinstance(created_at, datetime):
                    normalized['createdAt'] = created_at.isoformat()
                    normalized.pop('created_at', None)
                normalized_pages.append(normalized)

            messages = session_data.get('messages', [])

            # Get last_updated timestamp
            last_updated = session_data.get('last_updated')
            last_updated_str = last_updated.isoformat() if last_updated and isinstance(last_updated, datetime) else None

            chat_history.append({
                'session_id': session_doc.id,
                'session_name': session_name,
                'created_at': created_at_str,
                'last_updated': last_updated_str or created_at_str,
                'messages': messages,
                'pages': normalized_pages,
                'active_page_id': session_data.get('active_page_id'),
                'status': status,
                'pinned_at': pinned_at,
                'is_temporary': is_temporary,
                'project_id': session_data.get('project_id') or None,
            })

        return {'chat_history': chat_history, 'next_page_token': next_token}

    def list_metadata(self, user_id: str) -> List[Dict[str, Any]]:
        """Return flat metadata for ALL sessions of a user — no message bodies.

        Used by the /chats page for client-side filter/sort/search.
        Lazy backfills msg_count / has_artifact / has_attachment if missing.

        Note: backfill issues a Firestore write per session that's missing
        denormalized fields. For users with N legacy sessions, the first
        load of /chats triggers up to N writes (one-time migration cost).
        Subsequent loads have zero writes.
        """
        sessions_ref = (
            self.db.collection('users').document(user_id)
            .collection('chat_sessions')
            .order_by('last_updated', direction=gc_firestore.Query.DESCENDING)
        )
        out: List[Dict[str, Any]] = []
        for snap in sessions_ref.stream():
            data = snap.to_dict() or {}
            # Skip soft-deleted or invalid docs
            if data.get('deleted'):
                continue
            if data.get('status') in ('temporary', 'archived'):
                continue
            if data.get('is_temporary'):
                continue

            # Backfill denormalized fields if missing
            msg_count = data.get('msg_count')
            has_artifact = data.get('has_artifact')
            has_attachment = data.get('has_attachment')

            needs_backfill = (
                msg_count is None or has_artifact is None or has_attachment is None
            )
            if needs_backfill:
                messages = data.get('messages', []) or []
                msg_count = len(messages)
                has_artifact = any(m.get('artifact') for m in messages)
                has_attachment = any(m.get('attachments') for m in messages)
                try:
                    snap.reference.update({
                        'msg_count': msg_count,
                        'has_artifact': bool(has_artifact),
                        'has_attachment': bool(has_attachment),
                    })
                except Exception:
                    # Backfill is best-effort; missing fields stay missing on next read
                    pass

            last_updated = data.get('last_updated') or data.get('created_at') or ''
            if isinstance(last_updated, datetime):
                last_updated = last_updated.isoformat()

            out.append({
                'session_id': snap.id,
                'title': data.get('title') or data.get('session_name') or 'New chat',
                'last_updated': last_updated,
                'model': data.get('model') or '',
                'msg_count': int(msg_count or 0),
                'starred': bool(data.get('starred', False)),
                'pinned': bool(data.get('pinned', False)),
                'tags': list(data.get('tags', []) or []),
                'has_artifact': bool(has_artifact),
                'has_attachment': bool(has_attachment),
            })
        return out

    def get_session(self, user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        doc_ref = self._get_session_ref(user_id, session_id)
        try:
            session_doc = doc_ref.get(timeout=2, retry=None)
        except TypeError:
            session_doc = doc_ref.get()
        if not session_doc.exists:
            return None

        session_data = session_doc.to_dict() or {}
        created_at = session_data.get('created_at')
        pinned_at = session_data.get('pinned_at')

        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()
        if isinstance(pinned_at, datetime):
            pinned_at = pinned_at.isoformat()

        pages_raw = session_data.get('pages') or []
        normalized_pages = []
        for page in pages_raw:
            if not isinstance(page, dict):
                continue
            normalized = page.copy()
            created_at_page = normalized.get('createdAt') or normalized.get('created_at')
            if isinstance(created_at_page, datetime):
                normalized['createdAt'] = created_at_page.isoformat()
                normalized.pop('created_at', None)
            normalized_pages.append(normalized)

        return {
            'session_id': session_id,
            'session_name': session_data.get('session_name', 'Untitled Session'),
            'created_at': created_at,
            'messages': session_data.get('messages', []),
            'pages': normalized_pages,
            'active_page_id': session_data.get('active_page_id'),
            'status': session_data.get('status', 'active'),
            'pinned_at': pinned_at,
            'is_temporary': session_data.get('is_temporary', False),
            'model_label': session_data.get('model_label') or session_data.get('model') or '',
            'project_id': session_data.get('project_id') or None,
        }

    def archive_session(self, user_id: str, session_id: str) -> Dict[str, Any]:
        session_ref = self._get_session_ref(user_id, session_id)
        session_doc = session_ref.get()
        if not session_doc.exists:
            raise ValueError("Chat session not found.")

        archived_at = datetime.utcnow()
        session_ref.set({
            'status': 'archived',
            'archived_at': archived_at,
            'last_updated': archived_at
        }, merge=True)
        return {'archived_at': archived_at.isoformat()}

    def pin_session(self, user_id: str, session_id: str, pinned: bool = True) -> Dict[str, Any]:
        session_ref = self._get_session_ref(user_id, session_id)
        session_doc = session_ref.get()
        if not session_doc.exists:
            raise ValueError("Chat session not found.")

        pinned_at_value = datetime.utcnow() if pinned else None
        update_payload = {
            'is_pinned': pinned,
            'pinned_at': pinned_at_value,
            'last_updated': datetime.utcnow()
        }
        session_ref.set(update_payload, merge=True)
        return {
            'is_pinned': pinned,
            'pinned_at': pinned_at_value.isoformat() if pinned_at_value else None
        }

    def update_session_metadata(
        self, user_id: str, session_id: str, fields: Dict[str, Any]
    ) -> bool:
        """Update whitelisted metadata fields on a session document.

        Returns True on success (or if no-op), False if session not found.
        Silently ignores fields outside the whitelist (defense in depth).
        """
        cleaned = {k: v for k, v in fields.items() if k in ALLOWED_PATCH_FIELDS}
        if not cleaned:
            return True  # no-op
        ref = self._get_session_ref(user_id, session_id)
        snap = ref.get()
        if not snap.exists:
            return False
        ref.update(cleaned)
        return True

    def bulk_delete_sessions(self, user_id: str, session_ids: List[str]) -> int:
        """Delete multiple sessions in a single Firestore batch.

        Returns count of sessions deleted. Sessions that do not exist
        are silently skipped (the count reflects only existing ones).
        """
        if not session_ids:
            return 0
        batch = self.db.batch()
        deleted = 0
        for session_id in session_ids:
            ref = self._get_session_ref(user_id, session_id)
            snap = ref.get()
            if snap.exists:
                batch.delete(ref)
                deleted += 1
        if deleted > 0:
            batch.commit()
        return deleted

    def count_grouped(self, user_id: str) -> int:
        """Return the count of sessions whose project_id is not null.

        Firestore '!=' queries require composite indexes. If unavailable
        in production, fall back to total_active - ungrouped_count via two
        queries. The route layer is resilient to either implementation.
        """
        from google.cloud.firestore_v1.base_query import FieldFilter
        ref = (
            self.db.collection('users').document(user_id)
            .collection('chat_sessions')
            .where(filter=FieldFilter('project_id', '!=', None))
        )
        return sum(1 for _ in ref.stream())

    def _get_session_ref(self, user_id: str, session_id: str) -> gc_firestore.DocumentReference:
        return self.db.collection('users').document(user_id).collection('chat_sessions').document(session_id)

    def _get_sessions_query(self, user_id: str, page_token: Optional[str] = None) -> gc_firestore.Query:
        # Order by last_updated so most recently active sessions appear first
        query = self.db.collection('users').document(user_id) \
            .collection('chat_sessions') \
            .order_by('last_updated', direction=gc_firestore.Query.DESCENDING)
        if page_token:
            last_ref = self._get_session_ref(user_id, page_token)
            try:
                last_doc = last_ref.get(timeout=2, retry=None)
            except TypeError:
                last_doc = last_ref.get()
            if last_doc.exists:
                query = query.start_after(last_doc)
        return query
