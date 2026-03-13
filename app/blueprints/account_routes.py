"""Account management routes blueprint.

Routes:
- GET /account/manage - Account settings page
- GET/POST /account/api-keys - Manage user API keys
- GET /upgrade - Pricing/upgrade page
"""

import logging

from flask import Blueprint, jsonify, render_template, request, session
from google.cloud import firestore as gc_firestore

from app.config import db
from app.encryption import encrypt_api_key
from app.blueprints.shared import login_required, error_handler

logger = logging.getLogger(__name__)

account_bp = Blueprint('account', __name__)


@account_bp.route('/account/manage', methods=['GET'])
@error_handler
@login_required
def manage_account():
    """Account settings page with API key status."""
    user_id = session['user']['uid']
    api_key_state = {'groq': False, 'openrouter': False, 'deepseek': False}
    try:
        snapshot = db.collection('users').document(user_id).get()
        if snapshot.exists:
            data = snapshot.to_dict() or {}
            keys = data.get('api_keys') or {}
            api_key_state['groq'] = bool(keys.get('groq'))
            api_key_state['openrouter'] = bool(keys.get('openrouter'))
            api_key_state['deepseek'] = bool(keys.get('deepseek'))
    except Exception as e:
        logger.warning(f"Unable to load API key settings for {user_id}: {e}")
    return render_template('settings_api_keys.html', api_keys=api_key_state)


@account_bp.route('/account/api-keys', methods=['GET', 'POST'])
@error_handler
@login_required
def account_api_keys():
    """Manage user API keys (get status or update keys)."""
    user_id = session['user']['uid']
    user_ref = db.collection('users').document(user_id)

    if request.method == 'GET':
        try:
            snapshot = user_ref.get()
            data = snapshot.to_dict() if snapshot.exists else {}
            api_keys = data.get('api_keys') or {}
            return jsonify({
                'has_groq_key': bool(api_keys.get('groq')),
                'has_openrouter_key': bool(api_keys.get('openrouter')),
                'has_deepseek_key': bool(api_keys.get('deepseek'))
            })
        except Exception as e:
            logger.error(f"Failed to load API key metadata for {user_id}: {e}")
            return jsonify({'error': 'Unable to load API key state.'}), 500

    payload = request.get_json() or {}
    groq_key = (payload.get('groq_api_key') or '').strip()
    openrouter_key = (payload.get('openrouter_api_key') or '').strip()
    deepseek_key = (payload.get('deepseek_api_key') or '').strip()

    try:
        snapshot = user_ref.get()
        current = snapshot.to_dict().get('api_keys', {}) if snapshot.exists else {}
    except Exception:
        current = {}

    updated_keys = dict(current)
    if payload.get('groq_api_key') is not None:
        if groq_key:
            updated_keys['groq'] = encrypt_api_key(groq_key)
        else:
            updated_keys.pop('groq', None)
    if payload.get('openrouter_api_key') is not None:
        if openrouter_key:
            updated_keys['openrouter'] = encrypt_api_key(openrouter_key)
        else:
            updated_keys.pop('openrouter', None)
    if payload.get('deepseek_api_key') is not None:
        if deepseek_key:
            updated_keys['deepseek'] = encrypt_api_key(deepseek_key)
        else:
            updated_keys.pop('deepseek', None)

    try:
        if updated_keys:
            user_ref.set({'api_keys': updated_keys}, merge=True)
        else:
            user_ref.set({'api_keys': gc_firestore.DELETE_FIELD}, merge=True)
        return jsonify({
            'message': 'API keys updated.',
            'has_groq_key': bool(updated_keys.get('groq')),
            'has_openrouter_key': bool(updated_keys.get('openrouter')),
            'has_deepseek_key': bool(updated_keys.get('deepseek'))
        })
    except Exception as e:
        logger.error(f"Failed to persist API keys for {user_id}: {e}")
        return jsonify({'error': 'Failed to save API keys.'}), 500


@account_bp.route('/upgrade', methods=['GET'])
@error_handler
def upgrade():
    """Pricing/upgrade page."""
    user_data = session.get('user')

    plans = [
        {
            "name": "Free",
            "description": "Experiment, prototype, and chat with the core models we ship inside XelaSphere.",
            "button_label": "Your current plan",
            "button_style": "disabled",
            "tag": "Current plan",
            "is_current": True,
            "highlights": [
                "Chat with the default Llama 3.3 workspace model",
                "Auto-save every session in the sidebar history",
                "Upload PDFs, docs & images with OCR-powered extraction",
                "Trigger Pollinations image generations from any prompt"
            ],
            "pricing": {
                "monthly": {"price": "£0 GBP", "period": "/ month", "note": "Includes VAT"},
                "yearly": {"price": "£0 GBP", "period": "/ year", "note": "Includes VAT"}
            }
        },
        {
            "name": "Plus",
            "description": "Unlock premium models, faster speeds, and richer co-pilot workflows.",
            "button_label": "Upgrade to Plus",
            "button_style": "primary",
            "tag": "Most popular",
            "is_current": False,
            "highlights": [
                "Switch between Qwen 32B, GPT-OSS 120B, Groq & more",
                "Let rolling summaries auto-title and organize long chats",
                "Use the image modal gallery + composer for creative runs",
                "Attach multiple knowledge files for grounded responses",
                "Dashboard quick actions, metrics, and notifications for power users"
            ],
            "pricing": {
                "monthly": {"price": "£20 GBP", "period": "/ month", "note": "Includes VAT"},
                "yearly": {"price": "£200 GBP", "period": "/ year", "note": "Includes VAT (billed annually)"}
            }
        },
        {
            "name": "Business",
            "description": "Secure collaboration workspaces for high-performing teams.",
            "button_label": "Add Business workspace",
            "button_style": "primary",
            "tag": "Recommended",
            "is_current": False,
            "highlights": [
                "Shared Firestore-backed workspaces with categorized sessions",
                "Team dashboard with analytics, notifications, and files feed",
                "Workspace-wide image library plus attachment retention controls",
                "OAuth-based SSO logins with audit trails & session summaries",
                "Admin tools for deleting sessions & cleaning server-side images",
                "Centralized billing with Business workspace controls"
            ],
            "pricing": {
                "monthly": {"price": "£25 GBP", "period": "/ month", "note": "Exclusive of VAT"},
                "yearly": {"price": "£300 GBP", "period": "/ year", "note": "Exclusive of VAT (billed annually)"}
            }
        }
    ]

    plan_lookup = {plan['name'].lower(): plan for plan in plans}

    return render_template(
        'upgrade.html',
        user=user_data,
        free_plan=plan_lookup.get('free'),
        plus_plan=plan_lookup.get('plus'),
        business_plan=plan_lookup.get('business')
    )
