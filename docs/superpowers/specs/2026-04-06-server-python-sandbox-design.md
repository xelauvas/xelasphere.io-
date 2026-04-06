# Server-Side Python Sandbox — Design Spec

**Date:** 2026-04-06
**Status:** Approved for implementation

## Problem

Pyodide (browser Python) can't run packages that need native C extensions or OS access — pygame, tkinter, flask, opencv, etc. Users get "not available in browser Python" errors. We need a secure server-side execution environment that runs any Python code in disposable Docker containers and streams results back to the browser.

## Solution

Disposable Docker containers per execution. Three modes auto-detected from the code: script (stdout), app (web server proxied to iframe), GUI (pygame/tkinter via VNC streamed to iframe). Pre-built base image with common packages. SSE streaming for real-time output.

## Execution Flow

```
Frontend: POST /api/sandbox/run {code, files, packages}
                    ↓
Backend: Create Docker container from xelasphere-sandbox image
                    ↓
Phase 1 — Install (network ON, 60s timeout)
  pip install any packages not in base image
  SSE streams install logs: {"type":"install","text":"Installing pygame..."}
                    ↓
Phase 2 — Execute (network OFF)
  Auto-detect mode from code analysis:

  SCRIPT MODE (default):
    Run python main.py, capture stdout/stderr
    SSE stream: {"type":"stdout","text":"..."} / {"type":"stderr","text":"..."}
    Timeout: 30 seconds
    Container destroyed on exit

  APP MODE (Flask/Streamlit/FastAPI detected):
    Run the app, detect listening port
    SSE stream: {"type":"app","url":"/api/sandbox/proxy/<id>/"}
    Frontend loads URL in preview iframe
    Timeout: 5 minutes idle
    Container destroyed on timeout or user stop

  GUI MODE (pygame/tkinter detected):
    Start Xvfb virtual display + noVNC + websockify
    Run the Python script
    SSE stream: {"type":"vnc","url":"/api/sandbox/vnc/<id>/"}
    Frontend loads noVNC client in preview iframe
    Timeout: 5 minutes idle
    Container destroyed on timeout or user stop
                    ↓
Cleanup: Container removed, volumes deleted, port freed
```

## Components

### 1. Base Docker Image: `xelasphere-sandbox`

**Dockerfile:** `docker/sandbox/Dockerfile`

```dockerfile
FROM python:3.11-slim

# System deps for pygame, GUI, VNC
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11-utils libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev \
    libsdl2-ttf-dev libfreetype6-dev libportmidi-dev \
    novnc websockify \
    && rm -rf /var/lib/apt/lists/*

# Pre-install common Python packages
RUN pip install --no-cache-dir \
    pygame numpy pandas matplotlib scipy pillow \
    flask fastapi uvicorn streamlit requests \
    beautifulsoup4 rich sympy networkx scikit-learn \
    opencv-python-headless

# Non-root user
RUN useradd -m -s /bin/bash sandbox
USER sandbox
WORKDIR /home/sandbox

# Entrypoint script handles mode detection and execution
COPY entrypoint.sh /entrypoint.sh
```

**Entrypoint script:** `docker/sandbox/entrypoint.sh`

Handles:
1. Write user files to /home/sandbox/
2. pip install extra packages (if any) with 60s timeout
3. Detect mode from code
4. Script: `python main.py` with stdout/stderr piped to SSE collector
5. App: run app, wait for port, report URL
6. GUI: start `Xvfb :99`, set `DISPLAY=:99`, start `websockify --web /usr/share/novnc 6080 localhost:5900`, run script

### 2. Flask Blueprint: `app/blueprints/sandbox_routes.py`

**Endpoints:**

`POST /api/sandbox/run`
- Body: `{code: str, files: [{path, content}], packages: [str]}`
- Returns: SSE stream (text/event-stream)
- Auth: requires logged-in user
- Rate limit: 1 concurrent container per user

SSE event types:
```json
{"type": "status", "text": "Creating container..."}
{"type": "install", "text": "Installing pygame..."}
{"type": "stdout", "text": "Hello world\n"}
{"type": "stderr", "text": "Traceback...\n"}
{"type": "app", "url": "/api/sandbox/proxy/abc123/"}
{"type": "vnc", "url": "/api/sandbox/vnc/abc123/"}
{"type": "exit", "code": 0}
{"type": "error", "text": "Timeout exceeded"}
```

`POST /api/sandbox/stop`
- Body: `{container_id: str}`
- Kills and removes the container

`GET /api/sandbox/proxy/<container_id>/<path>`
- Reverse proxy HTTP requests to the app running inside the container
- Only accessible by the user who started the container

`GET /api/sandbox/vnc/<container_id>`
- WebSocket proxy to noVNC inside the container
- Only accessible by the user who started the container

### 3. Container Manager: `app/services/sandbox_manager.py`

**Class: `SandboxManager`**

```python
class SandboxManager:
    def __init__(self):
        self.active_containers = {}  # user_id -> ContainerInfo
        self.docker_client = docker.from_env()

    async def create_container(self, user_id, code, files, packages):
        # Enforce 1 container per user
        if user_id in self.active_containers:
            self.kill_container(user_id)

        # Detect execution mode
        mode = detect_mode(code)

        # Create container with limits
        container = self.docker_client.containers.run(
            'xelasphere-sandbox',
            detach=True,
            mem_limit='256m',
            cpu_quota=50000,  # 0.5 CPU
            storage_opt={'size': '100m'},
            network_mode='bridge',  # ON during install
            user='sandbox',
            read_only=False,
            tmpfs={'/tmp': 'size=50m'},
            environment={'MODE': mode, 'DISPLAY': ':99'},
            ports={} if mode == 'script' else {'8080/tcp': None, '6080/tcp': None},
        )

        # Write files into container
        # pip install phase (with network)
        # Disconnect network
        # Execute code
        # Stream output

    def kill_container(self, user_id):
        info = self.active_containers.pop(user_id, None)
        if info:
            info.container.remove(force=True)

    def cleanup_expired(self):
        # Called periodically — kills containers past their timeout
```

**Mode Detection:**
```python
def detect_mode(code: str) -> str:
    if any(p in code for p in ['pygame.init', 'pygame.display', 'tkinter', 'Tk()']):
        return 'gui'
    if any(p in code for p in ['Flask(__name__)', 'app.run(', 'uvicorn.run', 'streamlit']):
        return 'app'
    return 'script'
```

### 4. Frontend: Updated Python Renderer

**Preview iframe behavior per mode:**

**Script mode:**
Same terminal UI. But when Pyodide detects unsupported packages, shows a "Run on Server" button. Clicking it calls `/api/sandbox/run` and streams output into the terminal via EventSource.

**App mode:**
SSE returns `{"type":"app","url":"..."}`. Frontend sets `iframe.src = url` to load the running Flask/Streamlit app directly in the preview panel.

**GUI mode:**
SSE returns `{"type":"vnc","url":"..."}`. Frontend loads noVNC client in the iframe, connecting to the container's virtual display. User can interact with mouse/keyboard.

**Fallback logic:**
1. First try Pyodide (instant, no server load)
2. If Pyodide fails (unsupported package), offer "Run on Server"
3. Server execution streams results back

### 5. Nginx Configuration

Add to existing nginx config:
```nginx
# Sandbox proxy
location /api/sandbox/proxy/ {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_read_timeout 300s;
}

# VNC WebSocket proxy
location /api/sandbox/vnc/ {
    proxy_pass http://127.0.0.1:5000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 300s;
}
```

## Security Measures

1. **Filesystem isolation** — Docker volume, no host mounts
2. **Network disabled during execution** — only ON during pip install, then disconnected
3. **Non-root user** — `sandbox` user inside container, no sudo
4. **Resource limits** — 256MB RAM, 0.5 CPU, 100MB disk, 30s/5min timeout
5. **1 container per user** — prevents resource exhaustion
6. **Container destroyed after use** — nothing persists between runs
7. **No host Docker socket** — container can't escape to host
8. **Auth required** — only logged-in users can run code
9. **Rate limiting** — max 10 runs per minute per user

## Container Lifecycle

```
Created → Installing → Running → [Completed | Timeout | Killed]
                                         ↓
                                   Destroyed (always)
```

Every state transition is logged. Cleanup runs every 60 seconds to catch orphans.

## Files to Create

1. `docker/sandbox/Dockerfile` — base image
2. `docker/sandbox/entrypoint.sh` — container entrypoint
3. `app/services/sandbox_manager.py` — container lifecycle management
4. `app/blueprints/sandbox_routes.py` — API endpoints
5. `app/static/js/artifactPanel.js` — update Python renderer (add "Run on Server" fallback)

## Files to Modify

1. `app/__init__.py` — register sandbox blueprint
2. `nginx config` — add proxy rules for sandbox
3. `app/static/js/artifactPanel.js` — update Python renderer frontend

## What Stays the Same

- Pyodide for simple scripts (no server load)
- All existing artifact renderers untouched
- Chat streaming, patch system, all other features unchanged
