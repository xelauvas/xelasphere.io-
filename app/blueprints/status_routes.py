"""GET /status — slot availability + active voice sessions for frontend health UI."""
from flask import Blueprint, jsonify
from app.backpressure import slot_counts
from app.voice_sessions import count as voice_count

status_bp = Blueprint('status', __name__)


@status_bp.route('/status', methods=['GET'])
def status():
    return jsonify({
        'healthy': True,
        'slots': slot_counts(),
        'voice_sessions': voice_count(),
    })
