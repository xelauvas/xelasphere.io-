"""Email draft routes blueprint.

Routes:
- GET /email-drafts - List all email drafts
- GET /email-drafts/<draft_id> - Get specific email draft
- POST /email-drafts - Save or update an email draft
- DELETE /email-drafts/<draft_id> - Delete an email draft
"""

import logging
import re

import requests
from flask import Blueprint, jsonify, request, session
from google.cloud import firestore as gc_firestore

from app.config import Config, db
from app.limiter import limiter, csrf_required
from app.blueprints.shared import login_required, guest_allowed, error_handler

logger = logging.getLogger(__name__)

email_bp = Blueprint('email', __name__)


# ==================== Email Detection Helpers ====================

def is_email_request(user_prompt: str) -> bool:
    """
    Detect if the user is asking for an email to be written.
    Checks for email-related keywords in multiple languages.
    """
    if not user_prompt or len(user_prompt) < 3:
        return False

    prompt_lower = user_prompt.lower()

    email_keywords = [
        r'\bemail\b', r'\be-mail\b', r'\bmail\b',
        r'\bwrite\s+(?:an?\s+)?(?:email|e-mail|mail)\b',
        r'\bdraft\s+(?:an?\s+)?(?:email|e-mail|mail)\b',
        r'\bcompose\s+(?:an?\s+)?(?:email|e-mail|mail)\b',
        r'\bsend\s+(?:an?\s+)?(?:email|e-mail|mail)\b',
        r'\bcorreo\b', r'\bcorreo electr[oó]nico\b',
        r'\bescribe\s+(?:un\s+)?correo\b', r'\bredacta\s+(?:un\s+)?correo\b',
        r'\bcourriel\b', r'\bmél\b',
        r'\b[eé]crire?\s+(?:un\s+)?(?:courriel|e-mail|mail)\b',
        r'\br[eé]diger?\s+(?:un\s+)?(?:courriel|e-mail|mail)\b',
        r'\bschreiben?\s+(?:eine?\s+)?e-?mail\b',
        r'\bescrever?\s+(?:um\s+)?e-?mail\b',
        r'\bposta elettronica\b',
        r'\bscrivere?\s+(?:una?\s+)?e-?mail\b',
        r'\bscrie\s+(?:un\s+)?e-?mail\b', r'\bredacteaz[aă]\s+(?:un\s+)?e-?mail\b',
        r'\bписьмо\b', r'\bэлектронн(?:ое|ая|ой)\s+письм[оа]\b',
        r'\bнаписать?\s+письмо\b',
        r'电子邮件', r'邮件', r'写邮件',
        r'メール', r'電子メール',
        r'이메일', r'메일',
        r'بريد إلكتروني', r'إيميل',
        r'ईमेल',
    ]

    for pattern in email_keywords:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            logger.info(f"Email request detected: matched pattern '{pattern}'")
            return True

    return False


def looks_like_email(response_text: str, user_asked_for_email: bool = False) -> bool:
    """
    Check if the response looks like an email.
    Uses two-signal detection:
    - If user_asked_for_email: only need a sign-off OR a subject line (relaxed)
    - Otherwise: require both greeting + sign-off (strict)
    """
    if not response_text or len(response_text) < 50:
        return False

    text_lower = response_text.lower()

    greetings = [
        r'(?:^|\n)\s*(?:dear|hi|hello|hey)\s+[\w\[\]]',
        r'(?:^|\n)\s*(?:estimad[oa]|querid[oa]|hola)\s+[\w\[\]]',
        r'(?:^|\n)\s*(?:cher|chère|bonjour)\s*[\w\[\]]?',
        r'(?:^|\n)\s*(?:liebe[rs]?|sehr geehrte)\s*[\w\[\]]?',
        r'(?:^|\n)\s*(?:stimate|stimată|dragă|bună)\s*[\w\[\]]?',
    ]

    sign_offs = [
        r'(?:best|kind|warm)?\s*regards',
        r'sincerely', r'thanks', r'cheers', r'best wishes',
        r'saludos', r'atentamente',
        r'cordialement',
        r'cu stimă', r'cu respect',
        r'\[your name\]', r'\[name\]', r'\[tu nombre\]', r'\[numele tău\]',
    ]

    subject_patterns = [
        r'(?:^|\n)\s*\*{0,2}(?:subject|asunto|objet|betreff|subiect)[:\*]\s*.+',
    ]

    # Search the full text for greeting (AI may add preamble before the email)
    has_greeting = any(re.search(p, text_lower, re.IGNORECASE) for p in greetings)
    has_sign_off = any(re.search(p, text_lower[-500:], re.IGNORECASE) for p in sign_offs)
    has_subject = any(re.search(p, text_lower, re.IGNORECASE) for p in subject_patterns)

    logger.info(f"EMAIL CHECK: user_asked={user_asked_for_email}, has_greeting={has_greeting}, has_sign_off={has_sign_off}, has_subject={has_subject}")

    if user_asked_for_email:
        # User explicitly asked for an email — only need one structural signal
        return has_sign_off or has_subject or has_greeting
    else:
        # Unsolicited — require greeting + sign-off to avoid false positives
        return has_greeting and has_sign_off


def parse_email_from_response(response_text: str) -> dict:
    """
    Parse the AI response to extract email components (to, subject, body).
    """
    if not response_text:
        return None

    email_data = {'to': '', 'subject': '', 'body': ''}
    email_content = response_text.strip()

    subject_match = re.search(r'^(?:\*\*)?(?:subject|asunto|objet|betreff|oggetto|onderwerp|re|subj)[:\*]*\s*(.+?)$', email_content, re.IGNORECASE | re.MULTILINE)
    if subject_match:
        email_data['subject'] = subject_match.group(1).strip().strip('*')

    to_match = re.search(r'^(?:to|para|à|an|a)[:\s]+(.+?)$', email_content, re.IGNORECASE | re.MULTILINE)
    if to_match:
        email_data['to'] = to_match.group(1).strip()

    greeting_patterns = [
        r'^(?:dear|hi|hello|hey|good\s+(?:morning|afternoon|evening))\s+[\w\[\]]',
        r'^(?:estimad[oa]|querid[oa]|hola)\s+[\w\[\]]',
        r'^(?:cher|chère|bonjour|salut)\s+[\w\[\]]',
        r'^(?:sehr\s+geehrt|liebe[rs]?|hallo)\s+[\w\[\]]',
        r'^\[(?:recipient|name|client|hiring)',
        r'^(?:i\s+hope\s+this)',
    ]

    signoff_patterns = [
        r'(?:best|kind|warm)\s*regards',
        r'(?:sincerely|cordially|respectfully)',
        r'(?:thank\s*you|thanks)',
        r'(?:yours\s+(?:truly|faithfully))',
        r'\[your\s+(?:name|full\s+name|signature)\]',
        r'(?:atentamente|saludos|cordialmente)',
        r'(?:cordialement|salutations)',
    ]

    lines = email_content.split('\n')
    email_start = 0
    email_end = len(lines)

    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        for pattern in greeting_patterns:
            if re.search(pattern, line_lower, re.IGNORECASE):
                email_start = i
                break
        if email_start > 0:
            break

    for i in range(len(lines) - 1, -1, -1):
        line_lower = lines[i].lower().strip()
        for pattern in signoff_patterns:
            if re.search(pattern, line_lower, re.IGNORECASE):
                email_end = min(i + 7, len(lines))
                break
        if email_end < len(lines):
            break

    body_lines = lines[email_start:email_end]
    body = '\n'.join(body_lines)
    body = re.sub(r'^(?:\*\*)?(?:subject|asunto|objet|betreff|oggetto|onderwerp|re|subj|to|para|à|an|from|de|cc|bcc)[:\*]*\s*.+?$', '', body, flags=re.IGNORECASE | re.MULTILINE)
    body = re.sub(r'^[-=\*]{3,}\s*$', '', body, flags=re.MULTILINE)
    # Strip markdown formatting — email body should be plain text
    body = re.sub(r'\*\*(.+?)\*\*', r'\1', body)  # **bold**
    body = re.sub(r'\*(.+?)\*', r'\1', body)      # *italic*
    body = re.sub(r'__(.+?)__', r'\1', body)      # __bold__
    body = re.sub(r'_(.+?)_', r'\1', body)        # _italic_
    body = re.sub(r'^#{1,6}\s+', '', body, flags=re.MULTILINE)  # # headings
    body = re.sub(r'^\s*[-*+]\s+', '- ', body, flags=re.MULTILINE)  # normalize bullet markers
    body = re.sub(r'\n{3,}', '\n\n', body)
    email_data['body'] = body.strip()

    if not email_data['subject'] and body:
        first_line = body.split('\n')[0][:60]
        if first_line:
            email_data['subject'] = first_line + ('...' if len(body.split('\n')[0]) > 60 else '')

    return email_data


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


@email_bp.route('/api/edit-text', methods=['POST'])
@limiter.limit("20 per minute")
@csrf_required
@error_handler
@guest_allowed
def edit_text():
    """
    AI-assisted text editing for email composer toolbar.
    Takes selected text and an instruction, returns edited text.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        text = data.get('text', '').strip()
        instruction = data.get('instruction', '').strip()

        logger.info(f"Edit text request - instruction: {instruction[:50]}...")

        if not text or not instruction:
            return jsonify({'error': 'Text and instruction are required'}), 400

        if len(text) > 2000:
            return jsonify({'error': 'Text too long (max 2000 characters)'}), 400

        # Use Groq API for quick edits (consistent with rest of app)
        groq_key = Config.GROQ_API_KEY
        if not groq_key:
            return jsonify({'error': 'API key not configured'}), 500

        edit_prompt = f"""Edit the following text according to the instruction. Return ONLY the edited text, nothing else.

Instruction: {instruction}

Text to edit:
{text}

Edited text:"""

        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": "You are a helpful text editor. Edit text exactly as instructed. Return only the edited text without any explanations or additional formatting."},
                {"role": "user", "content": edit_prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }

        response = requests.post(
            Config.API_URL,
            json=payload,
            headers=headers,
            timeout=15
        )
        response.raise_for_status()

        result = response.json()
        edited_text = result['choices'][0]['message']['content'].strip()
        logger.info(f"Edit text success - result length: {len(edited_text)}")

        return jsonify({'edited_text': edited_text})

    except Exception as e:
        logger.error(f"Edit text error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
