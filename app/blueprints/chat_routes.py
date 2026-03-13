"""Chat management routes blueprint.

Routes:
- POST /chat/new - Create new chat session
- POST /chat/save/<session_id> - Save chat session
- POST /chat/archive - Archive chat session
- POST /chat/pin - Pin/unpin chat session

Note: The main POST /chat endpoint remains in views.py due to its complexity
(~1100 lines with many helper function dependencies). It can be extracted
in a future refactoring phase.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict

from flask import Blueprint, jsonify, request, session
from google.cloud import firestore as gc_firestore

from app.config import db
from app.limiter import limiter, csrf_required
from app.blueprints.shared import (
    login_required, guest_allowed, error_handler, is_guest, chat_session
)

logger = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/chat/new', methods=['POST'])
@limiter.limit("20 per minute")
@csrf_required
@error_handler
@guest_allowed
def start_new_chat():
    """Create a new chat session."""
    logger.info("Received a POST request to /chat/new")
    data = request.get_json(silent=True) or {}
    temporary = bool(data.get('temporary'))
    session_name = data.get('session_name') or 'New Chat'

    # Guest mode: create ephemeral session (not saved to Firestore)
    if is_guest():
        session_id = f"guest_{uuid.uuid4().hex[:16]}"
        session['current_session_id'] = session_id
        return jsonify({
            'message': 'Guest chat session created',
            'session_id': session_id,
            'status': 'guest'
        }), 201

    # Regular user: create persistent session
    user_id = session['user']['uid']
    session_id = chat_session.create_session(user_id, session_name=session_name, temporary=temporary)
    session['current_session_id'] = session_id

    return jsonify({
        'message': 'Chat session created',
        'session_id': session_id,
        'status': 'temporary' if temporary else 'active'
    }), 201


@chat_bp.route('/chat/save/<session_id>', methods=['POST'])
@csrf_required
@error_handler
@login_required
def save_chat_session(session_id: str):
    """Save messages and metadata to a chat session."""
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid payload.'}), 400

    user_id = session['user']['uid']
    messages = data.get('messages')
    pages = data.get('pages')
    active_page_id = data.get('active_page_id')

    if messages is None and pages is None and active_page_id is None:
        return jsonify({'error': 'No updates provided.'}), 400

    update_payload: Dict[str, Any] = {
        'last_updated': datetime.utcnow()
    }
    # Use ArrayUnion to APPEND messages instead of replacing
    # IMPORTANT: Skip messages with response_id - those were already saved by streaming/chat endpoint
    use_array_union = False
    if isinstance(messages, list) and messages:
        # Filter out messages that have response_id (already saved by backend)
        new_messages = [m for m in messages if not (isinstance(m, dict) and m.get('response_id'))]
        if new_messages:
            messages = new_messages
            use_array_union = True
            logger.info(f"SAVE ENDPOINT: Filtered to {len(new_messages)} messages (skipped {len(data.get('messages', [])) - len(new_messages)} with response_id)")
        else:
            messages = []
            logger.info(f"SAVE ENDPOINT: All messages had response_id, skipping message save")
    if isinstance(pages, list):
        update_payload['pages'] = pages
    if isinstance(active_page_id, str):
        update_payload['active_page_id'] = active_page_id

    try:
        session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
        if use_array_union:
            # Append messages to existing array with fallback
            try:
                session_ref.update({
                    **update_payload,
                    'messages': gc_firestore.ArrayUnion(messages)
                })
                logger.info(f"SAVE ENDPOINT: ArrayUnion saved {len(messages)} message(s) to session {session_id}")
            except Exception as array_err:
                logger.warning(f"SAVE ENDPOINT: ArrayUnion failed for session {session_id}: {array_err}, using fallback")
                # Fallback: read current messages and append
                doc = session_ref.get()
                if doc.exists:
                    current_messages = doc.to_dict().get('messages', [])
                    current_messages.extend(messages)
                    session_ref.update({**update_payload, 'messages': current_messages})
                else:
                    session_ref.set({**update_payload, 'messages': messages, 'session_name': 'Chat', 'status': 'active'})
        else:
            session_ref.set(update_payload, merge=True)
        return jsonify({'message': 'Session saved'}), 200
    except Exception as exc:
        logger.error(f"Failed to persist session {session_id} for user {user_id}: {exc}")
        return jsonify({'error': 'Failed to save chat session.'}), 500


@chat_bp.route('/chat/archive', methods=['POST'])
@csrf_required
@error_handler
@login_required
def archive_chat_session():
    """Archive a chat session."""
    user_id = session['user']['uid']
    data = request.get_json() or {}
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({'error': 'Session ID is required.'}), 400

    try:
        result = chat_session.archive_session(user_id, session_id)
        if session.get('current_session_id') == session_id:
            session.pop('current_session_id', None)
        return jsonify({
            'message': 'Chat archived successfully.',
            'session_id': session_id,
            'status': 'archived',
            **result
        }), 200
    except ValueError as ve:
        return jsonify({'error': str(ve)}), 404
    except Exception as exc:
        logger.exception("Failed to archive chat %s for user %s", session_id, user_id)
        return jsonify({'error': 'Failed to archive chat session.'}), 500


@chat_bp.route('/chat/pin', methods=['POST'])
@csrf_required
@error_handler
@login_required
def pin_chat_session():
    """Pin or unpin a chat session."""
    user_id = session['user']['uid']
    data = request.get_json() or {}
    session_id = data.get('session_id')
    pinned = bool(data.get('pinned', True))

    if not session_id:
        return jsonify({'error': 'Session ID is required.'}), 400

    try:
        result = chat_session.pin_session(user_id, session_id, pinned)
        message = 'Chat pinned successfully.' if pinned else 'Chat unpinned successfully.'
        return jsonify({
            'message': message,
            'session_id': session_id,
            **result
        }), 200
    except ValueError as ve:
        return jsonify({'error': str(ve)}), 404
    except Exception as exc:
        logger.exception("Failed to update pin state for chat %s (user %s)", session_id, user_id)
        return jsonify({'error': 'Failed to update pin state.'}), 500
