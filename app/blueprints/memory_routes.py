"""Memory management routes blueprint.

Routes:
- GET /user/memories - Get all memories organized by type
- DELETE /user/memories - Clear all memories
- DELETE /user/memories/item - Remove specific memory
- POST /user/memories/pin - Toggle pin status
- GET/POST /user/dream-settings - Get or update dream consolidation settings
- GET /user/memory-activity - Get dream consolidation activity log
- POST /user/memory-activity/<log_id>/undo - Undo a consolidation
"""

import logging

from flask import Blueprint, jsonify, request, session

from app.limiter import limiter, csrf_required
from app.blueprints.shared import login_required, error_handler, make_etag_response

logger = logging.getLogger(__name__)

memory_bp = Blueprint('memory', __name__)


@memory_bp.route('/user/memories', methods=['GET'])
@error_handler
@login_required
def get_user_memories():
    """Get all long-term memories organized by type (Memory V2)."""
    from app.memory_v2 import MemoryStoreV2, MemoryType, MemoryStatus

    user_id = session['user']['uid']

    try:
        store = MemoryStoreV2(user_id)
        memories = store.get_all(include_expired=False)

        # Organize by type - use singular keys matching MemoryType.value
        buckets = {
            'profile': [],
            'preference': [],
            'project': [],
            'constraint': [],
            'temporary': []
        }

        for mem in memories:
            if mem.status not in [MemoryStatus.ACTIVE, MemoryStatus.PROVISIONAL]:
                continue

            bucket_name = mem.type.value if mem.type else 'profile'
            if bucket_name not in buckets:
                bucket_name = 'profile'

            # Format display text: "key: value" or just value if key is generic
            if mem.key and mem.key not in ['unknown', 'general']:
                display_text = f"{mem.key}: {mem.value}"
            else:
                display_text = str(mem.value)

            # Access lifecycle timestamps correctly
            created_at = None
            last_used = None
            if hasattr(mem, 'lifecycle') and mem.lifecycle:
                created_at = mem.lifecycle.created_at.isoformat() if mem.lifecycle.created_at else None
                last_used = mem.lifecycle.last_used_at.isoformat() if mem.lifecycle.last_used_at else None

            buckets[bucket_name].append({
                'id': mem.id,
                'key': mem.key,
                'value': mem.value,
                'text': display_text,  # Frontend expects 'text' field
                'confidence': round(mem.confidence, 2) if mem.confidence else 0,
                'pinned': mem.pinned if hasattr(mem, 'pinned') else False,
                'created_at': created_at,
                'last_used': last_used,
                'status': mem.status.value if mem.status else 'active'
            })

        # Metadata for UI with icons and colors
        metadata = {
            'profile': {
                'label': 'Profile',
                'description': 'Personal info like name, location',
                'icon': 'fa-user',
                'color': '#3b82f6'
            },
            'preference': {
                'label': 'Preferences',
                'description': 'How you like things done',
                'icon': 'fa-sliders',
                'color': '#8b5cf6'
            },
            'project': {
                'label': 'Projects',
                'description': 'Work context and goals',
                'icon': 'fa-folder-open',
                'color': '#10b981'
            },
            'constraint': {
                'label': 'Constraints',
                'description': 'Rules and restrictions',
                'icon': 'fa-shield-halved',
                'color': '#f59e0b'
            },
            'temporary': {
                'label': 'Temporary',
                'description': 'Short-lived context',
                'icon': 'fa-clock',
                'color': '#6b7280'
            }
        }

        total = sum(len(items) for items in buckets.values())
        logger.info(f"Memory API: Returning {total} memories for user {user_id}")

        return make_etag_response({
            'buckets': buckets,
            'metadata': metadata,
            'count': total
        })
    except Exception as e:
        logger.error(f"Error fetching memories for user {user_id}: {e}")
        return jsonify({'error': 'Failed to fetch memories.'}), 500


@memory_bp.route('/user/memories', methods=['DELETE'])
@limiter.limit("5 per minute")
@csrf_required
@error_handler
@login_required
def clear_user_memories():
    """Clear all long-term memories for the current user (Memory V2)."""
    from app.memory_v2 import MemoryStoreV2, MemoryStatus

    user_id = session['user']['uid']

    try:
        store = MemoryStoreV2(user_id)
        memories = store.get_all(include_expired=False)

        # Soft delete all memories
        deleted_count = 0
        for mem in memories:
            if mem.status not in [MemoryStatus.DELETED, MemoryStatus.QUARANTINED]:
                mem.status = MemoryStatus.DELETED
                store.save(mem)
                deleted_count += 1

        logger.info(f"Cleared {deleted_count} memories for user {user_id}")
        return jsonify({'message': f'All {deleted_count} memories cleared successfully.'}), 200
    except Exception as e:
        logger.error(f"Error clearing memories for user {user_id}: {e}")
        return jsonify({'error': 'Failed to clear memories.'}), 500


@memory_bp.route('/user/memories/item', methods=['DELETE'])
@limiter.limit("20 per minute")
@csrf_required
@error_handler
@login_required
def remove_user_memory():
    """Remove a specific memory item (Memory V2)."""
    from app.memory_v2 import MemoryStoreV2, MemoryStatus

    user_id = session['user']['uid']
    data = request.get_json()
    memory_id = data.get('id', '').strip()
    memory_text = data.get('text', '').strip()

    if not memory_id and not memory_text:
        return jsonify({'error': 'Memory id or text is required.'}), 400

    try:
        store = MemoryStoreV2(user_id)

        # Find by ID or by value text
        if memory_id:
            mem = store.get(memory_id)
        else:
            # Search by value
            memories = store.get_all(include_expired=False)
            mem = next((m for m in memories if m.value == memory_text), None)

        if not mem:
            return jsonify({'error': 'Memory not found.'}), 404

        # Soft delete
        mem.status = MemoryStatus.DELETED
        store.save(mem)
        logger.info(f"Deleted memory {mem.id} for user {user_id}")

        return jsonify({'message': 'Memory removed successfully.'}), 200
    except Exception as e:
        logger.error(f"Error removing memory for user {user_id}: {e}")
        return jsonify({'error': 'Failed to remove memory.'}), 500


@memory_bp.route('/user/memories/pin', methods=['POST'])
@csrf_required
@error_handler
@login_required
def toggle_memory_pin():
    """Toggle pin status for a memory item (Memory V2)."""
    from app.memory_v2 import MemoryStoreV2

    user_id = session['user']['uid']
    data = request.get_json()
    memory_id = data.get('id', '').strip()
    memory_text = data.get('text', '').strip()

    if not memory_id and not memory_text:
        return jsonify({'error': 'Memory id or text is required.'}), 400

    try:
        store = MemoryStoreV2(user_id)

        # Find by ID or by value text
        if memory_id:
            mem = store.get(memory_id)
        else:
            memories = store.get_all(include_expired=False)
            mem = next((m for m in memories if m.value == memory_text), None)

        if not mem:
            return jsonify({'error': 'Memory not found.'}), 404

        # Toggle pin
        mem.pinned = not mem.pinned
        store.save(mem)

        return jsonify({
            'message': 'Pin status updated.',
            'pinned': mem.pinned
        }), 200
    except Exception as e:
        logger.error(f"Error toggling pin for user {user_id}: {e}")
        return jsonify({'error': 'Failed to update pin status.'}), 500


@memory_bp.route('/user/dream-settings', methods=['GET'])
@error_handler
@login_required
def get_dream_settings():
    """Get dream consolidation settings."""
    from app.config import db

    user_id = session['user']['uid']
    try:
        doc = db.collection('users').document(user_id).get()
        data = doc.to_dict() if doc.exists else {}
        prefs = data.get('preferences', {})
        return jsonify({'dream_enabled': prefs.get('dream_enabled', True)})
    except Exception as e:
        logger.error(f"Failed to get dream settings for {user_id}: {e}")
        return jsonify({'dream_enabled': True})  # Default on


@memory_bp.route('/user/dream-settings', methods=['POST'])
@csrf_required
@error_handler
@login_required
def update_dream_settings():
    """Update dream consolidation settings."""
    from app.config import db

    user_id = session['user']['uid']
    try:
        body = request.get_json(force=True)
        enabled = bool(body.get('dream_enabled', True))
        db.collection('users').document(user_id).set(
            {'preferences': {'dream_enabled': enabled}}, merge=True
        )
        return jsonify({'success': True, 'dream_enabled': enabled})
    except Exception as e:
        logger.error(f"Failed to update dream settings for {user_id}: {e}")
        return jsonify({'error': 'Failed to update settings'}), 500


@memory_bp.route('/user/dream-test', methods=['POST'])
@csrf_required
@error_handler
@login_required
def test_dream_consolidation():
    """TEMPORARY: Force a dream consolidation cycle, bypassing gates. Remove after testing."""
    from app.config import db
    from app.memory_v2.dream import _run_consolidation
    from app.memory_v2.dream.config import DreamConfig

    user_id = session['user']['uid']
    try:
        config = DreamConfig()
        config.min_active_memories = 1  # Lower threshold for testing
        ran = _run_consolidation(db, user_id, config)
        if ran:
            return jsonify({'success': True, 'message': 'Dream consolidation completed. Check the Activity tab.'})
        else:
            return jsonify({'success': False, 'message': 'Not enough memories to consolidate (need at least 1 active memory).'})
    except Exception as e:
        logger.error(f"Test dream failed for {user_id}: {e}")
        return jsonify({'error': str(e)}), 500


@memory_bp.route('/user/memory-activity', methods=['GET'])
@error_handler
@login_required
def get_memory_activity():
    """Get dream consolidation activity log."""
    from app.config import db
    from google.cloud.firestore_v1 import Query

    user_id = session['user']['uid']
    try:
        logs_ref = (
            db.collection('users')
            .document(user_id)
            .collection('dream_logs')
            .order_by('started_at', direction=Query.DESCENDING)
            .limit(20)
        )
        logs = []
        for doc in logs_ref.stream():
            log_data = doc.to_dict()
            log_data['id'] = doc.id
            # Convert datetime fields to ISO strings for JSON
            for field in ('started_at', 'completed_at'):
                if log_data.get(field) and hasattr(log_data[field], 'isoformat'):
                    log_data[field] = log_data[field].isoformat()
            logs.append(log_data)
        return jsonify({'logs': logs})
    except Exception as e:
        logger.error(f"Failed to get dream activity for {user_id}: {e}")
        return jsonify({'error': 'Failed to load activity'}), 500


@memory_bp.route('/user/memory-activity/<log_id>/undo', methods=['POST'])
@limiter.limit("5 per minute")
@csrf_required
@error_handler
@login_required
def undo_memory_activity(log_id):
    """Undo a dream consolidation cycle."""
    from app.config import db
    from app.memory_v2.store import MemoryStoreV2
    from app.memory_v2.dream.report import DreamLog, DreamAction
    from app.memory_v2.dream.executor import undo_dream_log
    from datetime import datetime, timedelta

    user_id = session['user']['uid']
    try:
        log_ref = (
            db.collection('users')
            .document(user_id)
            .collection('dream_logs')
            .document(log_id)
        )
        snapshot = log_ref.get()
        if not snapshot.exists:
            return jsonify({'error': 'Log not found'}), 404

        log_data = snapshot.to_dict()

        # Check undo window (7 days)
        started = log_data.get('started_at')
        if started and hasattr(started, 'timestamp'):
            from datetime import datetime as dt
            started = dt.fromtimestamp(started.timestamp())
        if started and (datetime.utcnow() - started).days > 7:
            return jsonify({'error': 'Undo window expired (7 days)'}), 400

        # Check not already undone
        if log_data.get('undone'):
            return jsonify({'error': 'Already undone'}), 400

        # Reconstruct DreamLog
        def _actions_from_dicts(action_dicts):
            return [
                DreamAction(
                    action_type=a.get('action_type', ''),
                    memory_id=a.get('memory_id', ''),
                    reason=a.get('reason', ''),
                    phase=a.get('phase', 0),
                    before_snapshot=a.get('before_snapshot'),
                    related_ids=a.get('related_ids', []),
                )
                for a in (action_dicts or [])
            ]

        dream_log = DreamLog(
            user_id=user_id,
            started_at=started or datetime.utcnow(),
            phase1_actions=_actions_from_dicts(log_data.get('phase1_actions')),
            phase2_actions=_actions_from_dicts(log_data.get('phase2_actions')),
        )

        store = MemoryStoreV2(user_id)
        success = undo_dream_log(db=db, store=store, dream_log=dream_log)

        if success:
            log_ref.update({'undone': True, 'undone_at': datetime.utcnow()})
            return jsonify({'success': True, 'message': 'Dream actions undone'})
        else:
            return jsonify({'error': 'Nothing to undo'}), 400

    except Exception as e:
        logger.error(f"Failed to undo dream log {log_id} for {user_id}: {e}")
        return jsonify({'error': 'Undo failed'}), 500


# ─────────────────────────────────────────────────────────────────────
# Conversation memory (mempalace turns) — Tier-1 trust contract Week 1
# ─────────────────────────────────────────────────────────────────────

import os
import requests as _requests


def _sidecar_base_url() -> str:
    """Return the base URL for the mempalace sidecar.

    Mirrors how ``app.mempalace.adapter`` resolves the sidecar URL —
    that module is the source of truth for runtime sidecar config.
    """
    return os.getenv("MEMPALACE_SIDECAR_URL", "http://sidecar")


def _sidecar_token() -> str:
    return os.getenv("MEMPALACE_TOKEN", "")


def _sidecar_list_drawers(user_id: str, limit: int = 100, offset: int = 0) -> dict:
    """Call sidecar GET /palace/{user_id}/drawers and return the raw envelope.

    Separated as a single seam so tests can monkey-patch this module-level
    helper without needing to mock the requests library globally.
    """
    url = f"{_sidecar_base_url()}/palace/{user_id}/drawers"
    resp = _requests.get(
        url,
        params={"limit": limit, "offset": offset},
        headers={"X-Sidecar-Token": _sidecar_token()},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


@memory_bp.route('/user/conversation-memory', methods=['GET'])
@error_handler
@login_required
def get_conversation_memory():
    """Get the user's mempalace conversation turns, formatted for the panel.

    Response shape:
        {turns: [{drawer_id, role, text, ts}], next_cursor: str|null}

    role is 'assistant' if text starts with "ACTION: chat"; else 'user'.
    The "ACTION: chat\\n" prefix is stripped for display.
    """
    user_id = session['user']['uid']
    cursor = request.args.get('cursor')
    limit = min(int(request.args.get('limit', 100)), 200)
    offset = int(cursor) if cursor and cursor.isdigit() else 0

    try:
        envelope = _sidecar_list_drawers(user_id, limit=limit, offset=offset)
    except Exception as exc:
        logger.warning("Sidecar drawers list failed: %s", exc)
        return jsonify({"error": "sidecar_unavailable"}), 503

    drawers = envelope.get("drawers", []) or []
    turns = []
    for d in drawers:
        text = (d.get("text") or "").strip()
        if not text:
            continue
        if text.startswith("ACTION: chat"):
            role = "assistant"
            display = text[len("ACTION: chat"):].lstrip("\n").strip()
        else:
            role = "user"
            display = text
        turns.append({
            "drawer_id": d.get("drawer_id"),
            "role": role,
            "text": display,
            "ts": d.get("ts"),
        })

    next_offset = envelope.get("next_offset")
    next_cursor = str(next_offset) if next_offset is not None else None
    return jsonify({"turns": turns, "next_cursor": next_cursor})
