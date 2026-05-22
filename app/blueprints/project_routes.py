"""Project CRUD routes. See spec:
docs/superpowers/specs/2026-05-22-projects-design.md
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request, session

from app.config import db
from app.limiter import limiter, csrf_required
from app.blueprints.shared import error_handler, login_required, chat_session
from app.project_service import ProjectService

logger = logging.getLogger(__name__)

project_bp = Blueprint('project_bp', __name__)

MAX_NAME_LEN = 120
MAX_INSTRUCTIONS_LEN = 8_000
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
