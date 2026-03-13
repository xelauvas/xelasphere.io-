"""File upload routes blueprint.

Routes:
- POST /upload - Upload files with OCR processing
- POST /url-metadata - Get metadata for a URL
- GET /uploads/<path:requested_path> - Serve uploaded files
"""

import hashlib
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Tuple

import requests
from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory, session
from google.cloud import firestore as gc_firestore

from app.config import Config, db
from app.file_validator import FileValidator, generate_unique_filename, get_file_type
from app.limiter import limiter, csrf_required
from app.blueprints.shared import login_required, error_handler, executor

logger = logging.getLogger(__name__)

upload_bp = Blueprint('upload', __name__)

# Check OCR availability
try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


def extract_text_concurrently(file_path: str) -> Tuple[str, Dict[str, Any]]:
    """Extract text from a file. Returns (text, debug_info)."""
    from app.text_extractor import TextExtractorUtility
    debug_info = {}
    try:
        text = TextExtractorUtility.extract_text(file_path, debug_info=debug_info)
        return text, debug_info
    except Exception as e:
        logger.error(f"Text extraction failed for {file_path}: {e}")
        return "", {"error": str(e)}


@upload_bp.route('/upload', methods=['POST'])
@limiter.limit("10 per minute")
@csrf_required
@error_handler
@login_required
def upload_file() -> Tuple[Dict[str, Any], int]:
    """Handle file uploads with validation, deduplication, and text extraction."""
    from app.file_processor import FileProcessor

    user_id = session['user']['uid']

    uploaded_files = request.files.getlist('file')
    if not uploaded_files:
        # Support keys like file0, file1 in addition to "file"
        uploaded_files = [f for key, f in request.files.items() if key.startswith('file')]

    # Filter out empty entries
    uploaded_files = [f for f in uploaded_files if f and f.filename]
    if not uploaded_files:
        return jsonify({'error': 'No file provided'}), 400

    # Prepare upload directory once per request
    try:
        sanitized_user_id = re.sub(r'[^\w\-]', '_', user_id)
        date_path = datetime.now().strftime('%Y/%m/%d')
        if not re.match(r'^[\d/]+$', date_path):
            logger.error(f"Invalid date path format: {date_path}")
            return jsonify({'error': 'Server error'}), 500

        base_upload_path = os.path.normpath(os.path.abspath(Config.UPLOAD_FOLDER))
        upload_dir = os.path.normpath(os.path.join(base_upload_path, sanitized_user_id, date_path))
        if not upload_dir.startswith(base_upload_path):
            logger.error(f"Attempted path traversal: {upload_dir}")
            return jsonify({'error': 'Invalid path'}), 400

        os.makedirs(upload_dir, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create upload directory for user {user_id}: {str(e)}")
        return jsonify({'error': 'Failed to save file'}), 500

    processed_files = []

    for uploaded_file in uploaded_files:
        # Validate file
        is_valid, message = FileValidator.validate(uploaded_file)
        if not is_valid:
            return jsonify({'error': message, 'filename': uploaded_file.filename}), 400

        # Calculate hash BEFORE saving to check for duplicates
        file_content = uploaded_file.read()
        md5_hash = hashlib.md5()
        for i in range(0, len(file_content), 65536):
            md5_hash.update(file_content[i:i+65536])
        file_hash = md5_hash.hexdigest()
        file_size = len(file_content)

        # Check for duplicate files for this user across recent sessions
        existing_file = None
        try:
            sessions_ref = db.collection('users').document(user_id).collection('chat_sessions')
            # Only check recent sessions with files, and only fetch the 'files' field
            sessions = sessions_ref.where('has_files', '==', True).select(['files']).limit(50).stream()
            for sess in sessions:
                sess_data = sess.to_dict()
                files = sess_data.get('files', [])
                for f in files:
                    if f.get('md5_hash') == file_hash:
                        existing_file = f
                        logger.info(f"Duplicate file detected for user {user_id}: {uploaded_file.filename} matches {f.get('original_filename')}")
                        break
                if existing_file:
                    break
        except Exception as e:
            logger.warning(f"Error checking for duplicates: {e}")
            # Continue with upload if duplicate check fails

        if existing_file:
            # Return existing file info instead of saving duplicate
            rel_path = None
            for root, dirs, files in os.walk(os.path.join(Config.UPLOAD_FOLDER, sanitized_user_id)):
                if existing_file.get('filename') in files:
                    rel_path = os.path.relpath(os.path.join(root, existing_file.get('filename')), Config.UPLOAD_FOLDER)
                    break

            if rel_path and os.path.exists(os.path.join(Config.UPLOAD_FOLDER, rel_path)):
                # Extract text from existing file
                full_path = os.path.join(Config.UPLOAD_FOLDER, rel_path)
                future = executor.submit(extract_text_concurrently, full_path)
                extracted_text, debug_info = future.result()

                processed_files.append({
                    'metadata': {
                        'file_id': str(uuid.uuid4()),
                        'filename': existing_file.get('filename'),
                        'original_filename': uploaded_file.filename,
                        'upload_date': existing_file.get('upload_date'),
                        'file_size': existing_file.get('file_size'),
                        'mime_type': existing_file.get('mime_type'),
                        'md5_hash': file_hash,
                        'user_id': user_id,
                        'relative_path': rel_path,
                        'ocr_available': OCR_AVAILABLE,
                        'is_duplicate': True
                    },
                    'extracted_text': extracted_text
                })
                continue
            # If existing file not found on disk, fall through to save new copy

        # Reset file stream and save
        uploaded_file.seek(0)
        unique_filename = generate_unique_filename(uploaded_file.filename, user_id)
        file_path = os.path.join(upload_dir, unique_filename)

        try:
            with open(file_path, 'wb') as f:
                f.write(file_content)
        except OSError as e:
            logger.error(f"Failed to save file for user {user_id}: {str(e)}")
            return jsonify({'error': 'Failed to save file'}), 500

        # Extract text asynchronously
        future = executor.submit(extract_text_concurrently, file_path)
        extracted_text, debug_info = future.result()

        # Get MIME type
        with open(file_path, 'rb') as file_handle:
            mime_type = get_file_type(uploaded_file.filename, file_handle)

        file_id = str(uuid.uuid4())
        metadata = {
            'file_id': file_id,
            'filename': unique_filename,
            'original_filename': uploaded_file.filename,
            'upload_date': datetime.now().isoformat(),
            'file_size': file_size,
            'mime_type': mime_type,
            'md5_hash': file_hash,
            'user_id': user_id,
            'relative_path': os.path.relpath(file_path, Config.UPLOAD_FOLDER),
            'ocr_available': OCR_AVAILABLE,
            'ocr_error': debug_info.get('error'),
            'ocr_engine': debug_info.get('engine')
        }

        processed_files.append({
            'metadata': metadata,
            'extracted_text': extracted_text
        })

    if not processed_files:
        return jsonify({'error': 'No valid files provided'}), 400

    session_id = session.get('current_session_id')

    # Store in Firestore if session exists
    if session_id:
        try:
            session_ref = db.collection('users').document(user_id).collection('chat_sessions').document(session_id)
            file_data_list = [{
                'filename': item['metadata']['filename'],
                'original_filename': item['metadata']['original_filename'],
                'upload_date': item['metadata']['upload_date'],
                'file_size': item['metadata']['file_size'],
                'mime_type': item['metadata']['mime_type'],
                'md5_hash': item['metadata']['md5_hash'],
                # Only store a short preview - full text is in RAG chunks
                'text_preview': (item['extracted_text'] or '')[:500],
                'text_length': len(item['extracted_text'] or ''),
                'ocr_error': item['metadata'].get('ocr_error'),
                'ocr_engine': item['metadata'].get('ocr_engine')
            } for item in processed_files]

            session_ref.set({
                'files': gc_firestore.ArrayUnion(file_data_list),
                'last_updated': datetime.now(),
                'has_files': True
            }, merge=True)
            logger.info(f"Successfully updated Firestore with file data for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to update Firestore: {str(e)}")

    combined_text = "\n\n".join([item['extracted_text'] for item in processed_files if item['extracted_text']]).strip()
    session['extracted_text'] = combined_text
    metadata_list = [item['metadata'] for item in processed_files]
    session['file_metadata'] = metadata_list[0] if len(metadata_list) == 1 else metadata_list
    session['file_metadata_list'] = metadata_list

    # Process files for RAG in background (don't block response)
    def process_rag_background(files_to_process, uid, sid):
        """Background RAG processing - chunks and embeds files."""
        try:
            file_processor = FileProcessor(db)
            for item in files_to_process:
                try:
                    file_meta = file_processor.process_file(
                        user_id=uid,
                        filename=item['metadata']['filename'],
                        original_filename=item['metadata']['original_filename'],
                        mime_type=item['metadata']['mime_type'],
                        file_size=item['metadata']['file_size'],
                        extracted_text=item['extracted_text'] or '',
                        file_path=item['metadata'].get('relative_path'),
                        conversation_id=sid
                    )
                    logger.info(f"RAG processed file {file_meta.file_id}: {file_meta.num_chunks} chunks")
                except Exception as e:
                    logger.error(f"Failed to RAG process file {item['metadata']['filename']}: {e}")
        except Exception as e:
            logger.error(f"Background RAG processing failed: {e}")

    # Submit RAG processing to background thread
    executor.submit(process_rag_background, processed_files, user_id, session_id)

    # Return response immediately with extracted text (RAG continues in background)
    return jsonify({
        'message': 'File(s) uploaded and processed successfully',
        'extracted_text': combined_text,
        'metadata': metadata_list[0] if len(metadata_list) == 1 else metadata_list,
        'files': [{
            **item['metadata'],
            'extracted_text': item['extracted_text']
        } for item in processed_files],
        'rag_status': 'processing',  # Indicate RAG is happening in background
        'status': 'success',
        'session_id': session_id
    }), 200


@upload_bp.route('/url-metadata', methods=['POST'])
@limiter.limit("20 per minute")
@csrf_required
@error_handler
def get_url_metadata():
    """
    Fetch metadata (title, description, favicon, image) for a URL.
    Used for rich link previews in the chat input.
    """
    from urllib.parse import urlparse

    data = request.get_json()
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    # Basic URL validation
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return jsonify({'error': 'Invalid URL'}), 400

        domain = parsed.netloc.replace('www.', '')

        # Determine URL type
        url_type = 'article'
        if 'youtube.com' in domain or 'youtu.be' in domain:
            url_type = 'youtube'
        elif 'github.com' in domain:
            url_type = 'github'
        elif 'twitter.com' in domain or 'x.com' in domain:
            url_type = 'twitter'
        elif parsed.path.endswith('.pdf'):
            url_type = 'pdf'
        elif 'arxiv.org' in domain:
            url_type = 'arxiv'

        # Fetch metadata
        metadata = {
            'url': url,
            'domain': domain,
            'type': url_type,
            'title': domain,
            'description': None,
            'image': None,
            'favicon': f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
        }

        # For YouTube, use oEmbed
        if url_type == 'youtube':
            try:
                # Extract video ID
                video_id = None
                if 'youtu.be/' in url:
                    video_id = url.split('youtu.be/')[-1].split('?')[0]
                elif 'v=' in url:
                    video_id = url.split('v=')[-1].split('&')[0]

                if video_id:
                    oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
                    resp = requests.get(oembed_url, timeout=5)
                    if resp.status_code == 200:
                        oembed = resp.json()
                        metadata['title'] = oembed.get('title', metadata['title'])
                        metadata['description'] = oembed.get('author_name')
                        metadata['image'] = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                        metadata['video_id'] = video_id
            except Exception as e:
                logger.warning(f"YouTube oEmbed failed: {e}")

        # For other URLs, fetch Open Graph metadata
        elif url_type in ('article', 'github', 'arxiv'):
            try:
                from bs4 import BeautifulSoup
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                resp = requests.get(url, timeout=8, headers=headers)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text[:50000], 'html.parser')

                    # Get title
                    og_title = soup.find('meta', property='og:title')
                    if og_title and og_title.get('content'):
                        metadata['title'] = og_title['content'][:100]
                    elif soup.title:
                        metadata['title'] = soup.title.string[:100] if soup.title.string else domain

                    # Get description
                    og_desc = soup.find('meta', property='og:description')
                    if og_desc and og_desc.get('content'):
                        metadata['description'] = og_desc['content'][:200]
                    else:
                        meta_desc = soup.find('meta', attrs={'name': 'description'})
                        if meta_desc and meta_desc.get('content'):
                            metadata['description'] = meta_desc['content'][:200]

                    # Get image
                    og_image = soup.find('meta', property='og:image')
                    if og_image and og_image.get('content'):
                        img_url = og_image['content']
                        # Make absolute URL if relative
                        if img_url.startswith('/'):
                            img_url = f"https://{parsed.netloc}{img_url}"
                        metadata['image'] = img_url
            except Exception as e:
                logger.warning(f"Metadata fetch failed for {url}: {e}")

        # For PDF
        elif url_type == 'pdf':
            metadata['title'] = parsed.path.split('/')[-1] or 'PDF Document'
            metadata['description'] = 'PDF Document'

        return jsonify(metadata), 200

    except Exception as e:
        logger.error(f"URL metadata error: {e}")
        return jsonify({'error': str(e)}), 500


@upload_bp.route('/uploads/<path:requested_path>')
@login_required
def serve_uploaded_file(requested_path: str):
    """Serve user-uploaded or generated files with security protections."""
    base_dir = os.path.abspath(Config.UPLOAD_FOLDER)
    safe_path = os.path.abspath(os.path.join(base_dir, requested_path))
    if not safe_path.startswith(base_dir) or not os.path.exists(safe_path):
        current_app.logger.warning(f"Attempted access to missing or invalid upload path: {requested_path}")
        abort(404)

    directory = os.path.dirname(requested_path)
    filename = os.path.basename(requested_path)
    ext = os.path.splitext(filename)[1].lower()

    # Security: Force download for potentially dangerous file types
    # These could contain scripts that execute in browser
    DANGEROUS_EXTENSIONS = {
        '.html', '.htm', '.svg', '.xml', '.xhtml',
        '.js', '.mjs', '.cjs',  # JavaScript
        '.php', '.asp', '.aspx', '.jsp',  # Server scripts
        '.sh', '.bash', '.ps1', '.bat', '.cmd',  # Shell scripts
    }

    # Safe to display inline (images, PDFs)
    INLINE_SAFE = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf'}

    if ext in DANGEROUS_EXTENSIONS:
        # Force download - prevents XSS
        response = send_from_directory(
            os.path.join(base_dir, directory),
            filename,
            as_attachment=True,
            download_name=filename
        )
        # Extra security headers
        response.headers['Content-Type'] = 'application/octet-stream'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'"
        return response
    elif ext in INLINE_SAFE:
        # Safe to display inline
        response = send_from_directory(os.path.join(base_dir, directory), filename)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    else:
        # Default: force download for unknown types
        response = send_from_directory(
            os.path.join(base_dir, directory),
            filename,
            as_attachment=True
        )
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
