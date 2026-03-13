"""Artifact management routes blueprint.

Routes:
- GET /chat/<session_id>/artifacts - List all artifacts for a session
- GET /chat/<session_id>/artifacts/<artifact_id> - Get specific artifact
- GET /chat/<session_id>/artifacts/<artifact_id>/versions/<version_id> - Get artifact version
- GET /chat/<session_id>/artifacts/<artifact_id>/versions/<version_id>/zip - Download version as zip
- POST /chat/<session_id>/artifacts/active - Set active artifact version
- POST /chat/<session_id>/artifacts/<artifact_id>/edit - Edit artifact
- POST /chat/<session_id>/artifacts/<artifact_id>/share - Share artifact
- GET /shared/<share_token> - View shared artifact (public)
- GET /chat/<session_id>/artifacts/<artifact_id>/comments - Get comments
- POST /chat/<session_id>/artifacts/<artifact_id>/comments - Post comment
"""

import logging
import re
import secrets
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request, session
from google.cloud import firestore

from app.config import db
from app.limiter import limiter, csrf_required
from app.blueprints.shared import (
    login_required, error_handler, make_etag_response, artifact_store
)

logger = logging.getLogger(__name__)

artifacts_bp = Blueprint('artifacts', __name__)


@artifacts_bp.route('/chat/<session_id>/artifacts', methods=['GET'])
@error_handler
@login_required
def list_session_artifacts(session_id: str):
    """List all artifacts for a chat session."""
    user_id = session['user']['uid']
    try:
        payload = artifact_store.list_artifacts(user_id, session_id)
        return make_etag_response(payload)
    except Exception as exc:
        logger.error("Failed to load artifacts for user %s session %s: %s", user_id, session_id, exc)
        return jsonify({'error': 'Failed to load artifacts.'}), 500


@artifacts_bp.route('/chat/<session_id>/artifacts/<artifact_id>', methods=['GET'])
@error_handler
@login_required
def get_session_artifact(session_id: str, artifact_id: str):
    """Get a specific artifact by ID."""
    user_id = session['user']['uid']
    try:
        artifact = artifact_store.get_artifact(user_id, session_id, artifact_id)
        if not artifact:
            return jsonify({'error': 'Artifact not found.'}), 404
        return make_etag_response({'artifact': artifact})
    except Exception as exc:
        logger.error("Failed to fetch artifact %s for user %s session %s: %s", artifact_id, user_id, session_id, exc)
        return jsonify({'error': 'Failed to load artifact.'}), 500


@artifacts_bp.route('/chat/<session_id>/artifacts/<artifact_id>/versions/<version_id>', methods=['GET'])
@error_handler
@login_required
def get_artifact_version(session_id: str, artifact_id: str, version_id: str):
    """Get a specific version of an artifact."""
    user_id = session['user']['uid']
    try:
        version = artifact_store.get_artifact_version(user_id, session_id, artifact_id, version_id, include_files=True)
        if not version:
            return jsonify({'error': 'Version not found.'}), 404
        return make_etag_response({'version': version})
    except Exception as exc:
        logger.error("Failed to fetch artifact version %s/%s for user %s session %s: %s", artifact_id, version_id, user_id, session_id, exc)
        return jsonify({'error': 'Failed to load artifact version.'}), 500


@artifacts_bp.route('/chat/<session_id>/artifacts/<artifact_id>/versions/<version_id>/zip', methods=['GET'])
@error_handler
@login_required
def download_artifact_version_zip(session_id: str, artifact_id: str, version_id: str):
    """Download an artifact version as a ZIP file."""
    user_id = session['user']['uid']
    try:
        zip_bytes = artifact_store.download_version_zip(user_id, session_id, artifact_id, version_id)
        if not zip_bytes:
            return jsonify({'error': 'Version not found.'}), 404
        response = current_app.response_class(zip_bytes, mimetype='application/zip')
        response.headers['Content-Disposition'] = f'attachment; filename="{artifact_id}_{version_id}.zip"'
        return response
    except Exception as exc:
        logger.error("Failed to download artifact version zip %s/%s for user %s: %s", artifact_id, version_id, user_id, exc)
        return jsonify({'error': 'Failed to prepare download.'}), 500


@artifacts_bp.route('/chat/<session_id>/artifacts/active', methods=['POST'])
@csrf_required
@error_handler
@login_required
def set_active_artifact(session_id: str):
    """Set the active version of an artifact."""
    user_id = session['user']['uid']
    data = request.get_json() or {}
    artifact_id = data.get('artifact_id')
    version_id = data.get('version_id')
    if not artifact_id or not version_id:
        return jsonify({'error': 'artifact_id and version_id are required.'}), 400
    try:
        logger.info(f"Setting active artifact: user={user_id}, session={session_id}, artifact={artifact_id}, version={version_id}")
        updated = artifact_store.set_active_version(user_id, session_id, artifact_id, version_id)
        if not updated:
            logger.warning(f"Artifact or version not found: {artifact_id}/{version_id}")
            return jsonify({'error': 'Artifact or version not found.'}), 404
        logger.info(f"Successfully set active artifact: {artifact_id}/{version_id}")
        return jsonify({'artifact': updated})
    except Exception as exc:
        logger.error("Failed to set active artifact version %s/%s for user %s session %s: %s", artifact_id, version_id, user_id, session_id, exc, exc_info=True)
        return jsonify({'error': 'Failed to update artifact state.'}), 500


@artifacts_bp.route('/chat/<session_id>/artifacts/<artifact_id>/edit', methods=['POST'])
@csrf_required
@error_handler
@login_required
def edit_artifact(session_id: str, artifact_id: str):
    """
    Create a new version of an artifact with edited file contents.

    Request body:
    {
        "files": [{"path": "...", "content": "...", "language": "..."}],
        "entrypointPath": "...",
        "primaryLanguage": "...",
        "title": "...",
        "description": "..."
    }
    """
    user_id = session['user']['uid']
    data = request.get_json() or {}

    # Validate required fields
    if not data.get('files'):
        return jsonify({'error': 'files array is required.'}), 400

    try:
        # Build artifact payload for new version
        artifact_payload = {
            'files': data.get('files', []),
            'entrypointPath': data.get('entrypointPath'),
            'primaryLanguage': data.get('primaryLanguage', 'code')
        }

        # Save as new version using artifact store
        result = artifact_store.save_artifact(
            user_id=user_id,
            session_id=session_id,
            artifact_id=artifact_id,
            payload=artifact_payload,
            title=data.get('title', 'Edited Artifact'),
            description=data.get('description', 'User-edited version')
        )

        if not result:
            raise Exception('Failed to save edited version')

        logger.info(
            "User %s created edited version of artifact %s in session %s",
            user_id,
            artifact_id,
            session_id
        )

        return jsonify({
            'success': True,
            'artifactId': result.get('artifact_id'),
            'versionId': result.get('version_id'),
            'message': 'Edits saved successfully.'
        })

    except Exception as exc:
        logger.error(
            "Failed to save edited artifact %s for user %s session %s: %s",
            artifact_id,
            user_id,
            session_id,
            exc
        )
        return jsonify({'error': 'Failed to save edits.'}), 500


@artifacts_bp.route('/chat/<session_id>/artifacts/<artifact_id>/share', methods=['POST'])
@csrf_required
@error_handler
@login_required
def share_artifact(session_id: str, artifact_id: str):
    """
    Generate a shareable public link for an artifact.

    Creates a unique share token and stores it in Firestore.
    Returns a public URL that anyone can use to view the artifact.
    """
    user_id = session['user']['uid']

    try:
        # Generate cryptographically secure share token (32 bytes = 256 bits of entropy)
        share_token = secrets.token_urlsafe(32)

        # Get artifact to verify it exists
        artifact = artifact_store.get_artifact(user_id, session_id, artifact_id)
        if not artifact:
            return jsonify({'error': 'Artifact not found.'}), 404

        # Store share token in Firestore
        share_ref = db.collection('shared_artifacts').document(share_token)
        share_ref.set({
            'artifactId': artifact_id,
            'sessionId': session_id,
            'userId': user_id,
            'createdAt': firestore.SERVER_TIMESTAMP,
            'title': artifact.get('title', 'Shared Artifact'),
            'expiresAt': None  # Can implement expiration later
        })

        # Build public share URL
        share_url = f"{request.host_url}shared/{share_token}"

        logger.info(
            "User %s created share link for artifact %s: %s",
            user_id,
            artifact_id,
            share_token
        )

        return jsonify({
            'success': True,
            'shareToken': share_token,
            'shareUrl': share_url
        })

    except Exception as exc:
        logger.error(
            "Failed to create share link for artifact %s (user %s session %s): %s",
            artifact_id,
            user_id,
            session_id,
            exc
        )
        return jsonify({'error': 'Failed to generate share link.'}), 500


@artifacts_bp.route('/shared/<share_token>', methods=['GET'])
@limiter.limit("30 per minute")  # Rate limit to prevent brute-force enumeration
@error_handler
def view_shared_artifact(share_token: str):
    """
    Public route to view a shared artifact.

    Renders a read-only view of the artifact without requiring authentication.
    """
    try:
        # Validate token format to prevent database queries with invalid tokens
        # Accept both UUID format and URL-safe base64 tokens
        uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
        urlsafe_pattern = re.compile(r'^[A-Za-z0-9_-]{32,}$')

        if not (uuid_pattern.match(share_token) or urlsafe_pattern.match(share_token)):
            logger.warning(f"Invalid share token format attempted: {share_token[:20]}...")
            return "Share link not found or has expired.", 404

        # Fetch share data from Firestore
        share_ref = db.collection('shared_artifacts').document(share_token)
        share_doc = share_ref.get()

        if not share_doc.exists:
            # Log failed attempts to detect enumeration attacks
            logger.info(f"Share token not found: {share_token[:8]}... (IP: {request.remote_addr})")
            return "Share link not found or has expired.", 404

        share_data = share_doc.to_dict()

        # Check if share link has expired (if expiration is implemented)
        if share_data.get('expiresAt'):
            expires_at = share_data['expiresAt']
            if datetime.now() > expires_at:
                return "This share link has expired.", 410

        # Fetch artifact data
        artifact = artifact_store.get_artifact(
            share_data['userId'],
            share_data['sessionId'],
            share_data['artifactId']
        )

        if not artifact:
            return "Artifact not found.", 404

        # Format shared date
        shared_at = None
        if share_data.get('createdAt'):
            try:
                created_timestamp = share_data['createdAt']
                if hasattr(created_timestamp, 'timestamp'):
                    # Firestore timestamp
                    shared_at = datetime.fromtimestamp(created_timestamp.timestamp()).strftime('%B %d, %Y')
                else:
                    shared_at = str(created_timestamp)
            except:
                shared_at = 'recently'

        # Render public artifact view template
        return render_template(
            'shared_artifact.html',
            artifact=artifact,
            shared_at=shared_at or 'recently'
        )

    except Exception as exc:
        logger.error("Failed to load shared artifact %s: %s", share_token, exc)
        return "Failed to load shared artifact.", 500


@artifacts_bp.route('/chat/<session_id>/artifacts/<artifact_id>/comments', methods=['GET'])
@error_handler
@login_required
def get_artifact_comments(session_id: str, artifact_id: str):
    """
    Get all comments for an artifact.

    Returns list of comments ordered by creation time.
    """
    user_id = session['user']['uid']

    try:
        # Get artifact to verify ownership
        artifact = artifact_store.get_artifact(user_id, session_id, artifact_id)
        if not artifact:
            return jsonify({'error': 'Artifact not found.'}), 404

        # Fetch comments from Firestore
        comments_ref = db.collection('users').document(user_id)\
            .collection('chat_sessions').document(session_id)\
            .collection('artifacts').document(artifact_id)\
            .collection('comments')\
            .order_by('createdAt', direction=firestore.Query.ASCENDING)

        comments_docs = comments_ref.get()

        comments = []
        for doc in comments_docs:
            comment_data = doc.to_dict()
            comment_data['commentId'] = doc.id
            comments.append(comment_data)

        logger.info("Loaded %d comments for artifact %s", len(comments), artifact_id)

        return make_etag_response({
            'comments': comments,
            'count': len(comments)
        })

    except Exception as exc:
        logger.error("Failed to load comments for artifact %s: %s", artifact_id, exc)
        return jsonify({'error': 'Failed to load comments.'}), 500


@artifacts_bp.route('/chat/<session_id>/artifacts/<artifact_id>/comments', methods=['POST'])
@csrf_required
@error_handler
@login_required
def post_artifact_comment(session_id: str, artifact_id: str):
    """
    Post a new comment on an artifact.

    Request body:
    {
        "content": "Comment text",
        "filePath": "optional/file/path.js"
    }
    """
    user_id = session['user']['uid']
    user_info = session.get('user', {})
    data = request.get_json() or {}

    # Validate content
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'error': 'Comment content is required.'}), 400

    if len(content) > 5000:
        return jsonify({'error': 'Comment is too long (max 5000 characters).'}), 400

    try:
        # Get artifact to verify ownership
        artifact = artifact_store.get_artifact(user_id, session_id, artifact_id)
        if not artifact:
            return jsonify({'error': 'Artifact not found.'}), 404

        # Create comment document
        comment_data = {
            'content': content,
            'authorId': user_id,
            'authorName': user_info.get('displayName') or user_info.get('email') or 'Anonymous',
            'filePath': data.get('filePath'),
            'createdAt': firestore.SERVER_TIMESTAMP,
            'updatedAt': firestore.SERVER_TIMESTAMP
        }

        # Save to Firestore
        comments_ref = db.collection('users').document(user_id)\
            .collection('chat_sessions').document(session_id)\
            .collection('artifacts').document(artifact_id)\
            .collection('comments')

        comment_ref = comments_ref.add(comment_data)
        comment_id = comment_ref[1].id

        logger.info(
            "User %s posted comment on artifact %s: %s",
            user_id,
            artifact_id,
            comment_id
        )

        return jsonify({
            'success': True,
            'commentId': comment_id,
            'message': 'Comment posted successfully.'
        })

    except Exception as exc:
        logger.error(
            "Failed to post comment on artifact %s for user %s: %s",
            artifact_id,
            user_id,
            exc
        )
        return jsonify({'error': 'Failed to post comment.'}), 500
