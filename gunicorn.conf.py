"""Gunicorn configuration for XelaSphere."""
import os

bind = "127.0.0.1:5000"
workers = 4
timeout = 120  # Monitor SSE streams — bump to 180 if streams get cut mid-response
max_requests = 500
max_requests_jitter = 50


def post_fork(server, worker):
    """Stagger worker startup to prevent CPU stampede on restart."""
    import time
    delay = (worker.age % 4) * 3  # 0s, 3s, 6s, 9s
    if delay:
        server.log.info(f"Worker {worker.pid} staggering startup by {delay}s")
        time.sleep(delay)
