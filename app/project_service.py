"""ProjectService — data-layer wrapper for users/{uid}/projects/{projectId}.

Files split between inline metadata (on the Project doc) and per-file text
docs in users/{uid}/projects/{projectId}/files/{fileId}. See spec:
docs/superpowers/specs/2026-05-22-projects-design.md
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from google.cloud import firestore as gc_firestore


MAX_FILES_PER_PROJECT = 20
PER_FILE_TEXT_CAP = 200_000  # storage cap, chars
ALLOWED_FIELDS = {'name', 'color', 'instructions', 'position'}


class ProjectService:
    class LimitExceeded(Exception):
        pass

    def __init__(self, db):
        self.db = db

    def _coll(self, uid: str):
        return self.db.collection('users').document(uid).collection('projects')

    def _files_coll(self, uid: str, project_id: str):
        return self._coll(uid).document(project_id).collection('files')

    def list(self, uid: str) -> List[Dict[str, Any]]:
        q = self._coll(uid).order_by('position', direction=gc_firestore.Query.ASCENDING)
        out = []
        for snap in q.stream():
            doc = snap.to_dict() or {}
            doc['id'] = snap.id
            out.append(_normalize(doc))
        return out

    def get(self, uid: str, project_id: str) -> Optional[Dict[str, Any]]:
        snap = self._coll(uid).document(project_id).get()
        if not snap.exists:
            return None
        doc = snap.to_dict() or {}
        doc['id'] = snap.id
        return _normalize(doc)

    def count(self, uid: str) -> int:
        return sum(1 for _ in self._coll(uid).stream())

    def create(self, uid: str, *, name: str, color: str) -> Dict[str, Any]:
        existing = self.list(uid)
        position = (existing[-1]['position'] + 1) if existing else 0
        now = datetime.utcnow()
        payload = {
            'name': name,
            'color': color,
            'instructions': '',
            'files': [],
            'position': position,
            'created_at': now,
            'updated_at': now,
        }
        ref = self._coll(uid).document()
        ref.set(payload)
        return self.get(uid, ref.id)

    def update(self, uid: str, project_id: str, fields: Dict[str, Any]) -> bool:
        cleaned = {k: v for k, v in fields.items() if k in ALLOWED_FIELDS}
        if not cleaned:
            return False
        cleaned['updated_at'] = datetime.utcnow()
        ref = self._coll(uid).document(project_id)
        if not ref.get().exists:
            return False
        ref.update(cleaned)
        return True

    def delete(self, uid: str, project_id: str) -> bool:
        ref = self._coll(uid).document(project_id)
        if not ref.get().exists:
            return False
        for snap in self._files_coll(uid, project_id).stream():
            snap.reference.delete()
        ref.delete()
        return True

    def add_file(
        self, uid: str, project_id: str, *,
        file_id: str, filename: str, mime_type: str, size_bytes: int,
        storage_path: str, extracted_text: str,
    ) -> Dict[str, Any]:
        proj = self.get(uid, project_id)
        if proj is None:
            raise ValueError('Project not found')
        if len(proj['files']) >= MAX_FILES_PER_PROJECT:
            raise self.LimitExceeded(f'Max {MAX_FILES_PER_PROJECT} files per project')

        text = extracted_text or ''
        if len(text) > PER_FILE_TEXT_CAP:
            text = text[:PER_FILE_TEXT_CAP - len('[truncated]')] + '[truncated]'

        now = datetime.utcnow()
        meta = {
            'file_id': file_id,
            'filename': filename,
            'mime_type': mime_type,
            'size_bytes': size_bytes,
            'storage_path': storage_path,
            'text_char_count': len(text),
            'uploaded_at': now,
        }

        ref = self._coll(uid).document(project_id)
        ref.update({
            'files': gc_firestore.ArrayUnion([meta]),
            'updated_at': now,
        })
        self._files_coll(uid, project_id).document(file_id).set({
            'extracted_text': text,
            'uploaded_at': now,
        })
        return meta

    def remove_file(self, uid: str, project_id: str, file_id: str) -> bool:
        proj = self.get(uid, project_id)
        if proj is None:
            return False
        new_files = [f for f in proj['files'] if f.get('file_id') != file_id]
        if len(new_files) == len(proj['files']):
            return False
        self._coll(uid).document(project_id).update({
            'files': new_files,
            'updated_at': datetime.utcnow(),
        })
        self._files_coll(uid, project_id).document(file_id).delete()
        return True

    def list_files(self, uid: str, project_id: str) -> List[Dict[str, Any]]:
        out = []
        for snap in self._files_coll(uid, project_id).stream():
            doc = snap.to_dict() or {}
            doc['file_id'] = snap.id
            out.append(_normalize(doc))
        return out


def _normalize(doc: Dict[str, Any]) -> Dict[str, Any]:
    for k in ('created_at', 'updated_at', 'uploaded_at'):
        v = doc.get(k)
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    files = doc.get('files')
    if isinstance(files, list):
        for f in files:
            v = f.get('uploaded_at')
            if isinstance(v, datetime):
                f['uploaded_at'] = v.isoformat()
    return doc
