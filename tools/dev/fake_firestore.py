"""In-memory stand-in for the Firestore client, for local UI work (tools/dev/devserver.py).

Covers what XelaSphere uses: collections/sub-collections, documents (get/set/update/delete/
create), queries (where incl. FieldFilter, order_by, limit, offset, start_after, select,
count), batches, transactions, get_all, and the transforms SERVER_TIMESTAMP, DELETE_FIELD,
Increment, ArrayUnion, ArrayRemove. Never talks to the network. Not for production.
"""
from __future__ import annotations

import copy
import datetime as _dt
import threading
import uuid

try:  # mimic the real errors so app code that catches them keeps working
    from google.api_core.exceptions import Conflict, NotFound
except Exception:  # pragma: no cover
    class NotFound(Exception):
        pass

    class Conflict(Exception):
        pass

_DELETE = object()


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def _is_sentinel(value, word):
    return type(value).__name__ == "Sentinel" and word in (getattr(value, "description", "") or "").lower()


def _transform(value, old):
    """Resolve Firestore transforms against the previous value."""
    name = type(value).__name__
    if _is_sentinel(value, "timestamp"):
        return _now()
    if _is_sentinel(value, "delete"):
        return _DELETE
    if name == "Increment":
        return (old or 0) + value.value
    if name == "ArrayUnion":
        out = list(old or [])
        for v in value.values:
            if v not in out:
                out.append(v)
        return out
    if name == "ArrayRemove":
        return [v for v in (old or []) if v not in value.values]
    if name in ("Maximum", "Minimum"):
        if old is None:
            return value.value
        return max(old, value.value) if name == "Maximum" else min(old, value.value)
    if isinstance(value, dict):
        base = old if isinstance(old, dict) else {}
        out = {}
        for k, v in value.items():
            r = _transform(v, base.get(k))
            if r is not _DELETE:
                out[k] = r
        return out
    if isinstance(value, list):
        return [_transform(v, None) for v in value]
    return copy.deepcopy(value)


def _get_path(data, dotted):
    cur = data
    for part in str(dotted).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_path(data, dotted, value):
    parts = str(dotted).split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    if value is _DELETE:
        cur.pop(parts[-1], None)
    else:
        cur[parts[-1]] = value


def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


class _AggResult:
    def __init__(self, alias, value):
        self.alias, self.value = alias, value


class _Aggregation:
    def __init__(self, query, alias):
        self._q, self._alias = query, alias or "count"

    def get(self, transaction=None, **_):
        return [[_AggResult(self._alias, len(self._q.get()))]]

    stream = get


class Snapshot:
    def __init__(self, ref, data):
        self.reference = ref
        self.id = ref.id
        self._data = copy.deepcopy(data) if data is not None else None
        self.exists = data is not None
        self.create_time = self.update_time = self.read_time = _now()

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None

    def get(self, field_path):
        return _get_path(self._data or {}, field_path)

    def __getitem__(self, key):
        return self.get(key)


class DocumentReference:
    def __init__(self, client, path):
        self._client = client
        self.path = path
        self.id = path.rsplit("/", 1)[-1]

    @property
    def parent(self):
        return CollectionReference(self._client, self.path.rsplit("/", 1)[0])

    def collection(self, name):
        return CollectionReference(self._client, f"{self.path}/{name}")

    def collections(self):
        prefix = self.path + "/"
        names = {p[len(prefix):].split("/", 1)[0] for p in self._client._docs if p.startswith(prefix)}
        return [self.collection(n) for n in sorted(names)]

    def get(self, field_paths=None, transaction=None, **_):
        with self._client._lock:
            return Snapshot(self, self._client._docs.get(self.path))

    def set(self, document_data, merge=False, **_):
        with self._client._lock:
            old = self._client._docs.get(self.path)
            if merge and old is not None:
                new = copy.deepcopy(old)
                _deep_merge(new, _transform(document_data, old))
            else:
                new = _transform(document_data, old if merge else None)
            self._client._docs[self.path] = new
        return _now()

    def create(self, document_data, **_):
        with self._client._lock:
            if self.path in self._client._docs:
                raise Conflict(f"Document already exists: {self.path}")
            self._client._docs[self.path] = _transform(document_data, None)
        return _now()

    def update(self, field_updates, **_):
        with self._client._lock:
            old = self._client._docs.get(self.path)
            if old is None:
                raise NotFound(f"No document to update: {self.path}")
            new = copy.deepcopy(old)
            for k, v in field_updates.items():
                _set_path(new, k, _transform(v, _get_path(old, k)))
            self._client._docs[self.path] = new
        return _now()

    def delete(self, **_):
        with self._client._lock:
            self._client._docs.pop(self.path, None)
        return _now()

    def __eq__(self, other):
        return isinstance(other, DocumentReference) and other.path == self.path

    def __hash__(self):
        return hash(self.path)

    def __repr__(self):
        return f"<FakeDocRef {self.path}>"


_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a is not None and a < b,
    "<=": lambda a, b: a is not None and a <= b,
    ">": lambda a, b: a is not None and a > b,
    ">=": lambda a, b: a is not None and a >= b,
    "in": lambda a, b: a in (b or []),
    "not-in": lambda a, b: a not in (b or []),
    "array_contains": lambda a, b: isinstance(a, list) and b in a,
    "array-contains": lambda a, b: isinstance(a, list) and b in a,
    "array_contains_any": lambda a, b: isinstance(a, list) and any(x in a for x in (b or [])),
    "array-contains-any": lambda a, b: isinstance(a, list) and any(x in a for x in (b or [])),
}


def _filter_fn(field=None, op=None, value=None, flt=None):
    if flt is not None:
        subs = getattr(flt, "filters", None)
        if subs is not None:  # And / Or composite
            fns = [_filter_fn(flt=s) for s in subs]
            is_or = "or" in str(getattr(flt, "operator", "")).lower() or type(flt).__name__ == "Or"
            return (lambda d: any(f(d) for f in fns)) if is_or else (lambda d: all(f(d) for f in fns))
        field, op, value = flt.field_path, flt.op_string, flt.value
    fn = _OPS.get(op, _OPS["=="])
    if field == "__name__":
        return lambda d, _v=value: fn(d.get("__name__"), getattr(_v, "id", _v))

    def check(d):
        try:
            return fn(_get_path(d, field), value)
        except TypeError:
            return False
    return check


def _sort_key(v):
    if v is None:
        return (0, 0)
    if isinstance(v, bool):
        return (1, int(v))
    if isinstance(v, (int, float)):
        return (2, v)
    if isinstance(v, _dt.datetime):
        return (3, v.timestamp() if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc).timestamp())
    if isinstance(v, str):
        return (4, v)
    return (5, str(v))


class Query:
    def __init__(self, client, path=None, group=None, filters=(), orders=(), limit=None, offset=0,
                 start_after=None, limit_to_last=False):
        self._client, self._path, self._group = client, path, group
        self._filters, self._orders = list(filters), list(orders)
        self._limit, self._offset, self._start_after, self._ltl = limit, offset, start_after, limit_to_last

    def _copy(self, **kw):
        args = dict(path=self._path, group=self._group, filters=self._filters, orders=self._orders,
                    limit=self._limit, offset=self._offset, start_after=self._start_after,
                    limit_to_last=self._ltl)
        args.update(kw)
        return Query(self._client, **args)

    def where(self, field_path=None, op_string=None, value=None, *, filter=None):
        return self._copy(filters=self._filters + [_filter_fn(field_path, op_string, value, filter)])

    def order_by(self, field_path, direction="ASCENDING"):
        return self._copy(orders=self._orders + [(field_path, str(direction).upper().endswith("DESCENDING"))])

    def limit(self, count):
        return self._copy(limit=count, limit_to_last=False)

    def limit_to_last(self, count):
        return self._copy(limit=count, limit_to_last=True)

    def offset(self, num_to_skip):
        return self._copy(offset=num_to_skip)

    def start_after(self, document_fields_or_snapshot):
        return self._copy(start_after=document_fields_or_snapshot)

    def start_at(self, document_fields_or_snapshot):
        return self._copy(start_after=document_fields_or_snapshot)

    def end_before(self, *_):
        return self

    def end_at(self, *_):
        return self

    def select(self, field_paths):
        return self

    def count(self, alias=None):
        return _Aggregation(self, alias)

    def _candidates(self):
        docs = self._client._docs
        if self._group:
            return [(p, d) for p, d in docs.items()
                    if p.count("/") % 2 == 1 and p.rsplit("/", 2)[-2] == self._group]
        prefix = self._path + "/"
        return [(p, d) for p, d in docs.items() if p.startswith(prefix) and "/" not in p[len(prefix):]]

    def get(self, transaction=None, **_):
        with self._client._lock:
            rows = []
            for p, d in self._candidates():
                probe = dict(d)
                probe["__name__"] = p.rsplit("/", 1)[-1]
                if all(f(probe) for f in self._filters):
                    rows.append((p, d))
        for field, desc in reversed(self._orders):
            rows.sort(key=lambda r, f=field: _sort_key(_get_path(r[1], f)), reverse=desc)
        if self._start_after is not None:
            sa = self._start_after
            sid = getattr(sa, "id", None)
            if sid is not None:
                ids = [p.rsplit("/", 1)[-1] for p, _ in rows]
                if sid in ids:
                    rows = rows[ids.index(sid) + 1:]
        rows = rows[self._offset:]
        if self._limit is not None:
            rows = rows[-self._limit:] if self._ltl else rows[: self._limit]
        return [Snapshot(DocumentReference(self._client, p), d) for p, d in rows]

    def stream(self, transaction=None, **_):
        return iter(self.get())


class CollectionReference(Query):
    def __init__(self, client, path):
        super().__init__(client, path=path)
        self.id = path.rsplit("/", 1)[-1]

    @property
    def parent(self):
        if "/" not in self._path:
            return None
        return DocumentReference(self._client, self._path.rsplit("/", 1)[0])

    def document(self, document_id=None):
        return DocumentReference(self._client, f"{self._path}/{document_id or uuid.uuid4().hex[:20]}")

    def add(self, document_data, document_id=None, **_):
        ref = self.document(document_id)
        ref.set(document_data)
        return _now(), ref

    def list_documents(self, page_size=None, **_):
        return [s.reference for s in self.get()]


class WriteBatch:
    def __init__(self, client):
        self._client, self._ops = client, []

    def set(self, ref, data, merge=False):
        self._ops.append(lambda: ref.set(data, merge=merge))
        return self

    def update(self, ref, data):
        self._ops.append(lambda: ref.update(data))
        return self

    def delete(self, ref, **_):
        self._ops.append(lambda: ref.delete())
        return self

    def create(self, ref, data):
        self._ops.append(lambda: ref.create(data))
        return self

    def commit(self, **_):
        with self._client._lock:
            results = [op() for op in self._ops]
        self._ops = []
        return results


class Transaction(WriteBatch):
    def get(self, ref_or_query, **_):
        if isinstance(ref_or_query, DocumentReference):
            return iter([ref_or_query.get()])
        return ref_or_query.stream()

    def get_all(self, refs, **_):
        return [r.get() for r in refs]

    # writes apply immediately: there is no concurrency to protect against here
    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)
        return self

    def update(self, ref, data):
        ref.update(data)
        return self

    def delete(self, ref, **_):
        ref.delete()
        return self

    def create(self, ref, data):
        ref.create(data)
        return self


def transactional(fn):
    """Replacement for firestore.transactional: run the function once with the fake transaction."""
    def wrapper(transaction, *args, **kwargs):
        return fn(transaction, *args, **kwargs)
    wrapper.__wrapped__ = fn
    return wrapper


class FakeClient:
    def __init__(self):
        self._docs = {}
        self._lock = threading.RLock()
        self.project = "xelasphere-dev"

    def collection(self, *path):
        return CollectionReference(self, "/".join(path))

    def document(self, *path):
        return DocumentReference(self, "/".join(path))

    def collection_group(self, collection_id):
        return Query(self, group=collection_id)

    def collections(self):
        names = {p.split("/", 1)[0] for p in self._docs}
        return [self.collection(n) for n in sorted(names)]

    def batch(self):
        return WriteBatch(self)

    def bulk_writer(self, **_):
        return WriteBatch(self)

    def transaction(self, **_):
        return Transaction(self)

    def get_all(self, references, field_paths=None, transaction=None, **_):
        return iter([r.get() for r in references])

    def close(self):
        pass
