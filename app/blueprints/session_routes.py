"""Chat session management routes blueprint.

Routes:
- POST /chat/select - Select a chat session
- POST /chat/session/<session_id>/mark-read - Mark session as read
- GET /chat/unread-counts - Get unread counts for sessions
- GET /chat/history - Get chat history with pagination
- GET /chat/session/<session_id> - Get a single chat session
- POST /chat/delete - Delete a chat session
- POST /chat/delete-all - Delete all chat sessions
- POST /chat/rename - Rename a chat session
"""

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from app.config import db
from app.limiter import limiter, csrf_required
from app.blueprints.shared import (
    login_required, guest_allowed, error_handler, make_etag_response,
    is_guest, chat_session
)

logger = logging.getLogger(__name__)

session_bp = Blueprint('session', __name__)


def categorize_sessions(chat_history):
    """Categorize chat sessions by time period.

    This helper is used by get_chat_history to organize sessions.
    """
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    today = now.date()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)

    categories = {
        'pinned': [],
        'today': [],
        'yesterday': [],
        'previous_7_days': [],
        'older': []
    }

    for chat in chat_history:
        # Check for pinned sessions first
        if chat.get('pinned'):
            categories['pinned'].append(chat)
            continue

        last_updated = chat.get('last_updated')
        if isinstance(last_updated, datetime):
            chat_date = last_updated.date()
        elif isinstance(last_updated, str):
            try:
                chat_date = datetime.fromisoformat(last_updated.replace('Z', '+00:00')).date()
            except (ValueError, AttributeError):
                chat_date = None
        else:
            chat_date = None

        if chat_date == today:
            categories['today'].append(chat)
        elif chat_date == yesterday:
            categories['yesterday'].append(chat)
        elif chat_date and chat_date > week_ago:
            categories['previous_7_days'].append(chat)
        else:
            categories['older'].append(chat)

    return categories


def delete_local_attachments(messages):
    """Delete local attachment files for a session's messages.

    This helper cleans up uploaded files when a session is deleted.
    """
    import os
    from flask import current_app

    if not messages:
        return

    for msg in messages:
        attachments = msg.get('attachments', [])
        for attachment in attachments:
            file_path = attachment.get('path')
            if file_path and file_path.startswith('/uploads/'):
                # Convert URL path to filesystem path
                upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
                filename = os.path.basename(file_path)
                full_path = os.path.join(upload_folder, filename)
                try:
                    if os.path.exists(full_path):
                        os.remove(full_path)
                        logger.debug(f"Deleted attachment: {full_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete attachment {full_path}: {e}")


def _adjust_user_session_total(user_id, delta):
    """Adjust the user's total session count by delta."""
    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()
        if user_doc.exists:
            current_total = user_doc.to_dict().get('total_sessions', 0)
            new_total = max(0, current_total + delta)
            user_ref.update({'total_sessions': new_total})
    except Exception as e:
        logger.warning(f"Failed to adjust session total for user {user_id}: {e}")


@session_bp.route('/chat/select', methods=['POST'])
@csrf_required
@error_handler
@guest_allowed
def select_chat_session():
    """Select a chat session to make it current."""
    data = request.get_json()
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({'error': 'Session ID is required.'}), 400

    # Guest mode: just set session ID without Firestore check
    if is_guest():
        session['current_session_id'] = session_id
        return jsonify({'message': 'Guest session selected.'}), 200

    user_id = session['user']['uid']
    try:
        session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
        try:
            session_doc = session_ref.get(timeout=2, retry=None)
        except TypeError:
            session_doc = session_ref.get()
        if not session_doc.exists:
            return jsonify({'error': 'Chat session not found.'}), 404

        session['current_session_id'] = session_id
        return jsonify({'message': 'Chat session selected successfully.'}), 200
    except Exception as e:
        logger.error(f"Error selecting chat session {session_id} for user {user_id}: {e}")
        return jsonify({'error': 'Failed to select chat session.'}), 500


@session_bp.route('/chat/session/<session_id>/mark-read', methods=['POST'])
@csrf_required
@error_handler
@login_required
def mark_session_read(session_id):
    """Mark a chat session as read, clearing unread count."""
    user_id = session['user']['uid']

    try:
        session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
        try:
            session_doc = session_ref.get(timeout=2, retry=None)
        except TypeError:
            session_doc = session_ref.get()

        if not session_doc.exists:
            return jsonify({'error': 'Chat session not found.'}), 404

        session_ref.update({
            'unread_count': 0,
            'last_read_at': datetime.utcnow()
        })

        return jsonify({'success': True}), 200
    except Exception as e:
        logger.error(f"Failed to mark session {session_id} read for user {user_id}: {e}")
        return jsonify({'error': 'Failed to mark session read'}), 500


@session_bp.route('/chat/unread-counts', methods=['GET'])
@error_handler
@login_required
def get_unread_counts():
    """Get last_message_at timestamps for all active sessions.

    Returns timestamps for client-side comparison with local lastReadAt.
    This enables per-device unread tracking without shared server state.
    """
    user_id = session['user']['uid']

    try:
        sessions_ref = db.collection('users').document(user_id).collection('chat_sessions')
        # Only fetch the fields we need, and exclude archived sessions
        query = sessions_ref.where('status', '!=', 'archived').select(
            ['last_message_at', 'last_updated', 'last_read_at', 'status']
        )
        # Avoid hanging the UI if Firestore is slow/quota-limited.
        try:
            all_sessions = query.stream(timeout=2, retry=None)
        except TypeError:
            all_sessions = query.stream()

        session_timestamps = {}
        for doc in all_sessions:
            data = doc.to_dict()
            # Use last_message_at if available, otherwise fall back to last_updated
            timestamp = data.get('last_message_at') or data.get('last_updated')
            if timestamp:
                entry = {
                    'last_message_at': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
                }
                # Include server-side last_read_at for cross-device sync
                read_at = data.get('last_read_at')
                if read_at:
                    entry['last_read_at'] = read_at.isoformat() if hasattr(read_at, 'isoformat') else str(read_at)
                session_timestamps[doc.id] = entry

        return make_etag_response({'session_timestamps': session_timestamps})
    except Exception as e:
        logger.error(f"Failed to get session timestamps for user {user_id}: {e}")
        return jsonify({'error': 'Failed to get session timestamps'}), 500


@session_bp.route('/chat/history', methods=['GET'])
@error_handler
@login_required
def get_chat_history():
    """Get paginated chat history for the user."""
    user_id = session['user']['uid']
    page_size = request.args.get('page_size', default=10, type=int)
    page_token = request.args.get('page_token', default=None, type=str)

    history = chat_session.get_history(user_id, page_size, page_token)
    chat_history = history.get('chat_history', [])

    if not isinstance(chat_history, list):
        logger.warning("Invalid 'chat_history' format.")
        raise TypeError("Expected 'chat_history' to be a list of dictionaries.")

    categorized_history = categorize_sessions(chat_history)
    return make_etag_response({
        'categorized_history': categorized_history,
        'next_page_token': history.get('next_page_token')
    })


@session_bp.route('/chat/session/<session_id>', methods=['GET'])
@error_handler
@login_required
def get_single_chat_session(session_id):
    """Get a single chat session by ID."""
    user_id = session['user']['uid']
    session_data = chat_session.get_session(user_id, session_id)
    if not session_data:
        return jsonify({'error': 'Chat session not found.'}), 404
    return make_etag_response({'session': session_data})


@session_bp.route('/chat/delete', methods=['POST'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@login_required
def delete_chat_session():
    """Delete a single chat session."""
    user_id = session['user']['uid']
    data = request.get_json()
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({'error': 'Session ID is required.'}), 400

    try:
        session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
        session_doc = session_ref.get()
        if not session_doc.exists:
            logger.warning(f"Chat session {session_id} not found for user {user_id}")
            return jsonify({'error': 'Chat session not found.'}), 404

        session_data = session_doc.to_dict() or {}
        delete_local_attachments(session_data.get('messages', []))
        session_ref.delete()
        status = session_data.get('status', 'active')
        if status != 'temporary':
            _adjust_user_session_total(user_id, -1)
        if session.get('current_session_id') == session_id:
            session.pop('current_session_id', None)
        logger.info(f"Chat session {session_id} deleted successfully for user {user_id}")
        return jsonify({'message': 'Chat session deleted successfully.'}), 200
    except Exception as e:
        logger.error(f"Error deleting chat session {session_id} for user {user_id}: {e}")
        return jsonify({'error': 'Failed to delete chat session.'}), 500


@session_bp.route('/chat/delete-all', methods=['POST'])
@limiter.limit("3 per minute")
@csrf_required
@error_handler
@login_required
def delete_all_chat_sessions():
    """Remove every chat session stored for the authenticated user."""
    user_id = session['user']['uid']
    try:
        sessions_ref = db.collection('users').document(user_id).collection('chat_sessions')

        # Process in batches to avoid loading all sessions into memory at once
        BATCH_SIZE = 50
        deleted_count = 0
        has_more = True

        while has_more:
            batch_query = sessions_ref.limit(BATCH_SIZE)
            batch_docs = list(batch_query.stream())

            if not batch_docs:
                if deleted_count == 0:
                    session.pop('current_session_id', None)
                    return jsonify({'message': 'No chats to delete.'}), 200
                has_more = False
                break

            for session_doc in batch_docs:
                session_data = session_doc.to_dict() or {}
                try:
                    delete_local_attachments(session_data.get('messages', []))
                except Exception as attachment_err:
                    logger.warning(
                        "Failed to clean up attachments for chat %s (user %s): %s",
                        session_doc.id,
                        user_id,
                        attachment_err
                    )
                try:
                    session_doc.reference.delete()
                    if session_data.get('status') != 'temporary':
                        deleted_count += 1
                except Exception as delete_err:
                    logger.error(
                        "Failed to delete chat session %s for user %s: %s",
                        session_doc.id,
                        user_id,
                        delete_err
                    )

            if len(batch_docs) < BATCH_SIZE:
                has_more = False

        if deleted_count:
            _adjust_user_session_total(user_id, -deleted_count)
        session.pop('current_session_id', None)
        logger.info("Deleted %s chat sessions for user %s", deleted_count, user_id)
        return jsonify({'message': f'Deleted {deleted_count} chat(s).'}), 200
    except Exception as e:
        logger.error(f"Error deleting all chat sessions for user {user_id}: {e}")
        return jsonify({'error': 'Failed to delete all chat sessions.'}), 500


@session_bp.route('/chat/rename', methods=['POST'])
@csrf_required
@error_handler
@login_required
def rename_chat_session():
    """Rename a chat session."""
    user_id = session['user']['uid']
    data = request.get_json()
    session_id = data.get('session_id')
    new_name = data.get('name')

    if not session_id or not new_name or not new_name.strip():
        return jsonify({'error': 'Session ID and new name are required.'}), 400

    try:
        session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
        doc = session_ref.get()
        if not doc.exists:
            logger.warning(f"Chat session {session_id} not found for user {user_id}")
            return jsonify({'error': 'Chat session not found.'}), 404

        session_ref.update({'session_name': new_name.strip()})
        logger.info(f"Chat session {session_id} renamed to '{new_name.strip()}' for user {user_id}")

        updated_doc = session_ref.get()
        updated_name = updated_doc.to_dict().get('session_name')

        return jsonify({
            'message': 'Chat session renamed successfully.',
            'session_id': session_id,
            'updated_name': updated_name
        }), 200

    except Exception as e:
        logger.error(f"Error renaming chat session {session_id} for user {user_id}: {e}")
        return jsonify({'error': 'Failed to rename chat session due to a server error.'}), 500
