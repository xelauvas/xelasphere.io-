"""Chat management routes blueprint.

Routes:
- POST /chat/new - Create new chat session
- POST /chat/save/<session_id> - Save chat session
- POST /chat/archive - Archive chat session
- POST /chat/pin - Pin/unpin chat session
- POST /chat - Main chat endpoint
- POST /chat/stream - Streaming chat endpoint
"""

import base64
import json
import logging
import mimetypes
import os
import re
import time
import traceback
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Blueprint, Response, abort, current_app, g, jsonify, request, send_from_directory, session, url_for
from google.cloud import firestore as gc_firestore
from werkzeug.exceptions import RequestEntityTooLarge

from app.config import Config, db
from app.limiter import limiter, csrf_required
from app.blueprints.shared import (
    login_required, guest_allowed, error_handler, is_guest, chat_session,
    get_client_ip, hash_ip, make_etag_response,
    ModelProviderError, TokenLimitError,
    artifact_router, artifact_store, executor,
    GUEST_MESSAGE_LIMIT, GUEST_BANNER_THRESHOLD,
    get_guest_message_count, get_guest_ip_usage,
    increment_guest_ip_usage, check_guest_ip_limit,
    task_generator,
)
from app.services.chat_engine import (
    SYSTEM_INSTRUCTIONS, DEVELOPER_INSTRUCTIONS,
    MAX_TOKENS, RESPONSE_TOKEN_BUFFER,
    FRIENDLY_AI_ERROR_MESSAGES, PROVIDER_SPECIFIC_MESSAGES,
    classify_user_intent, detect_crypto_price_query,
    get_embedding_model, build_prompt_fragment,
    load_or_create_session, load_user_and_session_preferences,
    _ensure_offline_session_state, _cache_offline_interaction,
    _clear_offline_cache, _update_session_summary,
    build_prompt, get_ai_response, _friendly_ai_error_message,
    save_interaction, _select_relevant_messages,
    _assemble_intent_text, update_session_title_if_missing,
    _fallback_title_from_prompt, _clean_user_prompt_for_title,
    _is_meaningful_prompt,
    build_artifact_reference_payload, build_draft_artifact,
)
from app.artifacts import (
    ArtifactPreference, parse_structured_response,
    detect_artifact_update_intent, load_current_artifact_context,
    delete_local_attachments, ARTIFACT_UPDATE_PATTERNS,
)
from app.file_processor import (
    get_file_type, generate_unique_filename, extract_text_concurrently,
    _safe_upload_relative_path, _build_metadata_lookup,
    _resolve_user_attachment_source, build_user_attachment_records,
    _get_file_chunk_context, _detect_file_intent,
)
from app.user_preferences import (
    fetch_user_preferences, update_user_preferences,
    _adjust_user_session_total, _increment_user_credits,
    _calculate_credit_cost, CREDITS_MIN_INCREMENT,
)
from app.image_generator import generate_image_result
from app.utilities import (
    categorize_sessions, count_tokens, send_prompt, send_prompt_with_images,
    tokenize_sentences, is_image_request, generate_fallback_response,
    strip_html_from_response,
)
from app.search_client import (
    SearchResult, SearchBackend, SearchClient, SearchException,
    resolve_search_backend,
    perform_web_search, should_auto_enable_search, should_auto_enable_search_simple,
)
from app.blueprints.email_routes import is_email_request, looks_like_email, parse_email_from_response
from app.model_catalog import MODEL_LABELS, MODEL_PROVIDERS, DEFAULT_MODEL, AUTO_MODEL, resolve_model_id, get_selectable_models
from app.smart_router import smart_route, is_refusal_response, get_uncensored_model, check_prompt_injection
from app.encryption import encrypt_api_key, decrypt_api_key, is_encrypted
from app.system_rules import get_system_rules, should_skip_memory, should_skip_reflection, should_skip_memory_write, get_message_mode
from app.list_registry import process_ai_response_for_lists, resolve_user_pointer
from app.smart_guard import smart_check_input, smart_tag_response, smart_validate_response
from app.memory_v2 import MemoryOrchestrator, SessionContext, ConversationState, MemoryFeatureFlags
from app.memory_v2.dream import maybe_consolidate as dream_maybe_consolidate
from app.memory_v2.dream.gates import get_dream_state, update_dream_state
from app.memory_v3 import (
    get_v3_context, V3_ENABLED, reset_v3_state,
    should_inject_v3, get_v3_cohort, get_rollout_manager,
    generate_v3_signal, record_v3_metrics,
)
from app.crypto_api import (
    get_crypto_ai_context, format_crypto_context_for_ai,
    get_stock_ai_context, format_stock_context_for_ai,
    get_coin_id as get_coin_id_dynamic,
)
try:
    from app.services.url_reader import get_url_reader
    URL_READER_AVAILABLE = True
except ImportError:
    get_url_reader = None
    URL_READER_AVAILABLE = False
from app.asset_config import CRYPTO_SYMBOLS, STOCK_SYMBOLS, COMPANY_TO_SYMBOL, ASSET_NAMES, get_coin_id, get_display_name

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


def handle_image_request(user_id: str, user_prompt: str, page_id: Optional[str] = None):
    """Handle image generation requests."""
    logger.info(f"Detected image request from user {user_id}: {user_prompt}")
    try:
        result = generate_image_result(user_id, user_prompt)
        if not result:
            raise RuntimeError("Image generation failed")
        # Ensure a session exists and persist message with attachment
        session_id, _ = load_or_create_session(user_id)
        session['current_session_id'] = session_id
        local_path = result.get('local_path')
        public_url = result['image_url']
        data_url = public_url if isinstance(public_url, str) and public_url.startswith('data:') else None
        response_id = f"msg_{uuid.uuid4().hex}"
        filename = os.path.basename(local_path) if local_path else None
        prompt_fragment = build_prompt_fragment(user_prompt)
        model_name = result.get('model')
        model_label = result.get('model_label') or model_name
        attachments = [{
            'name': 'AI generated image',
            'original_filename': filename,
            'isImage': True,
            'url': public_url,
            'dataUrl': data_url,
            'seed': result['seed'],
            'provider': result.get('provider'),
            'model': model_name,
            'model_label': model_label,
            'local_path': local_path,
            'prompt_fragment': prompt_fragment
        }]
        storage_attachments = []
        for att in attachments:
            cleaned = att.copy()
            cleaned.pop('dataUrl', None)
            local_rel = cleaned.get('local_path')
            if local_rel:
                cleaned['url'] = url_for('upload.serve_uploaded_file', requested_path=local_rel, _external=False)
            else:
                url_value = cleaned.get('url')
                if isinstance(url_value, str) and url_value.startswith('data:'):
                    cleaned.pop('url', None)
            cleaned = {k: v for k, v in cleaned.items() if v is not None}
            storage_attachments.append(cleaned)
        chat_data = {
            'prompt': user_prompt,
            'response': '',
            'timestamp': datetime.utcnow().isoformat(),
            'sentiment': 'neutral',
            'is_code': False,
            'search_results': [],
            'attachments': storage_attachments,
            'response_id': response_id,
            'suppress_text': True,
            'sender': 'AI',
            'message_type': 'image'
        }
        # This session was just set as current, so it's always the active session
        chat_session.save_messages(user_id, session_id, [chat_data], is_active_session=True)

        session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
        session_doc = session_ref.get()
        session_data = session_doc.to_dict() if session_doc.exists else {}
        if page_id:
            try:
                session_ref.set({'active_page_id': page_id}, merge=True)
            except Exception as exc:
                logger.warning(f"Failed to set active page for image request: {exc}")
        try:
            session_title = update_session_title_if_missing(
                user_id=user_id,
                session_id=session_id,
                session_data=session_data or {},
                response_text='',
                user_prompt=user_prompt
            )
        except Exception as title_error:
            logger.warning(f"Failed to auto-title image session {session_id} for user {user_id}: {title_error}")
            session_title = (session_data or {}).get('session_name', 'New Chat')

        # Use storage_attachments for the response so frontend has the served URL
        # (original attachments may have data: URLs that don't persist)
        response_attachments = []
        for att in attachments:
            resp_att = att.copy()
            resp_att.pop('dataUrl', None)
            local_rel = resp_att.get('local_path')
            if local_rel:
                resp_att['url'] = url_for('upload.serve_uploaded_file', requested_path=local_rel, _external=False)
            response_attachments.append(resp_att)

        return jsonify({
            'message': 'Image URL generated successfully.',
            'image_url': result['image_url'],
            'local_path': result.get('local_path'),
            'seed': result['seed'],
            'prompt': user_prompt,
            'provider': result.get('provider'),
            'model': model_name,
            'model_label': model_label,
            'attachments': response_attachments,
            'session_id': session_id,
            'session_name': session_title,
            'message_id': response_id,
            'suppress_text': True
        }), 200
    except Exception as e:
        logger.error(f"Error generating image for user {user_id}: {e}")
        return jsonify({'error': 'Failed to generate image URL.'}), 500


@chat_bp.route('/chat/stream', methods=['POST'])
@limiter.limit("20 per minute")
@csrf_required
@error_handler
@guest_allowed
def chat_stream():
    """
    Stream chat response with real-time reasoning content (DeepSeek R1).
    Returns Server-Sent Events (SSE) with reasoning and content chunks.
    At completion, processes content for artifacts and saves to history.
    """
    from .utilities import stream_deepseek_response

    user_id = None
    guest_mode = 'guest_id' in session and 'user' not in session

    if not guest_mode:
        user_data = session.get('user')
        if not user_data or 'uid' not in user_data:
            return jsonify({'error': 'Unauthorized'}), 401
        user_id = user_data['uid']
    else:
        user_id = session.get('guest', {}).get('id')

    prompt = request.form.get('prompt', '').strip()
    user_prompt_raw = prompt  # Keep raw user text for intent detection (before file content injection)
    model = request.form.get('model', 'deepseek-reasoner')
    page_id = request.form.get('page_id', '').strip() or None

    # Include extracted text from uploaded files
    extracted_text = ''
    attachments_json = request.form.get('attachments', '')
    if attachments_json:
        try:
            att_list = json.loads(attachments_json)
            for att in att_list:
                # Try extractedText sent from frontend first
                et = att.get('extractedText')
                if et:
                    extracted_text += et + '\n'
                    continue
                # Fallback: read file from disk using previewUrl
                preview_url = att.get('previewUrl', '')
                if preview_url and not att.get('isImage'):
                    # previewUrl is like /uploads/user_id/filename.txt
                    file_rel = preview_url.lstrip('/')
                    # Try relative to app root first, then Config.UPLOAD_FOLDER
                    app_root = os.path.dirname(os.path.dirname(__file__))
                    file_path = os.path.join(app_root, file_rel)
                    if not os.path.isfile(file_path):
                        # previewUrl may be /uploads/X, UPLOAD_FOLDER is 'uploads'
                        stripped = preview_url.replace('/uploads/', '', 1)
                        file_path = os.path.join(Config.UPLOAD_FOLDER, stripped)
                    if os.path.isfile(file_path):
                        try:
                            with open(file_path, 'r', errors='replace') as f:
                                et = f.read()
                            if et:
                                extracted_text += et + '\n'
                                logger.info(f"[Stream] Read {len(et)} chars from disk: {file_path}")
                        except Exception as e:
                            logger.warning(f"[Stream] Failed to read file {file_path}: {e}")
        except (json.JSONDecodeError, TypeError):
            pass
    # Fallback to session
    if not extracted_text:
        extracted_text = session.pop('extracted_text', '')
    if extracted_text:
        max_extract = min(len(extracted_text), 30000)
        file_context = f"Uploaded file content:\n{extracted_text[:max_extract]}"
        if len(extracted_text) > max_extract:
            file_context += f"\n[...truncated, showing first {max_extract} of {len(extracted_text)} chars]"
        prompt = f"{file_context}\n\nUser request: {prompt}"

    if not prompt:
        return jsonify({'error': 'No prompt provided'}), 400

    # Get or create session for saving history and context detection
    session_id = None
    previous_messages = []
    if not guest_mode and user_id:
        try:
            session_id, session_data = load_or_create_session(user_id)
            # Load previous messages for follow-up question context detection
            previous_messages = session_data.get('messages', [])
            if isinstance(previous_messages, list) and previous_messages:
                # Sort by timestamp
                try:
                    previous_messages = sorted(
                        previous_messages,
                        key=lambda m: m.get('timestamp', ''),
                    )
                except Exception:
                    pass
                # Cap messages in memory — only recent ones are needed for context
                if len(previous_messages) > 20:
                    logger.debug(f"[Stream] Trimming {len(previous_messages)} messages to last 20")
                    previous_messages = previous_messages[-20:]
                logger.debug(f"[Stream] Loaded {len(previous_messages)} previous messages for context")
        except Exception as e:
            logger.warning(f"Failed to load session for streaming: {e}")

    # ============================================================
    # Memory V2: Get memory context via orchestrator (streaming)
    # ============================================================
    memory_orchestrator_stream = None
    memory_eval_metadata_stream = None
    long_term_memory_stream = ""

    if not guest_mode and user_id:
        try:
            memory_session_ctx = SessionContext(
                user_id=user_id,
                device_id=session.get('_device_id'),
                is_new_device=session.get('_is_new_device', False)
            )
            conv_state = ConversationState(
                pending_confirmation_ids=session.get('_memory_pending_ids', []),
                last_retrieved_ids=session.get('_memory_last_ids', []),
                messages_in_session=len(previous_messages)
            )
            memory_orchestrator_stream = MemoryOrchestrator(user_id, memory_session_ctx)
            memory_context = memory_orchestrator_stream.get_context(prompt, conv_state)

            if not memory_context.memory_disabled:
                long_term_memory_stream = memory_context.context_block
                memory_eval_metadata_stream = memory_context.eval_metadata
                logger.info(f"🧠 [Stream] Memory V2: Retrieved {len(memory_context.eval_metadata.retrieved_ids)} memories")
        except Exception as e:
            logger.warning(f"[Stream] Memory V2 retrieval failed: {e}")
    # ============================================================

    # ============================================================
    # Memory V3: Add behavior signals (ephemeral, never stored)
    # ============================================================
    v3_injected = False
    v3_signal = None
    if V3_ENABLED and long_term_memory_stream:
        try:
            # Generate signal for metrics (always, even in shadow mode)
            v3_signal = generate_v3_signal(
                prompt,
                previous_messages[-5:] if previous_messages else None
            )

            # Check if this user/session should get V3 injection
            v3_should_inject = should_inject_v3(
                user_id=user_id,
                session_id=session_id
            )

            if v3_should_inject:
                long_term_memory_stream = get_v3_context(
                    user_message=prompt,
                    conversation_history=previous_messages[-5:] if previous_messages else None,
                    v2_context=long_term_memory_stream
                )
                v3_injected = True
                v3_cohort = "treatment"
                logger.info(f"🧠 [Stream] Memory V3: Behavior signals INJECTED (cohort={v3_cohort})")
            else:
                v3_cohort = "control"
                logger.info(f"🧠 [Stream] Memory V3: Shadow mode (cohort={v3_cohort})")

            # Record metrics for rollout analysis
            record_v3_metrics(v3_signal, injected=v3_injected)

            # Track conversation turn for KPIs
            rollout_mgr = get_rollout_manager()
            rollout_mgr.record_turn(
                prompt,
                previous_messages[-3:] if previous_messages else None
            )

        except Exception as e:
            logger.warning(f"[Stream] Memory V3 failed (using V2 only): {e}")
    # ============================================================

    # Inject memory context into streaming prompt (was missing — memories retrieved but never used)
    enhanced_prompt = prompt
    if long_term_memory_stream:
        enhanced_prompt = f"User memory (from previous sessions):\n{long_term_memory_stream}\n\n{enhanced_prompt}"
        logger.info(f"🧠 [Stream] Injected {len(long_term_memory_stream)} chars of memory context into prompt")

    # Inject recent chat history for conversation continuity
    if previous_messages:
        recent = previous_messages[-6:]  # Last 3 exchanges
        history_lines = []
        for msg in recent:
            role = "User" if msg.get('prompt') else "Assistant"
            if msg.get('prompt'):
                history_lines.append(f"User: {msg['prompt'][:300]}")
            if msg.get('response'):
                history_lines.append(f"Assistant: {msg['response'][:300]}")
        if history_lines:
            history_block = "\n".join(history_lines)
            enhanced_prompt = f"Recent conversation:\n{history_block}\n\n{enhanced_prompt}"
            logger.info(f"🔍 [Stream] Injected {len(recent)} recent messages into prompt")

    # Claude-inspired: Inject current artifact context when user asks to fix/update
    if session_id and user_id and detect_artifact_update_intent(prompt):
        logger.info(f"🔧 [Stream] Artifact update intent detected: '{prompt[:50]}...'")
        artifact_context = load_current_artifact_context(user_id, session_id)
        if artifact_context:
            logger.info(f"📦 [Stream] Injected {len(artifact_context)} chars of artifact context")
            # Prepend artifact context to prompt so AI sees current code
            enhanced_prompt = f"{artifact_context}\n\nUser request: {prompt}\n\nIMPORTANT: Apply the fix/update to the code shown above and output a complete ```artifact block with ALL files."
        else:
            logger.debug("[Stream] No active artifact found to inject")

    # Inject crypto/stock context ONLY when user explicitly asks for price/chart
    # Use raw user prompt (not file-enriched prompt) to avoid false positives from file content
    chart_query = detect_crypto_price_query(user_prompt_raw)
    if chart_query:
        asset_type = chart_query.get('asset_type')
        if asset_type == 'crypto':
            coin = chart_query.get('coin')
            if coin:
                crypto_context = get_crypto_ai_context(coin)
                if crypto_context:
                    context_str = format_crypto_context_for_ai(crypto_context)
                    logger.info(f"💰 [Stream] Injected crypto context for chart query: {coin}")
                    enhanced_prompt = f"{context_str}\n\nUser question: {enhanced_prompt}"
        elif asset_type == 'stock':
            symbol = chart_query.get('symbol')
            if symbol:
                stock_context = get_stock_ai_context(symbol)
                if stock_context:
                    context_str = format_stock_context_for_ai(stock_context)
                    logger.info(f"📈 [Stream] Injected stock context for chart query: {symbol}")
                    enhanced_prompt = f"{context_str}\n\nUser question: {enhanced_prompt}"

    def generate():
        import json
        reasoning_buffer = []
        content_buffer = []

        try:
            for chunk in stream_deepseek_response(enhanced_prompt, model):
                chunk_type = chunk.get('type')

                if chunk_type == 'reasoning':
                    text = chunk.get('text', '')
                    reasoning_buffer.append(text)
                    yield f"data: {json.dumps({'type': 'reasoning', 'text': text})}\n\n"

                elif chunk_type == 'content':
                    text = chunk.get('text', '')
                    content_buffer.append(text)
                    yield f"data: {json.dumps({'type': 'content', 'text': text})}\n\n"

                elif chunk_type == 'done':
                    full_reasoning = ''.join(reasoning_buffer)
                    full_content = ''.join(content_buffer)
                    # Strip HTML tags that some models may output
                    full_content = strip_html_from_response(full_content)

                    # Process content for artifacts
                    artifact_data = None
                    render_mode = 'inline'
                    chat_summary = full_content
                    routing = None

                    try:
                        structured = parse_structured_response(full_content)
                        routing = artifact_router.route(structured, prompt, provider_flagged_code=False)
                        logger.info(f"Stream routing decision: render_mode={routing.render_mode}, has_payload={routing.artifact_payload is not None}")
                        logger.info(f"Stream chat_payload: {repr(routing.chat_payload[:200] if routing.chat_payload else None)}")
                        logger.info(f"Stream structured.chat: {repr(structured.chat[:200] if structured.chat else None)}")
                        logger.info(f"Stream structured.remainder: {repr(structured.remainder[:200] if structured.remainder else None)}")

                        if routing.render_mode and routing.render_mode != 'inline':
                            render_mode = routing.render_mode
                            chat_summary = routing.chat_payload or structured.remainder or ''
                            logger.info(f"Stream chat_summary set to: {repr(chat_summary[:200])}")

                            if routing.artifact_payload:
                                files = routing.artifact_payload.get('files', [])
                                primary_lang = files[0].get('language', 'code') if files else 'code'
                                artifact_id = f'artifact-{int(time.time())}'
                                version_id = f'version-{int(time.time())}'

                                # Structure expected by artifactPanel.js
                                artifact_data = {
                                    'artifactId': routing.artifact_payload.get('artifact_id', artifact_id),
                                    'title': routing.artifact_payload.get('title', 'Artifact'),
                                    'type': routing.artifact_payload.get('type', 'project'),
                                    'primaryLanguage': primary_lang,
                                    'activeVersion': {
                                        'versionId': version_id,
                                        'versionNumber': 1,
                                        'files': files,
                                        'primaryLanguage': primary_lang,
                                        'entrypointPath': files[0].get('path') if files else None
                                    },
                                    'versions': [{
                                        'versionId': version_id,
                                        'versionNumber': 1
                                    }]
                                }
                                logger.info(f"Stream artifact extracted: {len(files)} files")
                    except Exception as artifact_err:
                        logger.warning(f"Artifact processing error in stream: {artifact_err}")

                    # Generate response ID before done payload so frontend can use same ID
                    response_msg_id = f"msg_{uuid.uuid4().hex}"

                    # Check if response looks like an email
                    is_email = looks_like_email(full_content, user_asked_for_email=is_email_request(user_prompt_raw))
                    email_data = None
                    if is_email:
                        email_data = parse_email_from_response(full_content)
                        logger.info(f"Stream: Email detected, parsed: subject='{email_data.get('subject', '')[:30]}...'")

                    # Check if user asked for crypto/stock price/chart
                    crypto_chart = detect_crypto_price_query(user_prompt_raw)
                    if crypto_chart:
                        asset_type = crypto_chart.get('asset_type', 'crypto')
                        asset = crypto_chart.get('symbol') if asset_type == 'stock' else crypto_chart.get('coin')
                        logger.info(f"Stream: {asset_type.upper()} chart detected for {asset}")

                    done_payload = {
                        'type': 'done',
                        'reasoning': full_reasoning,
                        'content': full_content,
                        'chat_summary': chat_summary,
                        'render_mode': render_mode,
                        'artifact': artifact_data,
                        'response_id': response_msg_id,
                        'is_email': is_email,
                        'email': email_data,
                        'crypto_chart': crypto_chart
                    }
                    logger.info(f"Stream done payload: render_mode={render_mode}, has_artifact={artifact_data is not None}, crypto_chart={crypto_chart is not None}")

                    # Save to history and persist artifact
                    if session_id and user_id and not guest_mode:
                        try:
                            # Update session title if missing
                            try:
                                session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
                                session_doc = session_ref.get()
                                session_data = session_doc.to_dict() if session_doc.exists else {}
                                update_session_title_if_missing(user_id, session_id, session_data, chat_summary, prompt)
                            except Exception as title_err:
                                logger.warning(f"Failed to update session title in streaming: {title_err}")

                            artifact_ref = None

                            # Persist artifact to Firestore if detected
                            if artifact_data and routing and routing.artifact_payload:
                                try:
                                    persisted = artifact_store.persist_decision(
                                        user_id=user_id,
                                        session_id=session_id,
                                        decision=routing,
                                        source_message_id=response_msg_id,
                                        created_by="assistant"
                                    )
                                    if persisted:
                                        logger.info(f"✅ Stream artifact persisted: {persisted.get('artifactId')}")
                                        # Update artifact_data with persisted IDs
                                        artifact_data['artifactId'] = persisted.get('artifactId', artifact_data.get('artifactId'))
                                        if persisted.get('activeVersion'):
                                            artifact_data['activeVersion']['versionId'] = persisted['activeVersion'].get('versionId')

                                        artifact_ref = {
                                            'artifactId': persisted.get('artifactId'),
                                            'title': persisted.get('title'),
                                            'type': persisted.get('type'),
                                            'primaryLanguage': persisted.get('primaryLanguage'),
                                            'summary': chat_summary[:200] if chat_summary else None,
                                            'versionId': persisted.get('activeVersion', {}).get('versionId'),
                                            'versionNumber': persisted.get('versionCount', 1)
                                        }
                                    else:
                                        # persist_decision returned None - use artifact_data as fallback
                                        logger.warning(f"⚠️ persist_decision returned None, using artifact_data fallback")
                                        artifact_ref = {
                                            'artifactId': artifact_data.get('artifactId'),
                                            'title': artifact_data.get('title'),
                                            'type': artifact_data.get('type'),
                                            'primaryLanguage': artifact_data.get('primaryLanguage'),
                                            'summary': chat_summary[:200] if chat_summary else None,
                                            'versionId': artifact_data.get('activeVersion', {}).get('versionId'),
                                            'versionNumber': 1,
                                            'pending': True
                                        }
                                except Exception as artifact_err:
                                    logger.warning(f"Failed to persist stream artifact: {artifact_err}")
                                    artifact_ref = {
                                        'artifactId': artifact_data.get('artifactId'),
                                        'title': artifact_data.get('title'),
                                        'type': artifact_data.get('type'),
                                        'primaryLanguage': artifact_data.get('primaryLanguage'),
                                        'summary': chat_summary[:200] if chat_summary else None,
                                        'pending': True
                                    }

                            logger.info(f"📦 Saving stream interaction: render_mode={render_mode}, artifact_ref={artifact_ref is not None}, artifact_id={artifact_ref.get('artifactId') if artifact_ref else None}, pending={artifact_ref.get('pending') if artifact_ref else 'N/A'}")
                            save_interaction(
                                user_id=user_id,
                                session_id=session_id,
                                user_prompt=prompt,
                                response_text=chat_summary,
                                sentiment='neutral',
                                is_code_response=(render_mode in ['artifact', 'code_window']),
                                search_results=[],
                                persist=True,
                                response_id=response_msg_id,
                                render_mode=render_mode,
                                artifact_reference=artifact_ref,
                                reasoning=full_reasoning if full_reasoning else None,
                                crypto_chart=crypto_chart
                            )
                            logger.info(f"Streaming response saved to history for session {session_id}")

                            # ============================================================
                            # Memory V2: Post-turn processing (streaming)
                            # ============================================================
                            stream_memory_updated = False
                            if memory_orchestrator_stream is not None:
                                try:
                                    # Use conv_state captured before generator (avoids request context issue)
                                    conv_state_for_write = ConversationState(
                                        pending_confirmation_ids=conv_state.pending_confirmation_ids if conv_state else [],
                                        last_retrieved_ids=conv_state.last_retrieved_ids if conv_state else [],
                                        messages_in_session=len(previous_messages)
                                    )
                                    memory_write_result = memory_orchestrator_stream.post_turn(
                                        prompt,
                                        full_content,
                                        conv_state_for_write,
                                        memory_eval_metadata_stream
                                    )
                                    # Note: session write skipped here (no request context in generator)
                                    # Pending IDs will be re-read from Firestore on next request
                                    # Check if anything was actually stored
                                    stream_memory_updated = (
                                        memory_write_result.stored > 0 or
                                        memory_write_result.promoted > 0 or
                                        memory_write_result.superseded > 0
                                    )
                                    logger.info(f"🧠 [Stream] Memory V2: stored={memory_write_result.stored}, promoted={memory_write_result.promoted}")
                                except Exception as mem_err:
                                    logger.warning(f"[Stream] Memory V2 post_turn failed: {mem_err}")

                            # Add memory_updated to done_payload
                            done_payload['memory_updated'] = stream_memory_updated
                            # ============================================================

                        except Exception as save_err:
                            logger.warning(f"Failed to save streaming response to history: {save_err}")

                    yield f"data: {json.dumps(done_payload)}\n\n"

                elif chunk_type == 'error':
                    yield f"data: {json.dumps({'type': 'error', 'text': chunk.get('text', 'Unknown error')})}\n\n"

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@chat_bp.route('/chat', methods=['POST'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@guest_allowed
def chat():
    """
    Handle chat interactions with the AI model:
    - Extract user input and optional extracted text from uploaded files.
    - Optionally perform a web search and integrate the results as context.
    - Retrieve conversation history and preferences to provide context.
    - Generate an AI response and return it along with metadata.
    """
    try:
        # Guest mode handling
        guest_mode = is_guest()
        if guest_mode:
            # IP-based tracking (prevents cookie clearing bypass)
            client_ip = get_client_ip()
            ip_hash = hash_ip(client_ip)

            # Check IP-based limit first (stored in Firebase)
            ip_allowed, ip_count = check_guest_ip_limit(ip_hash)
            if not ip_allowed:
                logger.info(f"Guest IP limit reached for hash: {ip_hash[:8]}...")
                return jsonify({
                    'error': 'guest_limit_reached',
                    'message': 'Create a free account to continue chatting and unlock all features.',
                    'limit': GUEST_MESSAGE_LIMIT
                }), 403

            # Also check session-based limit as backup
            session_count = get_guest_message_count()
            current_count = max(ip_count, session_count)

            if current_count >= GUEST_MESSAGE_LIMIT:
                return jsonify({
                    'error': 'guest_limit_reached',
                    'message': 'Create a free account to continue chatting and unlock all features.',
                    'limit': GUEST_MESSAGE_LIMIT
                }), 403

            user_id = session['guest']['id']
            # Store IP hash in session for later increment
            session['_guest_ip_hash'] = ip_hash
        else:
            user_id = session['user']['uid']

        user_prompt = request.form.get('prompt', '').strip()

        # For guests: force default model and disable certain features
        if guest_mode:
            model = DEFAULT_MODEL
            session['selected_model'] = model
            session['selected_model_label'] = MODEL_LABELS.get(model, model)
            smart_routing_enabled = False
        else:
            requested_model = request.form.get('model', AUTO_MODEL)
            # Check if smart routing should be used
            smart_routing_enabled = (requested_model == AUTO_MODEL or requested_model == 'auto' or not requested_model)
            if smart_routing_enabled:
                # Will be routed by smart_route() after we have context
                model = DEFAULT_MODEL  # Placeholder, will be updated
            else:
                model = resolve_model_id(requested_model)
            session['selected_model'] = model
            session['selected_model_label'] = MODEL_LABELS.get(model, model)

        extracted_text = ''
        raw_attachments = request.form.get('attachments')
        attachments = []
        if raw_attachments:
            try:
                parsed = json.loads(raw_attachments)
                if isinstance(parsed, list):
                    attachments = parsed
            except Exception as e:
                logger.warning(f"Failed to parse attachments payload: {e}")

        # Extract text from attachments (sent from frontend or read from disk)
        for att in attachments:
            et = att.get('extractedText')
            if et:
                extracted_text += et + '\n'
                continue
            # Fallback: read file from disk using previewUrl
            preview_url = att.get('previewUrl', '')
            if preview_url and not att.get('isImage'):
                file_rel = preview_url.lstrip('/')
                app_root = os.path.dirname(os.path.dirname(__file__))
                file_path = os.path.join(app_root, file_rel)
                if not os.path.isfile(file_path):
                    stripped = preview_url.replace('/uploads/', '', 1)
                    file_path = os.path.join(Config.UPLOAD_FOLDER, stripped)
                if os.path.isfile(file_path):
                    try:
                        with open(file_path, 'r', errors='replace') as f:
                            et = f.read()
                        if et:
                            extracted_text += et + '\n'
                    except Exception as e:
                        logger.warning(f"Failed to read file {file_path}: {e}")
        # Fallback to session
        if not extracted_text:
            extracted_text = session.pop('extracted_text', '')
        requested_page_id_raw = request.form.get('page_id')
        requested_page_id = requested_page_id_raw.strip() if requested_page_id_raw else None
        # Guests don't get web search
        if guest_mode:
            enable_search = False
            search_confidence = 'low'
        else:
            enable_search_param = request.form.get('enable_search')
            if enable_search_param is not None:
                # User explicitly set the toggle
                enable_search = enable_search_param.lower() == 'true'
                search_confidence = 'user'
            else:
                # Auto-detect if search is needed
                enable_search, search_confidence = should_auto_enable_search(user_prompt)

        backend_param = request.form.get('backend') or getattr(Config, 'SEARCH_DEFAULT_BACKEND', SearchBackend.BRAVE.value)
        backend = resolve_search_backend(backend_param)

        # Validate input
        if not user_prompt and not extracted_text:
            logger.warning("No user prompt or extracted text provided.")
            return jsonify({'error': 'No prompt provided.'}), 400

        # Validate prompt length to prevent resource exhaustion
        MAX_PROMPT_LENGTH = 50000  # ~12,500 tokens at 4 chars/token
        MAX_EXTRACTED_TEXT_LENGTH = 100000  # Allow more for documents
        if len(user_prompt) > MAX_PROMPT_LENGTH:
            logger.warning(f"Prompt too long: {len(user_prompt)} chars from user {user_id}")
            return jsonify({'error': f'Prompt exceeds maximum length of {MAX_PROMPT_LENGTH} characters.'}), 400
        if len(extracted_text) > MAX_EXTRACTED_TEXT_LENGTH:
            logger.warning(f"Truncating extracted text from {len(extracted_text)} to {MAX_EXTRACTED_TEXT_LENGTH} chars")
            extracted_text = extracted_text[:MAX_EXTRACTED_TEXT_LENGTH]

        # Prompt injection guard
        injection_check = check_prompt_injection(user_prompt)
        if not injection_check.is_safe:
            logger.warning(f"🛡️ Blocked prompt injection from user {user_id}: {injection_check.matched_patterns}")
            return jsonify({
                'error': 'security_blocked',
                'message': injection_check.message
            }), 400

        firestore_available = True
        user_profile: Dict[str, Any] = {}
        user_api_keys = {}

        # Skip Firestore user profile for guests
        if not guest_mode:
            # Track user preferences from their message for personalized suggestions
            update_user_preferences(user_id, user_prompt)

            try:
                user_ref = db.collection('users').document(user_id)
                user_snapshot = user_ref.get()
                if user_snapshot.exists:
                    user_profile = user_snapshot.to_dict() or {}
            except Exception as e:
                firestore_available = False
                logger.warning(f"Failed to load user profile for {user_id}: {e}")
                user_profile = {}

            if isinstance(user_profile, dict):
                encrypted_keys = user_profile.get('api_keys') or {}
                # Decrypt user API keys if they are encrypted
                for key_name, key_value in encrypted_keys.items():
                    if key_value and is_encrypted(key_value):
                        try:
                            user_api_keys[key_name] = decrypt_api_key(key_value)
                        except Exception as e:
                            logger.warning(f"Failed to decrypt {key_name} API key for user {user_id}: {e}")
                    else:
                        user_api_keys[key_name] = key_value

        g.api_keys = user_api_keys if user_api_keys else {}

        # NOTE: Image request check moved to after session/messages are loaded
        # so we can pass chat history for better detection accuracy

        # Perform optional web search
        search_results: List[SearchResult] = []
        if enable_search and user_prompt:
            search_results = perform_web_search(user_id, user_prompt, backend=backend)

        # Fetch content from URLs in the message (for summarization/analysis)
        url_content = ""
        url_fetch_results = []
        if URL_READER_AVAILABLE and user_prompt and not guest_mode:
            try:
                url_reader = get_url_reader(db)
                detected_urls = url_reader.detect_urls(user_prompt)
                if detected_urls:
                    logger.info(f"Detected {len(detected_urls)} URL(s) in message: {detected_urls}")
                    for url in detected_urls:
                        result = url_reader.fetch_url(url)
                        url_fetch_results.append(result)
                        if result.get('success'):
                            url_content += (
                                f"\n\n---\n"
                                f"[Content from: {result.get('title', url)}]\n"
                                f"Source: {url}\n"
                                f"Type: {result.get('type', 'article')}\n\n"
                                f"{result.get('content', '')}\n"
                                f"---\n"
                            )
                            logger.info(f"Fetched {len(result.get('content', ''))} chars from {url}")
                        else:
                            logger.warning(f"Failed to fetch URL {url}: {result.get('error')}")
            except Exception as e:
                logger.error(f"URL reader error: {e}")

        offline_reason = None

        # Guests use ephemeral in-memory session only
        if guest_mode:
            session_id = session['guest']['id']
            # Store guest messages in Flask session (ephemeral)
            if 'guest_messages' not in session:
                session['guest_messages'] = []
            session_data = {
                'messages': session['guest_messages'],
                'session_name': 'Guest Chat',
                'preferences': []
            }
        elif firestore_available:
            try:
                session_id, session_data = load_or_create_session(user_id)
            except Exception as e:
                firestore_available = False
                if not offline_reason:
                    offline_reason = 'session_unavailable'
                logger.warning(f"Falling back to offline session cache for user {user_id}: {e}")
                session_id, session_data = _ensure_offline_session_state()
        else:
            offline_reason = 'session_unavailable'
            session_id, session_data = _ensure_offline_session_state()
        # Note: We no longer override session_data['messages'] with page-specific messages.
        # The main messages array is the authoritative source for conversation history on the backend.
        # The pages feature is managed by the frontend for UI branching purposes.
        # Backend save_interaction() saves to session.messages, so we should read from there too.
        resolved_page_id = requested_page_id or (session_data.get('active_page_id') if isinstance(session_data, dict) else None)
        if resolved_page_id:
            logger.debug(f"📥 PAGE DEBUG: active_page_id={resolved_page_id} (using main messages array for history, not page-specific)")

        # Load user and session preferences (skip for guests)
        if guest_mode:
            user_preferences = []
            session_preferences = []
        elif firestore_available:
            try:
                user_preferences, session_preferences, user_profile = load_user_and_session_preferences(
                    user_id,
                    session_data,
                    user_profile
                )
            except Exception as e:
                firestore_available = False
                if not offline_reason:
                    offline_reason = 'preferences_unavailable'
                logger.warning(f"Failed to load preferences from Firestore for user {user_id}: {e}")
                user_preferences = []
                session_preferences = session_data.get('preferences', [])
        else:
            user_preferences = []
            session_preferences = session_data.get('preferences', [])
        # Use messages ordered by timestamp
        previous_messages = session_data.get('messages', [])
        if isinstance(previous_messages, list):
            try:
                previous_messages = sorted(
                    previous_messages,
                    key=lambda m: m.get('timestamp', ''),
                )
            except Exception:
                pass
            # Cap messages in memory — only recent ones are needed for context
            if len(previous_messages) > 20:
                logger.debug(f"Trimming {len(previous_messages)} messages to last 20")
                previous_messages = previous_messages[-20:]

        # Check if user requests an image (now with chat history for better accuracy)
        if is_image_request(user_prompt, previous_messages):
            return handle_image_request(user_id, user_prompt, requested_page_id)

        # Resolve pointer references (e.g., "tell me about #10" → explicit item)
        pointer_info = None
        original_prompt = user_prompt
        try:
            user_prompt, was_rewritten, pointer_info = resolve_user_pointer(
                user_id, session_id, user_prompt
            )
            if was_rewritten:
                logger.info(f"Pointer resolved: '{original_prompt[:50]}' → '{user_prompt[:80]}'")
            elif pointer_info and pointer_info.get("needs_clarification"):
                # Pointer detected but no list to resolve - will need clarification
                logger.debug(f"Pointer detected but needs clarification: {pointer_info}")
        except Exception as e:
            logger.debug(f"Pointer resolution failed: {e}")

        # Check if there's a recent crypto/stock chart in conversation
        # If so, skip ambiguity check - "tell me more" clearly refers to the chart
        has_recent_chart = False
        recent_chart_info = None
        logger.info(f"📊 CHART CHECK: Checking {len(previous_messages)} messages for crypto_chart")
        for i, msg in enumerate(reversed(previous_messages[-4:])):
            has_chart = msg.get('crypto_chart')
            logger.info(f"📊 CHART CHECK: msg[{i}] has crypto_chart: {has_chart}, keys: {list(msg.keys())[:10]}")
            if has_chart:
                has_recent_chart = True
                recent_chart_info = msg.get('crypto_chart')
                logger.info(f"📊 Skipping SmartGuard - recent chart context: {recent_chart_info.get('asset_type', 'crypto')} - {recent_chart_info.get('coin') or recent_chart_info.get('symbol')}")
                break
        if not has_recent_chart:
            logger.debug(f"📊 CHART CHECK: No recent chart found in messages")

        # Check for ambiguous input BEFORE calling LLM (LLM-powered smart check)
        # Skip if there's a recent chart context (follow-up questions have clear context)
        try:
            intercept_result = None
            if not has_recent_chart:
                intercept_result = smart_check_input(
                    user_id, session_id, user_prompt, previous_messages
                )
            if intercept_result and intercept_result.should_intercept:
                logger.info(f"Intercepting ambiguous input: {intercept_result.reason}")
                # Return clarification response directly without calling LLM
                clarification_response = intercept_result.clarification_prompt
                response_message_id = f"msg_{uuid.uuid4().hex}"
                chat_data, persisted = save_interaction(
                    user_id=user_id,
                    session_id=session_id,
                    user_prompt=user_prompt,
                    response_text=clarification_response,
                    sentiment='neutral',
                    is_code_response=False,
                    search_results=[],
                    user_attachments=[],
                    persist=firestore_available,
                    response_id=response_message_id,
                    render_mode='inline',
                    artifact_reference=None
                )
                return jsonify({
                    'response': clarification_response,
                    'sentiment': 'neutral',
                    'is_code_response': False,
                    'session_id': session_id,
                    'message_id': response_message_id,
                    'title': session_data.get('session_name') or "Chat",
                    'intercept_reason': intercept_result.reason,
                })
        except Exception as e:
            logger.debug(f"Ambiguity check failed: {e}")

        # Determine stateless vs stateful mode
        is_stateless = should_skip_memory(user_prompt)
        message_mode = get_message_mode(user_prompt)
        logger.debug(f"Message mode: {message_mode} for prompt: {user_prompt[:50]}...")

        # ============================================================
        # Memory V2: Get memory context via orchestrator
        # ============================================================
        logger.info(f"🧠 Memory V2: Starting retrieval for user {user_id}")
        memory_eval_metadata = None
        memory_orchestrator = None  # Kept for post_turn() call later
        try:
            # Build session context for Memory V2
            memory_session_ctx = SessionContext(
                user_id=user_id,
                device_id=session.get('_device_id'),
                is_new_device=session.get('_is_new_device', False)
            )

            # Get conversation state from session (for pending confirmations)
            conv_state = ConversationState(
                pending_confirmation_ids=session.get('_memory_pending_ids', []),
                last_retrieved_ids=session.get('_memory_last_ids', []),
                messages_in_session=len(previous_messages)
            )

            # Create orchestrator and get context
            memory_orchestrator = MemoryOrchestrator(user_id, memory_session_ctx)
            memory_context = memory_orchestrator.get_context(user_prompt, conv_state)

            # Use context_block as long_term_memory
            if memory_context.memory_disabled:
                long_term_memory = ""
                logger.debug(f"Memory disabled: {memory_context.disable_reason}")
            else:
                long_term_memory = memory_context.context_block
                memory_eval_metadata = memory_context.eval_metadata
                logger.info(f"🧠 Memory V2: Retrieved {len(memory_context.eval_metadata.retrieved_ids)} memories")

                # Check for action required (low-confidence confirmation)
                if memory_context.action_required:
                    logger.debug(f"Memory action required: {memory_context.action_required.action_type}")

        except Exception as e:
            logger.error(f"🧠 Memory V2 retrieval FAILED: {e}", exc_info=True)
            long_term_memory = ""
        # ============================================================

        # ============================================================
        # Memory V3: Add behavior signals (ephemeral, never stored)
        # ============================================================
        v3_injected = False
        v3_signal = None
        if V3_ENABLED and long_term_memory:
            try:
                # Generate signal for metrics (always, even in shadow mode)
                v3_signal = generate_v3_signal(
                    user_prompt,
                    previous_messages[-5:] if previous_messages else None
                )

                # Check if this user/session should get V3 injection
                v3_should_inject = should_inject_v3(
                    user_id=user_id,
                    session_id=session_id
                )

                if v3_should_inject:
                    long_term_memory = get_v3_context(
                        user_message=user_prompt,
                        conversation_history=previous_messages[-5:] if previous_messages else None,
                        v2_context=long_term_memory
                    )
                    v3_injected = True
                    v3_cohort = "treatment"
                    logger.info(f"🧠 Memory V3: Behavior signals INJECTED (cohort={v3_cohort})")
                else:
                    v3_cohort = "control"
                    logger.info(f"🧠 Memory V3: Shadow mode (cohort={v3_cohort})")

                # Record metrics for rollout analysis
                record_v3_metrics(v3_signal, injected=v3_injected)

                # Track conversation turn for KPIs
                rollout_mgr = get_rollout_manager()
                rollout_mgr.record_turn(
                    user_prompt,
                    previous_messages[-3:] if previous_messages else None
                )

            except Exception as e:
                logger.warning(f"Memory V3 failed (using V2 only): {e}")
        # ============================================================

        # Build attachment context (names/types) to keep the model aware of uploaded files
        attachment_lines = []
        if attachments:
            for att in attachments:
                name = att.get('name') or att.get('original_filename') or 'file'
                att_type = att.get('type') or ''
                attachment_lines.append(f"- {name}" + (f" ({att_type})" if att_type else ""))
        else:
            meta_list = session.get('file_metadata_list') or []
            for meta in meta_list:
                name = meta.get('original_filename') or meta.get('filename')
                if name:
                    att_type = meta.get('mime_type') or ''
                    attachment_lines.append(f"- {name}" + (f" ({att_type})" if att_type else ""))

        user_attachment_records = build_user_attachment_records(user_prompt, attachments)

        attachment_context = ""
        if attachment_lines:
            attachment_context = "Files attached or recently uploaded:\n" + "\n".join(attachment_lines)
            extracted_text = f"{attachment_context}\n\n{extracted_text}".strip()

        # RAG: Retrieve relevant file chunks if files exist in conversation
        has_files = bool(attachment_lines) or session_data.get('has_files', False)
        # Always try RAG retrieval when files exist (not just on explicit file mentions)
        # This allows follow-up questions like "summarize this" to work
        if has_files or _detect_file_intent(user_prompt, has_files=has_files):
            file_chunk_context = _get_file_chunk_context(
                user_id=user_id,
                query=user_prompt,
                conversation_id=session_id,
                top_k=8  # Increase for better context on follow-ups
            )
            if file_chunk_context:
                # Prepend file chunks to extracted text (higher priority than full text)
                extracted_text = f"{file_chunk_context}\n\n{extracted_text}".strip()
                logger.info(f"Added {len(file_chunk_context)} chars of file chunk context")

        # Add URL-fetched content to extracted_text
        if url_content:
            extracted_text = f"{extracted_text}\n\n{url_content}".strip()
            logger.info(f"Added {len(url_content)} chars of URL content")

        response_message_id = f"msg_{uuid.uuid4().hex}"

        # Build the prompt (skip context for stateless questions)
        try:
            # Check if there's existing conversation history
            # If we have history, treat as stateful even if message pattern looks "stateless"
            # because short follow-ups like "yes", "maintenance" ARE continuations
            has_conversation_history = len(previous_messages) > 1

            # For stateless questions WITHOUT history, skip session summary, relevant messages, pending steps
            if is_stateless and not has_conversation_history:
                session_summary_text = ""
                relevant_msgs = []
                pending_steps_list = None
                prev_msgs_for_prompt = previous_messages[-1:] if previous_messages else []
                logger.debug(f"Stateless mode (no history): limiting to 1 message")
            else:
                # Has conversation history OR is explicitly stateful - use full context
                session_summary_text = session_data.get('session_summary', '')
                relevant_msgs = _select_relevant_messages(user_prompt, previous_messages)
                pending_steps_list = session_data.get('pending_steps')
                prev_msgs_for_prompt = previous_messages
                if has_conversation_history and is_stateless:
                    logger.debug(f"Overriding stateless - has {len(previous_messages)} messages of history")

            # Increase token budget when there's file content to analyze
            prompt_max_tokens = 16000 if extracted_text else MAX_TOKENS
            is_recipe_request = False  # Default, updated below if recipe intent detected

            # Claude-inspired: Inject current artifact context when user asks to fix/update
            artifact_context = None
            if detect_artifact_update_intent(user_prompt):
                logger.info(f"🔧 Artifact update intent detected: '{user_prompt[:50]}...'")
                artifact_context = load_current_artifact_context(user_id, session_id)
                if artifact_context:
                    logger.info(f"📦 Injected {len(artifact_context)} chars of artifact context")
                    # Increase token budget for artifact context
                    prompt_max_tokens = max(prompt_max_tokens, 24000)
                else:
                    logger.debug("No active artifact found to inject")

            # Intent-based format enforcement for recipes
            # Classify intent and inject focused format reminder when recipe is detected
            user_intent = classify_user_intent(user_prompt)
            is_recipe_request = (
                user_intent.get('intent_type') == 'recipe_request' and
                user_intent.get('confidence', 0) > 0.5
            )

            # Preserve the user-facing prompt before any system injections
            display_prompt = user_prompt

            if is_recipe_request:
                recipe_format_reminder = (
                    "[SYSTEM: Recipe response format required]\n"
                    "When providing a recipe, you MUST:\n"
                    "1. Start with ## followed by the ACTUAL recipe name (e.g., '## Chicken Stir Fry')\n"
                    "2. Add meta line: ⏱ 20 min | 🍽 4 servings | 🔥 Easy\n"
                    "3. Add '### Ingredients' header\n"
                    "4. List EVERY ingredient with '- ' bullet and quantity (e.g., '- 1 lb chicken breast')\n"
                    "5. Add '### Instructions' header\n"
                    "6. List numbered steps\n\n"
                    "CRITICAL: Your ### Ingredients section must list ALL ingredients with measurements.\n"
                    "Every item used in cooking (proteins, vegetables, oils, sauces, spices) needs its own '- ' line.\n"
                    "Do NOT skip ingredients. Do NOT put ingredients inside instruction steps only.\n\n"
                )
                user_prompt = recipe_format_reminder + "User request: " + user_prompt
                logger.info(f"🍳 Injected recipe format reminder for: {user_prompt[:80]}...")

            full_prompt = build_prompt(
                user_prompt=user_prompt,
                extracted_text=extracted_text,
                search_results=search_results,
                previous_messages=prev_msgs_for_prompt,
                user_prefs=user_preferences,
                session_prefs=session_preferences,
                session_summary=session_summary_text,
                relevant_messages=relevant_msgs,
                system_instructions=SYSTEM_INSTRUCTIONS,
                developer_instructions=DEVELOPER_INSTRUCTIONS,
                long_term_memory=long_term_memory,
                pending_steps=pending_steps_list,
                canonical_context=artifact_context,  # Claude-style artifact injection
                encoding_name="cl100k_base",
                max_tokens=prompt_max_tokens,
                response_token_buffer=512
            )
        except TokenLimitError:
            logger.warning("Input exceeds maximum token limit.")
            return jsonify({'error': 'Input exceeds maximum token limit.'}), 400

        # Inject crypto/stock context ONLY when user explicitly asks for price/chart
        chart_query = detect_crypto_price_query(user_prompt)
        if chart_query:
            asset_type = chart_query.get('asset_type')
            if asset_type == 'crypto':
                coin = chart_query.get('coin')
                if coin:
                    crypto_context = get_crypto_ai_context(coin)
                    if crypto_context:
                        context_str = format_crypto_context_for_ai(crypto_context)
                        logger.info(f"💰 Injected crypto context for chart query: {coin}")
                        full_prompt = f"{context_str}\n\n{full_prompt}"
            elif asset_type == 'stock':
                symbol = chart_query.get('symbol')
                if symbol:
                    stock_context = get_stock_ai_context(symbol)
                    if stock_context:
                        context_str = format_stock_context_for_ai(stock_context)
                        logger.info(f"📈 Injected stock context for chart query: {symbol}")
                        full_prompt = f"{context_str}\n\n{full_prompt}"

        # Extract image URLs from attachments for vision API
        # Include both current attachments AND recent images from conversation history
        image_attachments = []
        seen_image_paths = set()  # Avoid duplicates

        # Enrich attachments with local_path from file_metadata_list (uploaded files)
        metadata_lookup = _build_metadata_lookup()
        for att in attachments:
            if att.get('isImage') and not att.get('local_path'):
                resolved = _resolve_user_attachment_source(att, metadata_lookup)
                if resolved and resolved.get('local_path'):
                    att['local_path'] = resolved['local_path']

        # Handle image files sent as multipart form fields (for vision API)
        image_files = request.files.getlist('image_files')
        for img_file in image_files:
            if img_file and img_file.content_type and img_file.content_type.startswith('image/'):
                try:
                    img_data = base64.b64encode(img_file.read()).decode('utf-8')
                    data_url = f"data:{img_file.content_type};base64,{img_data}"
                    # Find matching attachment and set dataUrl, or add new one
                    matched = False
                    for att in attachments:
                        if att.get('isImage') and att.get('name') == img_file.filename and not att.get('dataUrl'):
                            att['dataUrl'] = data_url
                            matched = True
                            break
                    if not matched:
                        attachments.append({
                            'name': img_file.filename,
                            'type': img_file.content_type,
                            'isImage': True,
                            'dataUrl': data_url,
                        })
                except Exception as e:
                    logger.warning(f"Failed to read image file {img_file.filename}: {e}")


        def extract_image_url(att: Dict[str, Any]) -> Optional[str]:
            """Extract and convert image attachment to data URL."""
            if not att.get('isImage'):
                return None

            # Try dataUrl first (base64), then previewUrl, then url, then local_path
            img_url = att.get('dataUrl') or att.get('previewUrl') or att.get('url') or att.get('local_path')
            if not img_url:
                return None

            # If it's a local path, convert to base64 data URL
            if img_url.startswith('/uploads/') or (not img_url.startswith('data:') and not img_url.startswith('http')):
                try:
                    # Normalize path
                    rel_path = img_url.replace('/uploads/', '') if img_url.startswith('/uploads/') else img_url
                    local_path = os.path.join(Config.UPLOAD_FOLDER, rel_path)

                    # Skip if already processed
                    if local_path in seen_image_paths:
                        return None
                    seen_image_paths.add(local_path)

                    if os.path.exists(local_path):
                        mime_type, _ = mimetypes.guess_type(local_path)
                        if mime_type and mime_type.startswith('image/'):
                            with open(local_path, 'rb') as f:
                                img_data = base64.b64encode(f.read()).decode('utf-8')
                                return f"data:{mime_type};base64,{img_data}"
                except Exception as e:
                    logger.warning(f"Failed to load image for vision: {e}")
                return None
            elif img_url.startswith('data:image/'):
                return img_url
            elif img_url.startswith('http'):
                return img_url
            return None

        # 1. Current message attachments (highest priority)
        for att in attachments:
            img_url = extract_image_url(att)
            if img_url:
                image_attachments.append({'url': img_url})
                logger.info(f"Added current attachment image for vision")

        # 2. Recent conversation history images (last 5 messages with images, max 3 images total)
        MAX_HISTORY_IMAGES = 3
        if len(image_attachments) < MAX_HISTORY_IMAGES:
            for msg in reversed(previous_messages[-10:]):  # Check last 10 messages
                if len(image_attachments) >= MAX_HISTORY_IMAGES:
                    break

                # Check user_attachments field
                for att in msg.get('user_attachments', []):
                    if len(image_attachments) >= MAX_HISTORY_IMAGES:
                        break
                    img_url = extract_image_url(att)
                    if img_url:
                        image_attachments.append({'url': img_url})
                        logger.info(f"Added history image for vision context")

                # Also check attachments field
                for att in msg.get('attachments', []):
                    if len(image_attachments) >= MAX_HISTORY_IMAGES:
                        break
                    img_url = extract_image_url(att)
                    if img_url:
                        image_attachments.append({'url': img_url})
                        logger.info(f"Added history image for vision context")

        # Smart routing: determine optimal model based on task
        selected_task_type = None
        selected_model_id = None

        if smart_routing_enabled:
            has_images = bool(image_attachments)
            conversation_length = len(previous_messages) if previous_messages else 0

            routed_model, task_type = smart_route(
                message=user_prompt,
                user_selected_model=None,  # Auto mode
                has_images=has_images,
                conversation_length=conversation_length,
                previous_response=None,
                conversation_history=previous_messages,  # Pass history for context detection
            )
            model = routed_model
            selected_task_type = task_type
            selected_model_id = model
            logger.info(f"🔀 Smart routing: {task_type} -> {model}")
            session['selected_model'] = model
            session['selected_model_label'] = MODEL_LABELS.get(model, model)

        # Get AI response (with images if present)
        raw_model_response, provider_flagged_code, reasoning_content = get_ai_response(
            full_prompt, model, images=image_attachments if image_attachments else None
        )

        # Check for refusal and retry with uncensored model if needed
        refusal_detected = is_refusal_response(raw_model_response)
        logger.info(f"🔍 Refusal check: smart_routing={smart_routing_enabled}, refusal_detected={refusal_detected}")
        if smart_routing_enabled and refusal_detected:
            uncensored_model = get_uncensored_model()
            logger.info(f"🔄 Detected refusal, retrying with {uncensored_model}")
            raw_model_response, provider_flagged_code, reasoning_content = get_ai_response(
                full_prompt, uncensored_model, images=image_attachments if image_attachments else None
            )
            model = uncensored_model

        # Strip HTML tags that some models (like Llama) may output despite instructions
        raw_model_response = strip_html_from_response(raw_model_response)

        if not raw_model_response.strip():
            logger.error(f"AI model returned an empty response for user {user_id}.")
            return jsonify({'error': 'Empty response from AI model.'}), 500

        # Validate response for duplicates and contradictions (embedding-based)
        response_validation = None
        try:
            response_validation = smart_validate_response(
                user_id, session_id, raw_model_response, previous_messages
            )
            if not response_validation.is_valid:
                logger.warning(f"Response validation failed: {response_validation.issues}")
                if response_validation.is_duplicate:
                    # Response is too similar to previous - ask LLM to expand differently
                    logger.info(f"Duplicate detected ({response_validation.similarity_score:.0%} similar)")
                    # Append instruction to avoid repetition
                    expansion_prompt = (
                        f"{full_prompt}\n\n"
                        "[SYSTEM: Your previous response was too similar. "
                        "Please provide NEW information, different examples, or expand on a different aspect. "
                        "Do NOT repeat what you've already said.]"
                    )
                    raw_model_response, provider_flagged_code, _ = get_ai_response(expansion_prompt, model)
                    raw_model_response = strip_html_from_response(raw_model_response)
                    if not raw_model_response.strip():
                        raw_model_response = "I've already covered this topic. Would you like me to explore a different aspect or go deeper on a specific point?"
                if response_validation.contradictions:
                    for c in response_validation.contradictions:
                        logger.warning(f"Contradiction detected: {c['entity']} was '{c['old_value']}', now '{c['new_value']}'")
        except Exception as e:
            logger.debug(f"Response validation failed: {e}")

        structured_response = parse_structured_response(raw_model_response)
        routing_decision = artifact_router.route(
            structured_response,
            user_prompt,
            provider_flagged_code=provider_flagged_code
        )
        # Determine response text based on render mode
        chat_payload = routing_decision.chat_payload or ""
        remainder = structured_response.remainder or ""

        # For code_window mode: verify code actually exists
        has_code_anywhere = "```" in chat_payload or "```" in remainder or "```" in raw_model_response

        # Fallback: if AI said code_window but no code exists, switch to inline
        effective_render_mode = routing_decision.render_mode
        effective_contains_code = routing_decision.contains_code

        if routing_decision.render_mode == "code_window" and not has_code_anywhere:
            logger.warning("🔧 AI specified code_window but no code fences found - falling back to inline")
            effective_render_mode = "inline"
            effective_contains_code = False
        elif routing_decision.render_mode == "artifact" and not routing_decision.artifact_payload:
            logger.warning("🔧 AI specified artifact mode but no artifact payload - falling back to code_window")
            effective_render_mode = "code_window" if has_code_anywhere else "inline"
            effective_contains_code = has_code_anywhere
        elif routing_decision.render_mode == "code_window" and "```" not in chat_payload and "```" in remainder:
            # Use remainder if chat_payload doesn't have code fences but remainder does
            response_text = remainder.strip() or raw_model_response
            logger.info("🔧 Using remainder for code_window (chat_payload had no code fences)")

        if routing_decision.render_mode == "code_window" and has_code_anywhere:
            response_text = (remainder if "```" in remainder else chat_payload if "```" in chat_payload else raw_model_response).strip() or raw_model_response
        else:
            response_text = (chat_payload or remainder or raw_model_response).strip() or raw_model_response

        full_response_text = remainder or raw_model_response
        is_code_response = effective_contains_code

        # Extract and store any numbered lists from response (for pointer resolution)
        try:
            stored_list = process_ai_response_for_lists(
                user_id, session_id, response_text, response_message_id
            )
            if stored_list:
                logger.debug(f"Stored list with {len(stored_list.items)} items for pointer resolution")
        except Exception as e:
            logger.debug(f"List extraction failed: {e}")

        # Validate recipe response format
        if is_recipe_request and response_text:
            has_ingredients_section = '### Ingredients' in response_text or '**Ingredients' in response_text
            has_ingredient_bullets = bool(re.search(r'### Ingredients.*?^-\s+\S', response_text, re.MULTILINE | re.DOTALL))

            if has_ingredients_section and not has_ingredient_bullets:
                logger.warning(f"🍳 Recipe response has Ingredients header but NO bullet items!")
                # Log the ingredients section for debugging
                ing_match = re.search(r'(### Ingredients.*?)(?=###|$)', response_text, re.DOTALL)
                if ing_match:
                    logger.warning(f"🍳 Ingredients section content: {ing_match.group(1)[:200]}...")
            elif not has_ingredients_section:
                logger.warning(f"🍳 Recipe response missing ### Ingredients section entirely!")
            else:
                logger.info(f"🍳 Recipe format validation passed ✓")

        # Debug logging for artifact decision
        logger.info(f"🔍 ARTIFACT DEBUG: AI render_mode={routing_decision.render_mode}, effective={effective_render_mode}")
        logger.info(f"🔍 ARTIFACT DEBUG: AI contains_code={routing_decision.contains_code}, effective={effective_contains_code}")
        logger.info(f"🔍 ARTIFACT DEBUG: has_code_anywhere={has_code_anywhere}")
        logger.info(f"🔍 ARTIFACT DEBUG: meta={structured_response.meta}")
        logger.info(f"🔍 ARTIFACT DEBUG: response_text_preview={response_text[:200] if response_text else 'EMPTY'}...")

        artifact_payload = None
        artifact_reference = None
        artifact_status = None
        if routing_decision.render_mode != "inline" and routing_decision.artifact_payload:
            draft_payload = routing_decision.artifact_payload.copy()
            persisted_artifact = None
            logger.info(f"🎨 Artifact detected! Mode: {routing_decision.render_mode}, Action: {routing_decision.desired_action}")
            if firestore_available:
                try:
                    logger.info(f"🔄 Calling persist_decision for session {session_id}")
                    persisted_artifact = artifact_store.persist_decision(
                        user_id=user_id,
                        session_id=session_id,
                        decision=routing_decision,
                        source_message_id=response_message_id,
                        created_by="assistant"
                    )
                    if persisted_artifact:
                        logger.info(f"✅ Artifact persisted: {persisted_artifact.get('artifactId')} v{persisted_artifact.get('versionCount', 1)}")
                    else:
                        logger.warning("⚠️ persist_decision returned None")
                except Exception as exc:
                    logger.error(f"❌ Failed to persist artifact: {exc}", exc_info=True)
                    logger.warning("Failed to persist artifact for user %s session %s: %s", user_id, session_id, exc)
                    artifact_status = 'persist_failed'
                    if not offline_reason:
                        offline_reason = 'artifact_persist_failed'
            else:
                artifact_status = 'pending_persist'
                if not offline_reason:
                    offline_reason = 'artifact_persist_deferred'

            if persisted_artifact:
                artifact_payload = persisted_artifact
            else:
                artifact_payload = build_draft_artifact(draft_payload, response_message_id)

            is_pending = artifact_status in {'persist_failed', 'pending_persist', 'artifact_persist_failed', 'artifact_persist_deferred'}
            artifact_reference = build_artifact_reference_payload(artifact_payload, response_text, is_pending)
            artifact_payload['pending'] = is_pending

        sentiment = 'neutral'

        # Check if user asked for crypto/stock price/chart (needed for save_interaction)
        crypto_chart = detect_crypto_price_query(user_prompt)
        if crypto_chart:
            asset_type = crypto_chart.get('asset_type', 'crypto')
            asset = crypto_chart.get('symbol') if asset_type == 'stock' else crypto_chart.get('coin')
            logger.info(f"{asset_type.upper()} chart detected for {asset}")

        # Guest mode: store in session, skip all Firestore operations
        if guest_mode:
            title = "Guest Chat"
            # Store message in Flask session
            guest_message = {
                'prompt': user_prompt,
                'response': response_text,
                'timestamp': datetime.utcnow().isoformat()
            }
            session['guest_messages'].append(guest_message)
            # Increment guest message count (both session and Firebase)
            session['guest']['message_count'] = session['guest'].get('message_count', 0) + 1
            session.modified = True

            # Increment Firebase IP-based counter (prevents cookie clearing bypass)
            ip_hash = session.get('_guest_ip_hash')
            if ip_hash:
                increment_guest_ip_usage(ip_hash)
            chat_data = guest_message
            persisted = False
        else:
            # Update session title if missing
            if firestore_available:
                try:
                    title = update_session_title_if_missing(user_id, session_id, session_data, response_text, user_prompt)
                except Exception as e:
                    firestore_available = False
                    if not offline_reason:
                        offline_reason = 'title_update_failed'
                    logger.warning(f"Failed to update session title for user {user_id}: {e}")
                    title = session_data.get('session_name') or "Offline Session"
            else:
                title = session_data.get('session_name') or "Offline Session"

            # Save this interaction (offline cache if persistence fails)
            chat_data, persisted = save_interaction(
                user_id=user_id,
                session_id=session_id,
                user_prompt=user_prompt,
                response_text=response_text,
                sentiment=sentiment,
                is_code_response=is_code_response,
                search_results=search_results,
                user_attachments=user_attachment_records,
                persist=firestore_available,
                response_id=response_message_id,
                render_mode=effective_render_mode,
                artifact_reference=artifact_reference,
                reasoning=reasoning_content,
                crypto_chart=crypto_chart,
                display_prompt=display_prompt,
                model_id=selected_model_id or model,
                task_type=selected_task_type,
                auto_selected=bool(smart_routing_enabled) or (session.get('selected_model') == AUTO_MODEL),
            )
            if artifact_payload and not persisted:
                chat_data['artifact_draft'] = artifact_payload

            if persisted and firestore_available:
                _clear_offline_cache()
            else:
                firestore_available = False
                if not offline_reason:
                    offline_reason = 'persistence_failed'
                _cache_offline_interaction(chat_data, title)

            # Tag response for SmartGuard continuation detection
            # This enables "other one", "again", "next" to work naturally
            try:
                smart_tag_response(user_id, session_id, response_text, user_prompt)
            except Exception as e:
                logger.debug(f"Failed to tag response for SmartGuard: {e}")

        # Skip all post-processing for guests
        if not guest_mode:
            if resolved_page_id and firestore_available:
                try:
                    db.collection('users').document(user_id).collection('chat_sessions').document(session_id).set(
                        {'active_page_id': resolved_page_id},
                        merge=True
                    )
                except Exception as exc:
                    logger.warning(f"Failed to update active page for session {session_id}: {exc}")

            # Update rolling session summary when Firestore is reachable (non-critical)
            if firestore_available:
                try:
                    _update_session_summary(user_id, session_id, session_data, user_prompt, response_text)
                except Exception as e:
                    logger.warning(f"Failed to update session summary for user {user_id}: {e}")

            # Track credit usage only when persistence succeeded
            credit_cost = _calculate_credit_cost(user_prompt, full_response_text)
            if firestore_available:
                try:
                    _increment_user_credits(user_id, credit_cost)
                except Exception as e:
                    logger.warning(f"Failed to increment credits for user {user_id}: {e}")

        # Process reflection + memory in parallel (skip for stateless questions and guests)
        memory_updated = False
        next_steps = []

        # Skip reflection and memory writing for stateless questions
        skip_reflection = should_skip_reflection(user_prompt)
        skip_memory_write = should_skip_memory_write(user_prompt, response_text)
        logger.info(f"🧠 Memory V2: skip_reflection={skip_reflection}, skip_memory_write={skip_memory_write}, guest={guest_mode}, firestore={firestore_available}")

        if not guest_mode and firestore_available and not (skip_reflection and skip_memory_write):
            try:
                from .reflection import reflect_on_response, get_memory_candidates, get_next_steps_for_session
            except ImportError:
                reflect_on_response = None
                get_memory_candidates = None
                get_next_steps_for_session = None
                logger.warning("Reflection module not available, skipping reflection")
                skip_reflection = True

            try:
                # Run reflection and memory V2 post_turn in parallel (conditionally)
                reflection_future = None
                memory_future = None

                if not skip_reflection:
                    reflection_future = executor.submit(
                        reflect_on_response,
                        user_prompt,
                        response_text
                    )

                # ============================================================
                # Memory V2: Post-turn processing (extraction + storage)
                # ============================================================
                logger.info(f"🧠 Memory V2: Checking post_turn - skip_memory_write={skip_memory_write}, orchestrator={memory_orchestrator is not None}")
                if not skip_memory_write and memory_orchestrator is not None:
                    logger.info(f"🧠 Memory V2: Running post_turn extraction...")
                    # Build conversation state for post_turn
                    conv_state_for_write = ConversationState(
                        pending_confirmation_ids=session.get('_memory_pending_ids', []),
                        last_retrieved_ids=session.get('_memory_last_ids', []),
                        messages_in_session=len(previous_messages)
                    )
                    memory_future = executor.submit(
                        memory_orchestrator.post_turn,
                        user_prompt,
                        response_text,
                        conv_state_for_write,
                        memory_eval_metadata
                    )
                elif skip_memory_write:
                    logger.debug("Skipping memory write - stateless question")
                # ============================================================

                # Gather results (with timeouts)
                reflection_result = None
                if reflection_future:
                    try:
                        reflection_result = reflection_future.result(timeout=4)
                    except Exception:
                        pass

                memory_write_result = None
                if memory_future:
                    try:
                        memory_write_result = memory_future.result(timeout=4)
                        # Memory V2 returns MemoryWriteResult - check if anything was actually stored
                        memory_updated = (
                            memory_write_result.stored > 0 or
                            memory_write_result.promoted > 0 or
                            memory_write_result.superseded > 0
                        )
                        # Update session state for next turn
                        if memory_write_result.new_pending_confirmation_ids:
                            session['_memory_pending_ids'] = memory_write_result.new_pending_confirmation_ids
                        logger.info(f"🧠 Memory V2: stored={memory_write_result.stored}, promoted={memory_write_result.promoted}, superseded={memory_write_result.superseded}")
                    except Exception as e:
                        logger.error(f"🧠 Memory V2 post_turn FAILED: {e}", exc_info=True)

                # Dream: background memory consolidation (fire-and-forget)
                if not guest_mode:
                    try:
                        user_prefs_for_dream = session.get('user', {}).get('preferences', {})
                        import threading
                        dream_thread = threading.Thread(
                            target=dream_maybe_consolidate,
                            args=(db, user_id, user_prefs_for_dream),
                            daemon=True,
                        )
                        dream_thread.start()
                    except Exception as e:
                        logger.debug(f"Dream trigger skipped: {e}")

                # Smart suggestions: trigger refresh after enough new conversations
                if not guest_mode:
                    try:
                        from app.services.smart_suggestions import (
                            get_pool_metadata, should_refresh_pool, refresh_pool_background,
                        )
                        metadata = get_pool_metadata(db, user_id)
                        convos_since = 0
                        last_refresh = metadata.get("last_refresh")
                        if last_refresh:
                            sessions_ref = db.collection("users").document(user_id).collection("chat_sessions")
                            from google.cloud.firestore_v1 import FieldFilter
                            try:
                                recent = sessions_ref.where(filter=FieldFilter("updated_at", ">", last_refresh)).count().get()
                                convos_since = recent[0][0].value if recent else 0
                            except Exception:
                                convos_since = 0

                        if should_refresh_pool(metadata, conversations_since=convos_since):
                            import threading
                            threading.Thread(
                                target=refresh_pool_background,
                                args=(db, user_id, "conversations"),
                                daemon=True,
                            ).start()
                    except Exception as e:
                        logger.debug(f"Smart suggestions refresh check skipped: {e}")

                # Process reflection results
                if reflection_result and reflection_result.has_actionable_items():
                    # NOTE: Reflection memory candidates are now handled by Memory V2's
                    # extraction in post_turn(). The old MemoryGate path is deprecated.
                    # If reflection finds unique memories not caught by extraction,
                    # consider routing them through orchestrator.add_external_candidates().

                    # Store next steps in session if any
                    next_steps = get_next_steps_for_session(reflection_result) or []
                    if next_steps and session_id:
                        try:
                            db.collection('users').document(user_id).collection('chat_sessions').document(session_id).update({
                                'pending_steps': next_steps
                            })
                        except Exception:
                            pass

            except Exception as e:
                logger.debug(f"Reflection/memory processing error: {e}")

        # Prepare and return response
        cached_unsynced = 0
        offline_active = False
        if not firestore_available:
            offline_messages = session.get('offline_messages')
            if isinstance(offline_messages, list):
                cached_unsynced = len(offline_messages)
            if not offline_reason:
                offline_reason = 'unknown'
            offline_active = cached_unsynced > 0

        # Check if response looks like an email
        email_data = None
        is_email = looks_like_email(response_text, user_asked_for_email=is_email_request(user_prompt))
        if is_email:
            email_data = parse_email_from_response(response_text)
            logger.info(f"Email detected, parsed: subject='{email_data.get('subject', '')[:30]}...'")

        # Check for pending dream notification
        dream_notification = None
        if not guest_mode and firestore_available:
            try:
                ds = get_dream_state(db, user_id)
                if ds.get('pending_notification'):
                    dream_notification = ds['pending_notification']
                    update_dream_state(db, user_id, {'pending_notification': None})
            except Exception:
                pass

        response_data = {
            'response': response_text,
            'is_code': is_code_response,
            'sentiment': sentiment,
            'tokenized_response': tokenize_sentences(response_text) if not is_code_response else [],
            'search_results': [result.to_dict() for result in search_results],
            'render_mode': effective_render_mode,
            'artifact': artifact_payload,
            'artifact_reference': artifact_reference,
            'artifact_status': artifact_status,
            'artifact_error': routing_decision.error_message,  # User-friendly error message
            'message_id': response_message_id,
            'chat_summary': response_text,
            'raw_response': full_response_text,
            'session_id': session_id,
            'session_name': title,
            'active_page_id': resolved_page_id,
            'offline_mode': offline_active,
            'offline_reason': offline_reason if offline_active else None,
            'offline_cached_messages': cached_unsynced,
            'memory_updated': memory_updated,
            'dream_notification': dream_notification,
            'next_steps': next_steps if next_steps else None,
            # Guest mode info
            'guest_mode': guest_mode,
            'guest_message_count': get_guest_message_count() if guest_mode else None,
            'guest_message_limit': GUEST_MESSAGE_LIMIT if guest_mode else None,
            'guest_show_banner': get_guest_message_count() >= GUEST_BANNER_THRESHOLD if guest_mode else False,
            # DeepSeek R1 reasoning content (chain-of-thought)
            'reasoning': reasoning_content if reasoning_content else None,
            # Email composition data
            'is_email': is_email,
            'email': email_data,
            # Crypto chart data
            'crypto_chart': crypto_chart
        }

        # Include per-message model provenance when available (for Auto details UI)
        if isinstance(chat_data, dict):
            for key in (
                'auto_selected',
                'model_id',
                'model_label',
                'model_provider',
                'model_provider_label',
                'task_type',
            ):
                if key in chat_data:
                    response_data[key] = chat_data.get(key)

        return jsonify(response_data)

    except RequestEntityTooLarge:
        logger.error("Request payload too large.")
        return jsonify({'error': 'Payload too large. Maximum allowed size is 16 MB.'}), 413
    except KeyError as e:
        logger.error(f"Missing key in data: {str(e)}")
        return jsonify({'error': f"Missing key: {str(e)}"}), 400
    except ModelProviderError:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error at /chat endpoint for user {session.get('user', {}).get('uid', 'unknown')}: {e}",
            exc_info=True
        )
        return jsonify({'error': 'An unexpected error occurred.'}), 500
