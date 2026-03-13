"""Email draft routes blueprint.

Routes:
- GET /email-drafts - List all email drafts
- GET /email-drafts/<draft_id> - Get specific email draft
- POST /email-drafts - Save or update an email draft
- DELETE /email-drafts/<draft_id> - Delete an email draft
"""

import logging

from flask import Blueprint, jsonify, request, session
from google.cloud import firestore as gc_firestore

from app.config import db
from app.limiter import csrf_required
from app.blueprints.shared import login_required, error_handler

logger = logging.getLogger(__name__)

email_bp = Blueprint('email', __name__)


@email_bp.route('/email-drafts', methods=['GET'])
@error_handler
@login_required
def list_email_drafts():
    """List all email drafts for the current user."""
    user_id = session['user']['uid']
    try:
        drafts_ref = db.collection('users').document(user_id).collection('email_drafts')
        drafts = drafts_ref.order_by('updatedAt', direction=gc_firestore.Query.DESCENDING).limit(50).stream()

        result = []
        for draft in drafts:
            data = draft.to_dict()
            data['id'] = draft.id
            result.append(data)

        return jsonify({'drafts': result})
    except Exception as exc:
        logger.error(f"Failed to list email drafts for user {user_id}: {exc}")
        return jsonify({'error': 'Failed to load email drafts.'}), 500


@email_bp.route('/email-drafts/<draft_id>', methods=['GET'])
@error_handler
@login_required
def get_email_draft(draft_id: str):
    """Get a specific email draft."""
    user_id = session['user']['uid']
    try:
        draft_ref = db.collection('users').document(user_id).collection('email_drafts').document(draft_id)
        draft = draft_ref.get()

        if not draft.exists:
            return jsonify({'error': 'Draft not found.'}), 404

        data = draft.to_dict()
        data['id'] = draft.id
        return jsonify({'draft': data})
    except Exception as exc:
        logger.error(f"Failed to get email draft {draft_id} for user {user_id}: {exc}")
        return jsonify({'error': 'Failed to load email draft.'}), 500


@email_bp.route('/email-drafts', methods=['POST'])
@csrf_required
@error_handler
@login_required
def save_email_draft():
    """Save or update an email draft."""
    user_id = session['user']['uid']
    data = request.get_json() or {}

    draft_id = data.get('id')
    if not draft_id:
        return jsonify({'error': 'Draft ID is required.'}), 400

    try:
        draft_data = {
            'to': data.get('to', ''),
            'subject': data.get('subject', ''),
            'bodyHtml': data.get('bodyHtml', ''),
            'bodyText': data.get('bodyText', ''),
            'updatedAt': gc_firestore.SERVER_TIMESTAMP
        }

        draft_ref = db.collection('users').document(user_id).collection('email_drafts').document(draft_id)
        draft_ref.set(draft_data, merge=True)

        return jsonify({'success': True, 'id': draft_id})
    except Exception as exc:
        logger.error(f"Failed to save email draft {draft_id} for user {user_id}: {exc}")
        return jsonify({'error': 'Failed to save email draft.'}), 500


@email_bp.route('/email-drafts/<draft_id>', methods=['DELETE'])
@csrf_required
@error_handler
@login_required
def delete_email_draft(draft_id: str):
    """Delete an email draft."""
    user_id = session['user']['uid']
    try:
        draft_ref = db.collection('users').document(user_id).collection('email_drafts').document(draft_id)
        draft_ref.delete()

        return jsonify({'success': True})
    except Exception as exc:
        logger.error(f"Failed to delete email draft {draft_id} for user {user_id}: {exc}")
        return jsonify({'error': 'Failed to delete email draft.'}), 500
