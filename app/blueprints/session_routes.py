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
from google.cloud import firestore as gc_firestore

from app.config import db
from app.limiter import limiter, csrf_required
from app.chat_session import ALLOWED_PATCH_FIELDS
from app.blueprints.shared import (
    login_required, guest_allowed, error_handler, make_etag_response,
    is_guest, chat_session
)

# FieldFilter is the modern query syntax (Firestore SDK >=2.11.0). Fall back to
# legacy positional `where()` if the import fails, so RAG purge keeps working
# instead of silently skipping on older SDKs.
try:
    from google.cloud.firestore_v1.base_query import FieldFilter
    _HAS_FIELD_FILTER = True
except Exception:  # pragma: no cover - depends on SDK version
    FieldFilter = None
    _HAS_FIELD_FILTER = False

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
    """Delete local attachment files referenced by a session's messages.

    Checks `local_path` (the canonical field used by chat_routes/image generator)
    first, falling back to legacy `path`. Each resolved path is joined against
    UPLOAD_FOLDER and verified to stay inside it before any removal.
    """
    import os
    from flask import current_app

    if not messages:
        return

    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    base_abs = os.path.abspath(upload_folder)

    for msg in messages:
        for attachment in msg.get('attachments', []) or []:
            rel = attachment.get('local_path')
            if not rel:
                legacy = attachment.get('path')
                if isinstance(legacy, str) and legacy.startswith('/uploads/'):
                    rel = legacy[len('/uploads/'):]
                elif isinstance(legacy, str) and not legacy.startswith('/'):
                    rel = legacy
            if not rel or not isinstance(rel, str):
                continue

            full_path = os.path.abspath(os.path.join(upload_folder, rel))
            if full_path != base_abs and not full_path.startswith(base_abs + os.sep):
                logger.warning("Skipping out-of-bounds attachment path: %s", rel)
                continue
            try:
                if os.path.isfile(full_path):
                    os.remove(full_path)
                    logger.debug("Deleted attachment: %s", full_path)
            except Exception as e:
                logger.warning("Failed to delete attachment %s: %s", full_path, e)


_BATCH_CHUNK_SIZE = 400  # Firestore hard limit is 500; leave headroom.


def _commit_refs_batched(refs):
    """Delete every DocumentReference via chunked WriteBatch commits."""
    if not refs:
        return
    batch = db.batch()
    ops = 0
    for ref in refs:
        batch.delete(ref)
        ops += 1
        if ops >= _BATCH_CHUNK_SIZE:
            try:
                batch.commit()
            except Exception as commit_err:
                logger.warning("Batch commit failed: %s", commit_err)
            batch = db.batch()
            ops = 0
    if ops > 0:
        try:
            batch.commit()
        except Exception as commit_err:
            logger.warning("Final batch commit failed: %s", commit_err)


def _delete_session_subcollections(session_ref):
    """Delete artifacts/versions/files under a chat session doc.

    Firestore does not cascade-delete subcollections when the parent document
    is deleted, so without this walk the artifact data would be orphaned.
    Deletes bottom-up (files → versions → artifacts) via batched writes.
    """
    file_refs = []
    version_refs = []
    artifact_refs = []
    try:
        for artifact_doc in session_ref.collection('artifacts').stream():
            artifact_ref = artifact_doc.reference
            artifact_refs.append(artifact_ref)
            try:
                for version_doc in artifact_ref.collection('versions').stream():
                    version_ref = version_doc.reference
                    version_refs.append(version_ref)
                    try:
                        for file_doc in version_ref.collection('files').stream():
                            file_refs.append(file_doc.reference)
                    except Exception as files_err:
                        logger.warning(
                            "Failed to list files under version %s: %s",
                            version_ref.id, files_err
                        )
            except Exception as versions_err:
                logger.warning(
                    "Failed to list versions for artifact %s: %s",
                    artifact_ref.id, versions_err
                )
    except Exception as e:
        logger.warning("Failed to walk artifacts subcollection: %s", e)
        return

    _commit_refs_batched(file_refs)
    _commit_refs_batched(version_refs)
    _commit_refs_batched(artifact_refs)


def _remove_disk_file(rel_path, base_abs):
    """Safely remove an upload file, enforcing containment inside base_abs."""
    import os
    if not rel_path or not isinstance(rel_path, str):
        return
    full_path = os.path.abspath(os.path.join(base_abs, rel_path))
    if full_path != base_abs and not full_path.startswith(base_abs + os.sep):
        logger.warning("Skipping out-of-bounds upload path: %s", rel_path)
        return
    try:
        if os.path.isfile(full_path):
            os.remove(full_path)
            logger.debug("Deleted upload: %s", full_path)
    except Exception as e:
        logger.warning("Failed to delete upload %s: %s", full_path, e)


def _where_conversation(collection_ref, session_id):
    """Build a `conversation_id == session_id` query across SDK versions."""
    if _HAS_FIELD_FILTER:
        return collection_ref.where(
            filter=FieldFilter('conversation_id', '==', session_id)
        )
    return collection_ref.where('conversation_id', '==', session_id)


def _delete_session_files_and_chunks(user_id, session_id):
    """Purge RAG file metadata, embeddings, and physical upload files.

    Users upload docs that get indexed into `users/{uid}/files` and
    `users/{uid}/file_chunks`, both keyed by `conversation_id`. The files
    collection carries a `file_path` field (relative to UPLOAD_FOLDER) which
    points to the actual bytes on disk; those must be removed too.
    """
    import os
    from flask import current_app

    user_ref = db.collection('users').document(user_id)

    chunk_refs = []
    try:
        chunks_query = _where_conversation(
            user_ref.collection('file_chunks'), session_id
        )
        for chunk_doc in chunks_query.stream():
            chunk_refs.append(chunk_doc.reference)
    except Exception as e:
        logger.warning(
            "Failed to list file_chunks for session %s: %s", session_id, e
        )

    file_refs = []
    disk_paths = []
    try:
        files_query = _where_conversation(
            user_ref.collection('files'), session_id
        )
        for file_doc in files_query.stream():
            file_refs.append(file_doc.reference)
            data = file_doc.to_dict() or {}
            fp = data.get('file_path')
            if fp:
                disk_paths.append(fp)
    except Exception as e:
        logger.warning(
            "Failed to list files for session %s: %s", session_id, e
        )

    if disk_paths:
        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
        base_abs = os.path.abspath(upload_folder)
        for rel in disk_paths:
            _remove_disk_file(rel, base_abs)

    _commit_refs_batched(chunk_refs)
    _commit_refs_batched(file_refs)
    if chunk_refs or file_refs or disk_paths:
        logger.info(
            "Purged RAG data for session %s: %s chunks, %s files, %s disk files",
            session_id, len(chunk_refs), len(file_refs), len(disk_paths)
        )


def _clear_session_caches(user_id, session_id):
    """Evict every in-memory cache keyed by this chat session."""
    try:
        from app.conversation_context import clear_conversation_context
        clear_conversation_context(session_id)
    except Exception as e:
        logger.debug("clear_conversation_context failed for %s: %s", session_id, e)
    try:
        from app.list_registry import clear_registry_cache
        clear_registry_cache(user_id=user_id, session_id=session_id)
    except Exception as e:
        logger.debug("clear_registry_cache failed for %s: %s", session_id, e)
    try:
        from app.canonical_store import clear_store_cache
        clear_store_cache(user_id=user_id, session_id=session_id)
    except Exception as e:
        logger.debug("clear_store_cache failed for %s: %s", session_id, e)
    try:
        from app.response_guard import clear_response_guard
        clear_response_guard(user_id=user_id, session_id=session_id)
    except Exception as e:
        logger.debug("clear_response_guard failed for %s: %s", session_id, e)
    try:
        from app.smart_guard import clear_smart_guard
        clear_smart_guard(user_id=user_id, session_id=session_id)
    except Exception as e:
        logger.debug("clear_smart_guard failed for %s: %s", session_id, e)
    try:
        from app.context.artifact_state_tracker import clear_tracker_for_session
        clear_tracker_for_session(session_id)
    except Exception as e:
        logger.debug("clear_tracker_for_session failed for %s: %s", session_id, e)


def _adjust_user_session_total(user_id, delta):
    """Atomically adjust the user's total session count by `delta`.

    Uses Firestore's `Increment` so concurrent delete operations (e.g.
    single-delete racing delete-all, or two open tabs) can't lose decrements
    the way a read-modify-write would. Matches the helper in chat_session.py.
    """
    if not user_id or delta == 0:
        return
    try:
        db.collection('users').document(user_id).set(
            {'total_sessions': gc_firestore.Increment(delta)},
            merge=True
        )
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

    # Temporary chat ("incognito") sessions never have a Firestore document
    # by design — mirrors guest_mode. Skip the existence check; the temp_
    # prefix identifies them and setting the cookie is enough. Without
    # this branch, /chat/select 404s for every incognito chat, the
    # frontend's switchSession shows "Failed to switch chat session" toast,
    # and the user sees a phantom error notification on every ghost click.
    if isinstance(session_id, str) and session_id.startswith('temp_'):
        session['current_session_id'] = session_id
        return jsonify({'message': 'Temporary session selected.'}), 200

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
    """Get paginated chat history for the user, optionally filtered by project."""
    user_id = session['user']['uid']
    page_size = request.args.get('page_size', default=10, type=int)
    page_token = request.args.get('page_token', default=None, type=str)
    project_id = request.args.get('project_id', default=None, type=str)
    ungrouped = request.args.get('ungrouped', default='0') == '1'

    history = chat_session.get_history(
        user_id, page_size, page_token,
        project_id=project_id,
        ungrouped=ungrouped,
    )
    chat_history = history.get('chat_history', [])

    if not isinstance(chat_history, list):
        logger.warning("Invalid 'chat_history' format.")
        raise TypeError("Expected 'chat_history' to be a list of dictionaries.")

    categorized_history = categorize_sessions(chat_history)
    payload = {
        'categorized_history': categorized_history,
        'next_page_token': history.get('next_page_token')
    }
    if ungrouped and not project_id:
        payload['grouped_count'] = chat_session.count_grouped(user_id)
    return make_etag_response(payload)


@session_bp.route('/chat/list-metadata', methods=['GET'])
@limiter.limit("30 per minute")
@error_handler
@login_required
def chat_list_metadata():
    """Return flat metadata array of all sessions for the current user.

    Used by the /chats page. No pagination, no message bodies.
    """
    user_id = session['user']['uid']
    sessions = chat_session.list_metadata(user_id)
    response = jsonify({'sessions': sessions})
    response.headers['Cache-Control'] = 'no-store'
    return response


@session_bp.route('/chat/<session_id>', methods=['PATCH'])
@limiter.limit("60 per minute")
@csrf_required
@error_handler
@login_required
def chat_patch_session(session_id):
    """Update starred / pinned / title / tags on a session.

    Body: any subset of {starred, pinned, title, tags}.
    Other fields are silently ignored (whitelist enforced at both layers).
    """
    user_id = session['user']['uid']
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'error': 'Invalid body'}), 400
    cleaned = {k: v for k, v in body.items() if k in ALLOWED_PATCH_FIELDS}
    ok = chat_session.update_session_metadata(user_id, session_id, cleaned)
    if not ok:
        return jsonify({'error': 'Session not found'}), 404
    return ('', 204)


@session_bp.route('/chat/bulk-delete', methods=['POST'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@login_required
def chat_bulk_delete():
    """Delete multiple sessions in a single batched operation.

    Body: {"session_ids": ["...", "..."]}
    Returns: {"deleted": <count>}
    """
    user_id = session['user']['uid']
    body = request.get_json(silent=True) or {}
    session_ids = body.get('session_ids')
    if not isinstance(session_ids, list) or not session_ids:
        return jsonify({'error': 'session_ids required (non-empty list)'}), 400
    if not all(isinstance(s, str) for s in session_ids):
        return jsonify({'error': 'session_ids must be strings'}), 400
    count = chat_session.bulk_delete_sessions(user_id, session_ids)
    return jsonify({'deleted': count})


@session_bp.route('/chat/session/<session_id>', methods=['GET'])
@error_handler
@login_required
def get_single_chat_session(session_id):
    """Get a single chat session by ID."""
    # Temp ("incognito") sessions never have a Firestore doc by design.
    # Returning 404 with "Chat session not found" surfaces a "Failed to
    # load shared session" toast on every page reload after an incognito
    # session leaves a stale cookie. Return 410 Gone with empty session
    # data — the canonical "this resource existed in your browser
    # session but is intentionally unrecoverable" status.
    if isinstance(session_id, str) and session_id.startswith('temp_'):
        return jsonify({'error': 'Temporary session — not retained'}), 410

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
        status = session_data.get('status', 'active')
        messages = session_data.get('messages', [])

        # Delete the session doc FIRST so the user-visible state flips to
        # "deleted" atomically. If the supporting-data cleanup below fails,
        # orphaned subcollection/RAG/attachment data is left behind (invisible
        # to the user) rather than a zombie session doc with missing content.
        session_ref.delete()
        _clear_session_caches(user_id, session_id)
        if status != 'temporary':
            _adjust_user_session_total(user_id, -1)
        if session.get('current_session_id') == session_id:
            session.pop('current_session_id', None)

        # Best-effort cleanup of supporting data — never raises past here.
        try:
            delete_local_attachments(messages)
        except Exception as attachment_err:
            logger.warning(
                "Failed to clean up attachments for chat %s (user %s): %s",
                session_id, user_id, attachment_err
            )
        try:
            _delete_session_subcollections(session_ref)
        except Exception as sub_err:
            logger.warning(
                "Failed to purge subcollections for chat %s (user %s): %s",
                session_id, user_id, sub_err
            )
        try:
            _delete_session_files_and_chunks(user_id, session_id)
        except Exception as rag_err:
            logger.warning(
                "Failed to purge RAG data for chat %s (user %s): %s",
                session_id, user_id, rag_err
            )

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

        # Process in batches to avoid loading all sessions into memory at once.
        # MAX_ITERATIONS caps runaway loops if deletes keep failing and the same
        # docs reappear in each query (rare, but possible under transient
        # Firestore errors or rule changes mid-request).
        BATCH_SIZE = 50
        MAX_ITERATIONS = 200  # Ceiling: 10_000 sessions per request.
        deleted_count = 0      # Non-temporary sessions (drives total_sessions).
        deleted_total = 0      # Every physical session removed (user-facing).
        failed_ids = set()     # Unique session IDs whose delete() call raised.
        has_more = True
        iterations = 0
        prev_batch_ids = None
        truncated = False
        stalled = False

        while has_more:
            iterations += 1
            if iterations > MAX_ITERATIONS:
                logger.warning(
                    "delete_all_chat_sessions hit MAX_ITERATIONS=%s for user %s "
                    "(deleted_total=%s); aborting to avoid infinite loop",
                    MAX_ITERATIONS, user_id, deleted_total
                )
                truncated = True
                break

            batch_query = sessions_ref.limit(BATCH_SIZE)
            batch_docs = list(batch_query.stream())

            if not batch_docs:
                if deleted_total == 0:
                    session.pop('current_session_id', None)
                    return jsonify({
                        'message': 'No chats to delete.',
                        'deleted_count': 0,
                        'deleted_total': 0,
                    }), 200
                has_more = False
                break

            # Abort if the same batch is returned twice in a row — means we
            # made no progress (all deletes failed or were rejected).
            current_batch_ids = frozenset(d.id for d in batch_docs)
            if prev_batch_ids is not None and current_batch_ids == prev_batch_ids:
                logger.warning(
                    "delete_all_chat_sessions stalled for user %s: same %s docs "
                    "returned twice; aborting",
                    user_id, len(current_batch_ids)
                )
                stalled = True
                break
            prev_batch_ids = current_batch_ids

            for session_doc in batch_docs:
                session_data = session_doc.to_dict() or {}
                session_ref = session_doc.reference
                session_id = session_doc.id
                status = session_data.get('status')
                messages = session_data.get('messages', [])

                # Delete the session doc FIRST. If this call raises, we skip
                # the rest of the cleanup so the session stays intact rather
                # than becoming a zombie with missing attachments/artifacts.
                try:
                    session_ref.delete()
                except Exception as delete_err:
                    # Log once per unique ID; stall-detection will break us out
                    # if the same failing docs keep reappearing.
                    if session_id not in failed_ids:
                        logger.error(
                            "Failed to delete chat session %s for user %s: %s",
                            session_id, user_id, delete_err
                        )
                    failed_ids.add(session_id)
                    continue

                deleted_total += 1
                if status != 'temporary':
                    deleted_count += 1

                try:
                    _clear_session_caches(user_id, session_id)
                except Exception as cache_err:
                    logger.debug(
                        "Cache clear failed for chat %s (user %s): %s",
                        session_id, user_id, cache_err
                    )
                try:
                    delete_local_attachments(messages)
                except Exception as attachment_err:
                    logger.warning(
                        "Failed to clean up attachments for chat %s (user %s): %s",
                        session_id, user_id, attachment_err
                    )
                try:
                    _delete_session_subcollections(session_ref)
                except Exception as sub_err:
                    logger.warning(
                        "Failed to purge subcollections for chat %s (user %s): %s",
                        session_id, user_id, sub_err
                    )
                try:
                    _delete_session_files_and_chunks(user_id, session_id)
                except Exception as rag_err:
                    logger.warning(
                        "Failed to purge RAG data for chat %s (user %s): %s",
                        session_id, user_id, rag_err
                    )

            if len(batch_docs) < BATCH_SIZE:
                has_more = False

        if deleted_count:
            _adjust_user_session_total(user_id, -deleted_count)
        session.pop('current_session_id', None)

        failed_count = len(failed_ids)
        partial = truncated or stalled or failed_count > 0
        if truncated:
            reason = 'truncated'
            message = (
                f'Deleted {deleted_total} chat(s). More chats remain — '
                'run Delete all again to continue.'
            )
        elif stalled:
            reason = 'stalled'
            message = (
                f'Deleted {deleted_total} chat(s). Some chats could not be '
                'removed — please retry.'
            )
        elif failed_count:
            reason = 'partial_failure'
            message = (
                f'Deleted {deleted_total} chat(s); {failed_count} could not '
                'be removed — please retry.'
            )
        else:
            reason = None
            message = f'Deleted {deleted_total} chat(s).'

        logger.info(
            "Deleted %s chat sessions for user %s (countable=%s, failed=%s, "
            "truncated=%s, stalled=%s)",
            deleted_total, user_id, deleted_count, failed_count,
            truncated, stalled
        )

        payload = {
            'message': message,
            'deleted_count': deleted_count,
            'deleted_total': deleted_total,
            'failed_count': failed_count,
            'partial': partial,
        }
        if reason:
            payload['reason'] = reason
        return jsonify(payload), 200
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
