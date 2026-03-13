"""Memory management routes blueprint.

Routes:
- GET /user/memories - Get all memories organized by type
- DELETE /user/memories - Clear all memories
- DELETE /user/memories/item - Remove specific memory
- POST /user/memories/pin - Toggle pin status
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
