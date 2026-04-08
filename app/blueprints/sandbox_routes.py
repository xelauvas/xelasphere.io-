"""
API endpoints for server-side Python sandbox execution.
SSE streaming of container output to the browser.
"""

import json
import logging
import requests as http_requests
from flask import Blueprint, request, Response, stream_with_context, session, jsonify

from app.services.sandbox_manager import sandbox_manager, detect_mode, detect_extra_packages, PREINSTALLED

logger = logging.getLogger(__name__)

sandbox_bp = Blueprint('sandbox', __name__)


@sandbox_bp.route('/api/sandbox/preflight', methods=['POST'])
def sandbox_preflight():
    """Check what a script needs before running. Returns mode + extra packages."""
    user_id = session.get('user', {}).get('uid') if session.get('user') else None
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json()
    if not data or not data.get('code'):
        return jsonify({"error": "No code provided"}), 400

    code = data['code']
    # Build local files dict for filtering local imports
    local_files = {}
    if data.get('files'):
        for f in data['files']:
            local_files[f['path']] = f.get('content', '')
    mode = detect_mode(code)
    extra = detect_extra_packages(code, local_files=local_files)

    # If user already has a running container, packages are likely installed
    has_container = sandbox_manager._find_running_container(user_id) is not None

    return jsonify({
        "mode": mode,
        "extra_packages": extra if not has_container else [],
        "preinstalled": len(extra) == 0 or has_container,
        "has_container": has_container,
    })


@sandbox_bp.route('/api/sandbox/run', methods=['POST'])
def sandbox_run():
    """Start a sandboxed Python execution. Returns SSE stream."""
    user_id = session.get('user', {}).get('uid') if session.get('user') else None
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401

    if not sandbox_manager.available:
        return jsonify({"error": "Sandbox not available"}), 503

    data = request.get_json()
    if not data or not data.get('code'):
        return jsonify({"error": "No code provided"}), 400

    code = data['code']
    files = {}
    entry = data.get('entry', 'main.py')

    # Build files dict: {path: content}
    if data.get('files'):
        for f in data['files']:
            files[f['path']] = f['content']
    else:
        files[entry] = code

    def generate():
        try:
            for event in sandbox_manager.run_code(user_id, code, files, entry):
                yield f"data: {json.dumps(event)}\n\n"
        except GeneratorExit:
            sandbox_manager.kill(user_id)
        except Exception as e:
            logger.error(f"Sandbox stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@sandbox_bp.route('/api/sandbox/stop', methods=['POST'])
def sandbox_stop():
    """Kill a user's running sandbox container."""
    user_id = session.get('user', {}).get('uid') if session.get('user') else None
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401

    sandbox_manager.kill(user_id)
    return jsonify({"ok": True})


@sandbox_bp.route('/api/sandbox/proxy/<container_id>/', defaults={'path': ''})
@sandbox_bp.route('/api/sandbox/proxy/<container_id>/<path:path>')
def sandbox_proxy(container_id, path):
    """Reverse proxy to a running app container."""
    user_id = session.get('user', {}).get('uid') if session.get('user') else None
    if not user_id:
        return "Unauthorized", 401

    info = sandbox_manager.get_container(container_id)
    if not info or info.user_id != user_id:
        return "Container not found", 404

    if not info.app_port:
        return "App port not ready", 503

    target_url = f"http://127.0.0.1:{info.app_port}/{path}"
    try:
        resp = http_requests.request(
            method=request.method,
            url=target_url,
            headers={k: v for k, v in request.headers if k.lower() != 'host'},
            data=request.get_data(),
            params=request.args,
            allow_redirects=False,
            timeout=10,
        )
        excluded_headers = {'transfer-encoding', 'content-encoding', 'content-length', 'connection'}
        headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded_headers}
        return Response(resp.content, status=resp.status_code, headers=headers)
    except Exception as e:
        return f"Proxy error: {e}", 502


@sandbox_bp.route('/api/sandbox/vnc/<container_id>/', defaults={'path': 'vnc.html'})
@sandbox_bp.route('/api/sandbox/vnc/<container_id>/<path:path>')
def sandbox_vnc(container_id, path):
    """Proxy noVNC static files and WebSocket to the container."""
    user_id = session.get('user', {}).get('uid') if session.get('user') else None
    if not user_id:
        return "Unauthorized", 401

    info = sandbox_manager.get_container(container_id)
    if not info or info.user_id != user_id:
        return "Container not found", 404

    if not info.vnc_port:
        return "VNC not ready", 503

    target_url = f"http://127.0.0.1:{info.vnc_port}/{path}"
    if request.query_string:
        target_url += f"?{request.query_string.decode()}"

    try:
        resp = http_requests.get(target_url, timeout=10)
        excluded_headers = {'transfer-encoding', 'content-encoding', 'content-length', 'connection'}
        headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded_headers}
        return Response(resp.content, status=resp.status_code, headers=headers)
    except Exception as e:
        return f"VNC proxy error: {e}", 502
