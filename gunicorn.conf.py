"""Gunicorn configuration for XelaSphere — gthread workers.

gthread uses real OS threads (not greenlets) so it's fully compatible
with the gRPC C-extension that google-cloud-firestore uses. Each worker
handles `threads` concurrent requests; total concurrency = workers × threads.

We tried gevent; it deadlocked on Firestore gRPC calls at startup
(see commit log: "Registry: Timeout of 300s exceeded"). gthread is the
safe middle ground — much better than sync, compatible with existing code.
"""

bind = "127.0.0.1:5000"
workers = 4
worker_class = "gthread"
threads = 8                 # 4 × 8 = 32 concurrent request slots
worker_connections = 1000   # total keep-alive slots per worker
timeout = 120               # SSE stream cap — bump to 180 if cut mid-stream
keepalive = 5
max_requests = 500
max_requests_jitter = 50
