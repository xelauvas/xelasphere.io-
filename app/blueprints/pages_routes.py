"""Page routes blueprint for main views.

Routes:
- GET / - Main index page
- GET /c/<session_id> - Open shared/existing session
- POST /more-options - Get more task options
- GET /favicon.ico - Serve favicon
- POST /debug - Debug code endpoint
"""

import logging
import os
import traceback
import uuid
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory, session

from app.config import db
from app.limiter import csrf_required
from app.blueprints.shared import (
    login_required, guest_allowed, error_handler, task_generator,
    is_guest, get_guest_message_count, get_client_ip, hash_ip,
    GUEST_MESSAGE_LIMIT, GUEST_BANNER_THRESHOLD
)

logger = logging.getLogger(__name__)

pages_bp = Blueprint('pages', __name__)


def _is_mobile():
    """Check if the request is from a mobile device."""
    ua = request.headers.get("User-Agent", "").lower()
    return any(kw in ua for kw in ("iphone", "android", "mobile"))


def check_guest_ip_limit(ip_hash):
    """Check if IP has exceeded guest message limit.

    Returns (is_blocked, message_count) tuple.
    """
    try:
        doc_ref = db.collection('guest_ips').document(ip_hash)
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            count = data.get('message_count', 0)
            return count >= GUEST_MESSAGE_LIMIT, count
        return False, 0
    except Exception as e:
        logger.warning(f"Failed to check guest IP limit: {e}")
        return False, 0


def fetch_user_preferences(user_id):
    """Fetch user preferences for personalized suggestions."""
    try:
        user_doc = db.collection('users').document(user_id).get()
        if user_doc.exists:
            data = user_doc.to_dict()
            return data.get('preferences', {})
        return {}
    except Exception as e:
        logger.warning(f"Failed to fetch user preferences for {user_id}: {e}")
        return {}


@pages_bp.route('/', methods=['GET'])
@error_handler
def index():
    """Main index page."""
    try:
        # Check if user is logged in, if not set up guest mode
        guest_mode = 'user' not in session
        if guest_mode and 'guest' not in session:
            # Initialize guest session
            session['guest'] = {
                'id': f'guest_{uuid.uuid4().hex[:12]}',
                'message_count': 0,
                'created_at': datetime.utcnow().isoformat()
            }
            session['guest_messages'] = []
            session.modified = True

        # Mobile gets 7 suggestions (no More), desktop gets 3 + More = 4 (2x2 grid)
        mobile = _is_mobile()
        num_suggestions = 6 if mobile else 3

        # Use fallback options for guests (no API call), full options for logged-in users
        if guest_mode:
            options = task_generator.get_fallback_options(num_tasks=num_suggestions, include_more_option=True)

            # Get accurate guest message count from Firebase (IP-based)
            client_ip = get_client_ip()
            ip_hash = hash_ip(client_ip)
            _, ip_count = check_guest_ip_limit(ip_hash)
            session_count = get_guest_message_count()
            guest_count = max(ip_count, session_count)

            # Sync session with Firebase count if Firebase is higher
            if ip_count > session_count:
                session['guest']['message_count'] = ip_count
                session.modified = True
        else:
            # Serve precomputed smart suggestions pool
            user_id = session['user']['uid']
            try:
                from app.services.smart_suggestions import (
                    load_pool, select_display_slots, get_signal_based_suggestions,
                    get_pool_metadata, should_refresh_pool, refresh_pool_background,
                    increment_shown_counts,
                )
                from app.grog_api import TaskOption as _TaskOption
                per_slot = 3 if mobile else 1  # 3 per slot = ~7-9 for mobile, 1 per slot = 3 for desktop
                pool = load_pool(db, user_id)
                if pool:
                    selected = select_display_slots(pool, per_slot=per_slot)
                    if mobile:
                        selected = selected[:6]  # cap at 7 for mobile
                    options = [s.to_task_option() for s in selected]
                    shown_titles = [s.title for s in selected]
                    increment_shown_counts(db, user_id, shown_titles)
                    metadata = get_pool_metadata(db, user_id)
                    if should_refresh_pool(metadata):
                        import threading
                        threading.Thread(
                            target=refresh_pool_background,
                            args=(db, user_id, "expired"),
                            daemon=True,
                        ).start()
                else:
                    hour = datetime.utcnow().hour
                    day = datetime.utcnow().strftime("%A")
                    ua = request.headers.get("User-Agent", "")
                    selected = get_signal_based_suggestions(hour=hour, day_of_week=day, user_agent=ua, per_slot=per_slot)
                    if mobile:
                        selected = selected[:6]
                    options = [s.to_task_option() for s in selected]
                    import threading
                    threading.Thread(
                        target=refresh_pool_background,
                        args=(db, user_id, "first_conversation"),
                        daemon=True,
                    ).start()
                # Desktop: append "More" button for 2x2 grid. Mobile: no More (CSS hides it anyway)
                options.append(_TaskOption(
                    title="More", subtitle="Load new ideas",
                    action="fetchMoreOptions()", icon="more",
                ))
            except Exception as e:
                logger.warning(f"Smart suggestions failed, using fallback: {e}")
                user_preferences = fetch_user_preferences(user_id)
                options = task_generator.get_fallback_options(num_tasks=num_suggestions, include_more_option=True, user_preferences=user_preferences)
            guest_count = 0

        return render_template(
            'index.html',
            options=options,
            shared_session_id=None,
            is_guest=guest_mode,
            guest_message_count=guest_count,
            guest_message_limit=GUEST_MESSAGE_LIMIT,
            guest_banner_threshold=GUEST_BANNER_THRESHOLD,
            session_has_messages=False
        )
    except Exception as e:
        logger.error(f"Failed to load the index page: {e}")
        return jsonify({'error': 'Failed to load the index page.'}), 500


@pages_bp.route('/c/<session_id>', methods=['GET'])
@error_handler
@guest_allowed
def open_shared_session(session_id):
    """Open a shared or existing session by ID."""
    try:
        # Check if this is a guest session
        guest_mode = is_guest() or session_id.startswith('guest_')
        session_has_messages = False

        # Mobile gets 7 suggestions (no More), desktop gets 3 + More = 4 (2x2 grid)
        mobile = _is_mobile()
        num_suggestions = 6 if mobile else 3

        # Use fallback options for guests (no API call), full options for logged-in users
        if guest_mode:
            options = task_generator.get_fallback_options(num_tasks=num_suggestions, include_more_option=True)

            # Get accurate guest message count from Firebase (IP-based)
            client_ip = get_client_ip()
            ip_hash = hash_ip(client_ip)
            _, ip_count = check_guest_ip_limit(ip_hash)
            session_count = get_guest_message_count()
            guest_count = max(ip_count, session_count)

            # Sync session with Firebase count if Firebase is higher
            if ip_count > session_count and 'guest' in session:
                session['guest']['message_count'] = ip_count
                session.modified = True
        else:
            # Serve precomputed smart suggestions pool
            user_id = session['user']['uid']
            try:
                from app.services.smart_suggestions import (
                    load_pool, select_display_slots, get_signal_based_suggestions,
                    get_pool_metadata, should_refresh_pool, refresh_pool_background,
                    increment_shown_counts,
                )
                from app.grog_api import TaskOption as _TaskOption
                per_slot = 3 if mobile else 1
                pool = load_pool(db, user_id)
                if pool:
                    selected = select_display_slots(pool, per_slot=per_slot)
                    if mobile:
                        selected = selected[:6]
                    options = [s.to_task_option() for s in selected]
                    shown_titles = [s.title for s in selected]
                    increment_shown_counts(db, user_id, shown_titles)
                    metadata = get_pool_metadata(db, user_id)
                    if should_refresh_pool(metadata):
                        import threading
                        threading.Thread(
                            target=refresh_pool_background,
                            args=(db, user_id, "expired"),
                            daemon=True,
                        ).start()
                else:
                    hour = datetime.utcnow().hour
                    day = datetime.utcnow().strftime("%A")
                    ua = request.headers.get("User-Agent", "")
                    selected = get_signal_based_suggestions(hour=hour, day_of_week=day, user_agent=ua, per_slot=per_slot)
                    if mobile:
                        selected = selected[:6]
                    options = [s.to_task_option() for s in selected]
                    import threading
                    threading.Thread(
                        target=refresh_pool_background,
                        args=(db, user_id, "first_conversation"),
                        daemon=True,
                    ).start()
                options.append(_TaskOption(
                    title="More", subtitle="Load new ideas",
                    action="fetchMoreOptions()", icon="more",
                ))
            except Exception as e:
                logger.warning(f"Smart suggestions failed, using fallback: {e}")
                user_preferences = fetch_user_preferences(user_id)
                options = task_generator.get_fallback_options(num_tasks=num_suggestions, include_more_option=True, user_preferences=user_preferences)
            guest_count = 0

            # Check if session has messages to prevent FOUC
            try:
                session_doc = db.collection('users').document(user_id).collection('chat_sessions').document(session_id).get()
                if session_doc.exists:
                    session_data = session_doc.to_dict()
                    msg_count = len(session_data.get('messages', []))
                    session_has_messages = msg_count > 0
                    logger.info(f"FOUC check: session {session_id} has {msg_count} messages, session_has_messages={session_has_messages}")
            except Exception as e:
                logger.error(f"FOUC check failed: {e}")
                pass  # If check fails, default to showing home elements

        return render_template(
            'index.html',
            options=options,
            shared_session_id=session_id,
            session_has_messages=session_has_messages,
            is_guest=guest_mode,
            guest_message_count=guest_count,
            guest_message_limit=GUEST_MESSAGE_LIMIT,
            guest_banner_threshold=GUEST_BANNER_THRESHOLD
        )
    except Exception as e:
        logger.error(f"Failed to load shared session {session_id}: {e}")
        return jsonify({'error': 'Failed to load the shared session.'}), 500


@pages_bp.route('/more-options', methods=['POST'])
@csrf_required
@error_handler
def more_options():
    """Get more task options — serve from precomputed pool for logged-in users."""
    try:
        if 'user' in session:
            user_id = session['user']['uid']
            try:
                from app.services.smart_suggestions import (
                    load_pool, select_display_slots, increment_shown_counts,
                    get_pool_metadata, should_refresh_pool, refresh_pool_background,
                )
                pool = load_pool(db, user_id)
                if pool:
                    import random
                    random.shuffle(pool)
                    selected = select_display_slots(pool)
                    options_dicts = [s.to_task_option().to_dict() for s in selected]
                    options_dicts.append({
                        "title": "More", "subtitle": "Load new ideas",
                        "action": "fetchMoreOptions()", "icon": "more",
                    })
                    increment_shown_counts(db, user_id, [s.title for s in selected])
                    metadata = get_pool_metadata(db, user_id)
                    if should_refresh_pool(metadata):
                        import threading
                        threading.Thread(
                            target=refresh_pool_background,
                            args=(db, user_id, "exhausted"),
                            daemon=True,
                        ).start()
                    return jsonify({"options": options_dicts}), 200
            except Exception as e:
                logger.warning(f"Smart suggestions more-options failed: {e}")

        # Fallback: original behavior
        current_options = request.json.get('current_options', [])
        user_preferences = {}
        user_history = None
        if 'user' in session:
            user_id = session['user']['uid']
            user_preferences = fetch_user_preferences(user_id)
        num_tasks = 3
        options = task_generator.generate_task_options(
            user_history=user_history, num_tasks=num_tasks, force_new_tasks=True,
        )
        new_options = [opt for opt in options if opt not in current_options]
        options_dicts = [opt.to_dict() for opt in new_options]
        return jsonify({"options": options_dicts}), 200
    except Exception as e:
        logger.error(f"Failed to fetch more options: {e}")
        return jsonify({'error': 'Failed to fetch more options.'}), 500


@pages_bp.route('/suggestion-click', methods=['POST'])
@csrf_required
@error_handler
def suggestion_click():
    """Record a suggestion click for the learning loop."""
    if 'user' not in session:
        return jsonify({"ok": True}), 200  # silently ignore for guests

    try:
        from app.services.smart_suggestions import record_click
        user_id = session['user']['uid']
        click_data = request.json or {}
        record_click(db, user_id, click_data)
        return jsonify({"ok": True}), 200
    except Exception as e:
        logger.debug(f"Click tracking failed: {e}")
        return jsonify({"ok": True}), 200  # never fail the user's click


@pages_bp.route('/favicon.ico')
def favicon():
    """Serve favicon asset for browsers."""
    icon_dir = os.path.join(current_app.root_path, 'static', 'images')
    return send_from_directory(icon_dir, 'logo.png', mimetype='image/png')


@pages_bp.route('/debug', methods=['POST'])
@csrf_required
@error_handler
@login_required
def debug_code():
    """Debug code endpoint - analyzes and fixes code using AI."""
    try:
        # Import send_prompt here to avoid circular imports
        from app.utilities import send_prompt

        logger.info("Received a request to /debug")
        code = request.json.get('code', '').strip()

        if not code:
            logger.error('No code provided in the request.')
            return jsonify({'error': 'No code provided.'}), 400

        prompt = (
            "Please review and debug the following code. Provide any comments, explanations, or debugging steps "
            "before the corrected code. Then return the corrected code only, strictly enclosed between "
            "`<code>` and `</code>` tags, without any additional text inside the tags:\n\n" + code
        )

        logger.info("Sending request to AI with prompt.")
        response, is_code_response = send_prompt(prompt, model="llama-3.3-70b-versatile")

        if isinstance(response, dict) and 'error' in response:
            logger.error(f"Error from AI model: {response['error']}")
            return jsonify({'error': response['error']}), 500

        if not response:
            logger.error('AI model returned an empty response.')
            return jsonify({'error': 'AI model returned an empty response.'}), 500

        logger.info("Debugged code received from AI.")
        return jsonify({'debugged_code': response}), 200

    except Exception as e:
        logger.error(f'Error during code debugging: {e}')
        logger.debug(traceback.format_exc())
        return jsonify({'error': 'Failed to debug code.'}), 500
