"""Project CRUD routes. See spec:
docs/superpowers/specs/2026-05-22-projects-design.md
"""
from __future__ import annotations

import logging
import os
import re
import uuid

from flask import Blueprint, jsonify, request, session
from werkzeug.utils import secure_filename

from app.config import Config, db
from app.limiter import limiter, csrf_required
from app.blueprints.shared import error_handler, login_required, chat_session
from app.project_service import ProjectService
from app.text_extractor import TextExtractorUtility

logger = logging.getLogger(__name__)

project_bp = Blueprint('project_bp', __name__)

MAX_NAME_LEN = 120
MAX_INSTRUCTIONS_LEN = 8_000
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB per file
COLOR_PALETTE = [
    '#d97706', '#dc2626', '#db2777', '#7c3aed', '#2563eb',
    '#0891b2', '#16a34a', '#ca8a04', '#475569', '#737373',
]


def _service() -> ProjectService:
    return ProjectService(db)


def _validate_color(color):
    if not isinstance(color, str) or not color.startswith('#'):
        return False
    if len(color) not in (4, 7, 9):
        return False
    try:
        int(color[1:], 16)
        return True
    except ValueError:
        return False


@project_bp.route('/projects', methods=['GET'])
@error_handler
@login_required
def list_projects():
    uid = session['user']['uid']
    return jsonify({'projects': _service().list(uid)})


@project_bp.route('/projects', methods=['POST'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@login_required
def create_project():
    uid = session['user']['uid']
    body = request.get_json(silent=True) or {}
    name = body.get('name')
    if not isinstance(name, str) or not name.strip():
        return jsonify({'error': 'name required'}), 400
    name = name.strip()
    if len(name) > MAX_NAME_LEN:
        return jsonify({'error': f'name max {MAX_NAME_LEN} chars'}), 400

    color = body.get('color')
    if color is None:
        idx = _service().count(uid) % len(COLOR_PALETTE)
        color = COLOR_PALETTE[idx]
    elif not _validate_color(color):
        return jsonify({'error': 'color must be valid hex starting with #'}), 400

    try:
        p = _service().create(uid, name=name, color=color)
    except ProjectService.LimitExceeded as e:
        return jsonify({'error': str(e)}), 409
    return jsonify({'project': p}), 201


@project_bp.route('/projects/<project_id>', methods=['GET'])
@error_handler
@login_required
def get_project(project_id):
    uid = session['user']['uid']
    p = _service().get(uid, project_id)
    if not p:
        return jsonify({'error': 'Project not found'}), 404
    return jsonify({'project': p})


@project_bp.route('/projects/<project_id>', methods=['PATCH'])
@limiter.limit("60 per minute")
@csrf_required
@error_handler
@login_required
def patch_project(project_id):
    uid = session['user']['uid']
    body = request.get_json(silent=True) or {}
    cleaned = {}

    if 'name' in body:
        if not isinstance(body['name'], str) or not body['name'].strip():
            return jsonify({'error': 'name must be non-empty string'}), 400
        name = body['name'].strip()
        if len(name) > MAX_NAME_LEN:
            return jsonify({'error': f'name max {MAX_NAME_LEN} chars'}), 400
        cleaned['name'] = name
    if 'color' in body:
        if not _validate_color(body['color']):
            return jsonify({'error': 'color must be valid hex starting with #'}), 400
        cleaned['color'] = body['color']
    if 'instructions' in body:
        if body['instructions'] is None:
            cleaned['instructions'] = ''
        else:
            if not isinstance(body['instructions'], str):
                return jsonify({'error': 'instructions must be string'}), 400
            if len(body['instructions']) > MAX_INSTRUCTIONS_LEN:
                return jsonify({'error': f'instructions max {MAX_INSTRUCTIONS_LEN} chars'}), 400
            cleaned['instructions'] = body['instructions']

    if not cleaned:
        return jsonify({'error': 'no editable fields provided'}), 400

    ok = _service().update(uid, project_id, cleaned)
    if not ok:
        return jsonify({'error': 'Project not found'}), 404
    return ('', 204)


@project_bp.route('/projects/<project_id>', methods=['DELETE'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@login_required
def delete_project(project_id):
    uid = session['user']['uid']
    cascade = (request.args.get('cascade') or 'keep').lower()
    if cascade not in ('keep', 'delete'):
        return jsonify({'error': "cascade must be 'keep' or 'delete'"}), 400

    from google.cloud.firestore_v1.base_query import FieldFilter
    sessions_ref = db.collection('users').document(uid).collection('chat_sessions')
    child_ids = [
        snap.id for snap in
        sessions_ref.where(filter=FieldFilter('project_id', '==', project_id)).stream()
    ]

    if cascade == 'delete' and child_ids:
        chat_session.bulk_delete_sessions(uid, child_ids)
    elif cascade == 'keep' and child_ids:
        batch = db.batch()
        for cid in child_ids:
            batch.update(sessions_ref.document(cid), {'project_id': None})
        batch.commit()

    ok = _service().delete(uid, project_id)
    if not ok:
        return jsonify({'error': 'Project not found'}), 404
    return ('', 204)


@project_bp.route('/projects/<project_id>/files', methods=['POST'])
@limiter.limit("20 per minute")
@csrf_required
@error_handler
@login_required
def upload_project_file(project_id):
    uid = session['user']['uid']
    svc = _service()
    if not svc.get(uid, project_id):
        return jsonify({'error': 'Project not found'}), 404

    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'No file provided'}), 400

    # Size check
    f.stream.seek(0, os.SEEK_END)
    size_bytes = f.stream.tell()
    f.stream.seek(0)
    if size_bytes > MAX_FILE_BYTES:
        return jsonify({'error': 'File too large (>10 MB)'}), 413

    filename = secure_filename(f.filename) or f'file-{uuid.uuid4().hex}'
    sanitized_user_id = re.sub(r'[^\w\-]', '_', uid)
    project_dir = os.path.join(
        os.path.abspath(Config.UPLOAD_FOLDER),
        sanitized_user_id, 'projects', project_id,
    )
    os.makedirs(project_dir, exist_ok=True)
    file_id = uuid.uuid4().hex
    storage_path_abs = os.path.join(project_dir, f'{file_id}-{filename}')
    f.save(storage_path_abs)
    storage_path_rel = os.path.relpath(storage_path_abs, os.path.abspath(Config.UPLOAD_FOLDER))

    try:
        extracted_text, _debug = TextExtractorUtility.extract_text(storage_path_abs)
    except Exception as e:
        logger.warning(f"Text extraction failed for {storage_path_abs}: {e}")
        extracted_text = ''

    try:
        meta = svc.add_file(
            uid, project_id,
            file_id=file_id, filename=filename,
            mime_type=f.mimetype or 'application/octet-stream',
            size_bytes=size_bytes, storage_path=storage_path_rel,
            extracted_text=extracted_text or '',
        )
    except ProjectService.LimitExceeded as e:
        try:
            os.remove(storage_path_abs)
        except OSError:
            pass
        return jsonify({'error': str(e)}), 409

    return jsonify({'file': meta}), 201


@project_bp.route('/projects/<project_id>/files/<file_id>', methods=['DELETE'])
@limiter.limit("30 per minute")
@csrf_required
@error_handler
@login_required
def delete_project_file(project_id, file_id):
    uid = session['user']['uid']
    svc = _service()
    p = svc.get(uid, project_id)
    if not p:
        return jsonify({'error': 'Project not found'}), 404
    meta = next((f for f in p['files'] if f.get('file_id') == file_id), None)
    if not meta:
        return jsonify({'error': 'File not found'}), 404

    abs_path = os.path.join(os.path.abspath(Config.UPLOAD_FOLDER), meta['storage_path'])
    try:
        os.remove(abs_path)
    except OSError as e:
        logger.warning(f'Failed to remove disk file: {e}')

    svc.remove_file(uid, project_id, file_id)
    return ('', 204)
