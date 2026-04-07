# Server-Side Python Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run any Python code securely in disposable Docker containers with live output streaming to the browser — supporting scripts, web apps, and GUI apps (pygame).

**Architecture:** Pre-built Docker image with common packages. Flask blueprint with SSE streaming. Three execution modes auto-detected: script (stdout), app (HTTP proxy), GUI (VNC). Container destroyed after every run.

**Tech Stack:** Docker, Flask SSE, docker-py, nginx WebSocket proxy, Xvfb + noVNC, Pyodide fallback

**Spec:** `docs/superpowers/specs/2026-04-06-server-python-sandbox-design.md`

---

### Task 1: Create the sandbox Docker image

**Files:**
- Create: `docker/sandbox/Dockerfile`
- Create: `docker/sandbox/entrypoint.sh`

- [ ] **Step 1: Create docker directory**

```bash
mkdir -p docker/sandbox
```

- [ ] **Step 2: Create the Dockerfile**

Create `docker/sandbox/Dockerfile`:

```dockerfile
FROM python:3.11-slim

# System deps for pygame, GUI, VNC
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11-utils xdotool \
    libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev \
    libsdl2-ttf-dev libfreetype6-dev libportmidi-dev \
    novnc websockify x11vnc \
    && rm -rf /var/lib/apt/lists/*

# Pre-install common Python packages
RUN pip install --no-cache-dir \
    pygame numpy pandas matplotlib scipy pillow \
    flask fastapi uvicorn[standard] streamlit requests \
    beautifulsoup4 rich sympy networkx scikit-learn \
    opencv-python-headless seaborn plotly dash

# Non-root user
RUN useradd -m -s /bin/bash sandbox

# Entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER sandbox
WORKDIR /home/sandbox

ENTRYPOINT ["/entrypoint.sh"]
```

- [ ] **Step 3: Create the entrypoint script**

Create `docker/sandbox/entrypoint.sh`:

```bash
#!/bin/bash
set -e

# MODE is set via environment: script, app, gui
# FILES_JSON is set via environment: base64-encoded JSON of {path: content}
# PACKAGES is set via environment: comma-separated extra packages
# ENTRY is set via environment: entrypoint filename

MODE="${MODE:-script}"
ENTRY="${ENTRY:-main.py}"

# Write user files
if [ -n "$FILES_JSON" ]; then
    echo "$FILES_JSON" | base64 -d | python3 -c "
import json, sys, os
files = json.load(sys.stdin)
for path, content in files.items():
    os.makedirs(os.path.dirname(path) if '/' in path else '.', exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)
"
fi

# Install extra packages if any
if [ -n "$PACKAGES" ]; then
    echo "SANDBOX_EVENT:{\"type\":\"install\",\"text\":\"Installing packages...\"}"
    pip install --no-cache-dir --user $PACKAGES 2>&1 | while read line; do
        echo "SANDBOX_EVENT:{\"type\":\"install\",\"text\":\"$line\"}"
    done
    echo "SANDBOX_EVENT:{\"type\":\"install\",\"text\":\"Packages installed.\"}"
fi

echo "SANDBOX_EVENT:{\"type\":\"status\",\"text\":\"Running $ENTRY ($MODE mode)...\"}"

case "$MODE" in
    script)
        # Run script, prefix stdout/stderr for parsing
        python3 -u "$ENTRY" 2>&1
        EXIT_CODE=$?
        echo ""
        echo "SANDBOX_EVENT:{\"type\":\"exit\",\"code\":$EXIT_CODE}"
        ;;

    app)
        # Run the app in background, wait for port
        python3 -u "$ENTRY" &
        APP_PID=$!

        # Wait up to 15s for a port to open
        for i in $(seq 1 30); do
            PORT=$(ss -tlnp 2>/dev/null | grep "python" | head -1 | grep -oP ':\K[0-9]+(?=\s)' || true)
            if [ -n "$PORT" ]; then
                echo "SANDBOX_EVENT:{\"type\":\"app\",\"port\":\"$PORT\"}"
                break
            fi
            sleep 0.5
        done

        if [ -z "$PORT" ]; then
            echo "SANDBOX_EVENT:{\"type\":\"error\",\"text\":\"No port detected after 15s\"}"
            kill $APP_PID 2>/dev/null
            exit 1
        fi

        # Keep alive until killed
        wait $APP_PID
        ;;

    gui)
        # Start virtual display
        Xvfb :99 -screen 0 800x600x24 &
        sleep 0.5
        export DISPLAY=:99

        # Start VNC server
        x11vnc -display :99 -nopw -listen 0.0.0.0 -port 5900 -shared -forever -bg

        # Start noVNC websocket bridge
        websockify --web /usr/share/novnc 6080 localhost:5900 &
        sleep 0.5

        echo "SANDBOX_EVENT:{\"type\":\"vnc\",\"port\":\"6080\"}"

        # Run the Python script
        python3 -u "$ENTRY" 2>&1
        EXIT_CODE=$?
        echo ""
        echo "SANDBOX_EVENT:{\"type\":\"exit\",\"code\":$EXIT_CODE}"
        ;;
esac
```

- [ ] **Step 4: Build the image**

```bash
cd docker/sandbox
docker build -t xelasphere-sandbox .
```

This takes ~5 minutes (downloads pygame, numpy, etc). Only needs to be done once.

- [ ] **Step 5: Test the image manually**

```bash
# Test script mode
echo 'print("Hello from sandbox!")' > /tmp/test.py
docker run --rm -v /tmp/test.py:/home/sandbox/main.py xelasphere-sandbox
# Should output: SANDBOX_EVENT:{"type":"status",...} then "Hello from sandbox!" then SANDBOX_EVENT:{"type":"exit","code":0}
```

- [ ] **Step 6: Commit**

```bash
git add docker/sandbox/
git commit -m "feat(sandbox): create Docker image with Python + pygame + VNC"
```

---

### Task 2: Create the SandboxManager service

**Files:**
- Create: `app/services/sandbox_manager.py`

- [ ] **Step 1: Create the sandbox manager**

Create `app/services/sandbox_manager.py`:

```python
"""
Manages disposable Docker containers for running Python code securely.
Each user gets at most one container at a time. Containers are destroyed after use.
"""

import docker
import json
import base64
import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Generator

logger = logging.getLogger(__name__)

SCRIPT_TIMEOUT = 30
APP_TIMEOUT = 300
GUI_TIMEOUT = 300
IMAGE_NAME = 'xelasphere-sandbox'


@dataclass
class ContainerInfo:
    container_id: str
    user_id: str
    mode: str
    created_at: float
    container: object
    app_port: Optional[int] = None
    vnc_port: Optional[int] = None

    @property
    def timeout(self):
        return SCRIPT_TIMEOUT if self.mode == 'script' else APP_TIMEOUT


def detect_mode(code: str) -> str:
    """Detect execution mode from Python source code."""
    gui_patterns = ['pygame.init', 'pygame.display', 'import tkinter', 'from tkinter', 'Tk()']
    app_patterns = ['Flask(__name__)', 'app.run(', 'uvicorn.run', 'import streamlit', 'from streamlit']

    for p in gui_patterns:
        if p in code:
            return 'gui'
    for p in app_patterns:
        if p in code:
            return 'app'
    return 'script'


def detect_packages(code: str) -> List[str]:
    """Detect imported packages that might need pip install."""
    import re
    imports = set()
    for match in re.finditer(r'(?:^|\n)\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)', code):
        imports.add(match.group(1))

    # Pre-installed in the Docker image — skip these
    preinstalled = {
        'pygame', 'numpy', 'pandas', 'matplotlib', 'scipy', 'pillow', 'PIL',
        'flask', 'fastapi', 'uvicorn', 'streamlit', 'requests',
        'bs4', 'beautifulsoup4', 'rich', 'sympy', 'networkx', 'sklearn',
        'cv2', 'seaborn', 'plotly', 'dash',
        # stdlib
        'sys', 'os', 'io', 'json', 'math', 'random', 're', 'collections',
        'itertools', 'functools', 'datetime', 'time', 'string', 'pathlib',
        'typing', 'abc', 'copy', 'hashlib', 'base64', 'struct', 'csv',
        'textwrap', 'enum', 'dataclasses', 'argparse', 'logging',
        'unittest', 'http', 'urllib', 'socket', 'threading', 'queue',
        'heapq', 'bisect', 'array', 'contextlib', 'operator', 'decimal',
        'fractions', 'statistics', 'secrets', 'uuid', 'pprint', 'shutil',
        'glob', 'fnmatch', 'tempfile', 'pickle', 'shelve', 'sqlite3',
        'gzip', 'zipfile', 'tarfile', 'configparser', 'xml', 'html',
        'email', 'mimetypes', 'webbrowser', 'turtle', 'tkinter',
    }
    return [p for p in imports if p.lower() not in {s.lower() for s in preinstalled}]


class SandboxManager:
    def __init__(self):
        self.active: Dict[str, ContainerInfo] = {}
        self._lock = threading.Lock()
        try:
            self.client = docker.from_env()
            self.available = True
            logger.info("SandboxManager: Docker connected")
        except Exception as e:
            self.available = False
            logger.warning(f"SandboxManager: Docker not available: {e}")

    def run_code(self, user_id: str, code: str, files: Dict[str, str],
                 entry: str = 'main.py') -> Generator[dict, None, None]:
        """
        Run Python code in a sandbox container.
        Yields SSE event dicts: {"type": "...", "text": "..."}
        """
        if not self.available:
            yield {"type": "error", "text": "Docker not available on this server"}
            return

        # Kill existing container for this user
        self.kill(user_id)

        mode = detect_mode(code)
        extra_packages = detect_packages(code)
        packages_str = ' '.join(extra_packages) if extra_packages else ''

        yield {"type": "status", "text": f"Creating container ({mode} mode)..."}

        # Encode files as base64 JSON
        files_json = base64.b64encode(json.dumps(files).encode()).decode()

        # Port mappings for app/gui modes
        ports = {}
        if mode == 'app':
            ports = {'8080/tcp': None, '5000/tcp': None, '3000/tcp': None, '8000/tcp': None}
        elif mode == 'gui':
            ports = {'6080/tcp': None}

        try:
            container = self.client.containers.run(
                IMAGE_NAME,
                detach=True,
                mem_limit='256m',
                nano_cpus=500000000,  # 0.5 CPU
                network_mode='bridge',
                user='sandbox',
                environment={
                    'MODE': mode,
                    'ENTRY': entry,
                    'FILES_JSON': files_json,
                    'PACKAGES': packages_str,
                },
                ports=ports,
                remove=False,
                stdout=True,
                stderr=True,
            )
        except docker.errors.ImageNotFound:
            yield {"type": "error", "text": f"Sandbox image '{IMAGE_NAME}' not found. Build it with: docker build -t {IMAGE_NAME} docker/sandbox/"}
            return
        except Exception as e:
            yield {"type": "error", "text": f"Failed to create container: {e}"}
            return

        info = ContainerInfo(
            container_id=container.id[:12],
            user_id=user_id,
            mode=mode,
            created_at=time.time(),
            container=container,
        )

        with self._lock:
            self.active[user_id] = info

        yield {"type": "status", "text": f"Container started ({info.container_id})"}

        try:
            # Stream container logs
            timeout = info.timeout
            start = time.time()

            for log_line in container.logs(stream=True, follow=True):
                if time.time() - start > timeout:
                    yield {"type": "error", "text": f"Timeout ({timeout}s) exceeded"}
                    break

                line = log_line.decode('utf-8', errors='replace').rstrip('\n')

                # Parse sandbox events
                if line.startswith('SANDBOX_EVENT:'):
                    event_json = line[len('SANDBOX_EVENT:'):]
                    try:
                        event = json.loads(event_json)
                        # Resolve Docker-mapped ports for app/vnc
                        if event.get('type') == 'app' and event.get('port'):
                            container.reload()
                            port_bindings = container.attrs['NetworkSettings']['Ports']
                            for container_port, host_bindings in port_bindings.items():
                                if host_bindings:
                                    info.app_port = int(host_bindings[0]['HostPort'])
                                    event['url'] = f'/api/sandbox/proxy/{info.container_id}/'
                                    break
                        elif event.get('type') == 'vnc' and event.get('port'):
                            container.reload()
                            port_bindings = container.attrs['NetworkSettings']['Ports']
                            vnc_binding = port_bindings.get('6080/tcp')
                            if vnc_binding:
                                info.vnc_port = int(vnc_binding[0]['HostPort'])
                                event['url'] = f'/api/sandbox/vnc/{info.container_id}/'

                        yield event
                        continue
                    except json.JSONDecodeError:
                        pass

                # Regular stdout/stderr
                yield {"type": "stdout", "text": line + "\n"}

        except Exception as e:
            yield {"type": "error", "text": str(e)}
        finally:
            # Cleanup for script mode (app/gui stay alive)
            if mode == 'script':
                self._remove_container(user_id)

    def kill(self, user_id: str):
        """Kill and remove a user's active container."""
        with self._lock:
            info = self.active.pop(user_id, None)
        if info:
            try:
                info.container.remove(force=True)
                logger.info(f"Sandbox: killed container {info.container_id} for {user_id}")
            except Exception as e:
                logger.warning(f"Sandbox: failed to kill container: {e}")

    def _remove_container(self, user_id: str):
        """Remove container after execution completes."""
        with self._lock:
            info = self.active.pop(user_id, None)
        if info:
            try:
                info.container.remove(force=True)
            except Exception:
                pass

    def get_container(self, container_id: str) -> Optional[ContainerInfo]:
        """Get container info by short ID."""
        with self._lock:
            for info in self.active.values():
                if info.container_id == container_id:
                    return info
        return None

    def cleanup_expired(self):
        """Remove containers that exceeded their timeout."""
        now = time.time()
        to_remove = []
        with self._lock:
            for uid, info in self.active.items():
                if now - info.created_at > info.timeout:
                    to_remove.append(uid)

        for uid in to_remove:
            logger.info(f"Sandbox: cleaning up expired container for {uid}")
            self.kill(uid)


# Singleton
sandbox_manager = SandboxManager()
```

- [ ] **Step 2: Commit**

```bash
git add app/services/sandbox_manager.py
git commit -m "feat(sandbox): add SandboxManager for Docker container lifecycle"
```

---

### Task 3: Create the sandbox Flask blueprint

**Files:**
- Create: `app/blueprints/sandbox_routes.py`
- Modify: `app/__init__.py`

- [ ] **Step 1: Create the blueprint**

Create `app/blueprints/sandbox_routes.py`:

```python
"""
API endpoints for server-side Python sandbox execution.
SSE streaming of container output to the browser.
"""

import json
import logging
import requests as http_requests
from flask import Blueprint, request, Response, stream_with_context, session, jsonify

from app.services.sandbox_manager import sandbox_manager

logger = logging.getLogger(__name__)

sandbox_bp = Blueprint('sandbox', __name__)


@sandbox_bp.route('/api/sandbox/run', methods=['POST'])
def sandbox_run():
    """Start a sandboxed Python execution. Returns SSE stream."""
    user_id = session.get('user_id')
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
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401

    sandbox_manager.kill(user_id)
    return jsonify({"ok": True})


@sandbox_bp.route('/api/sandbox/proxy/<container_id>/', defaults={'path': ''})
@sandbox_bp.route('/api/sandbox/proxy/<container_id>/<path:path>')
def sandbox_proxy(container_id, path):
    """Reverse proxy to a running app container."""
    user_id = session.get('user_id')
    if not user_id:
        return "Unauthorized", 401

    info = sandbox_manager.get_container(container_id)
    if not info or info.user_id != user_id:
        return "Container not found", 404

    if not info.app_port:
        return "App port not ready", 503

    # Proxy the request to the container
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
        return Response(
            resp.content,
            status=resp.status_code,
            headers=dict(resp.headers),
        )
    except Exception as e:
        return f"Proxy error: {e}", 502
```

- [ ] **Step 2: Register the blueprint in app/__init__.py**

Find the blueprint registration block (around line 175-184) in `app/__init__.py` and add after the last `register_blueprint`:

```python
from app.blueprints.sandbox_routes import sandbox_bp
app.register_blueprint(sandbox_bp)
```

- [ ] **Step 3: Commit**

```bash
git add app/blueprints/sandbox_routes.py app/__init__.py
git commit -m "feat(sandbox): add Flask blueprint with SSE streaming + proxy"
```

---

### Task 4: Update nginx for sandbox proxy and WebSocket

**Files:**
- Modify: `/etc/nginx/sites-enabled/xelasphere`

- [ ] **Step 1: Add sandbox proxy rules to nginx**

Add before the existing `location / {` block in `/etc/nginx/sites-enabled/xelasphere`:

```nginx
    # Sandbox SSE streaming (long timeout)
    location /api/sandbox/run {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
        proxy_buffering off;
        proxy_cache off;
    }

    # Sandbox app proxy
    location /api/sandbox/proxy/ {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }

    # Sandbox VNC WebSocket proxy
    location /api/sandbox/vnc/ {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 300s;
    }
```

- [ ] **Step 2: Test and reload nginx**

```bash
sudo nginx -t && sudo systemctl reload nginx
```

- [ ] **Step 3: Commit nginx config note**

No git commit needed (nginx config is outside the repo), but note the change was made.

---

### Task 5: Update frontend Python renderer with "Run on Server" fallback

**Files:**
- Modify: `app/static/js/artifactPanel.js` (Python renderer section)

- [ ] **Step 1: Update the Python renderer to add server fallback**

In the Python renderer's iframe `srcdoc`, after the "Cannot run" message for unsupported packages, add a "Run on Server" button. When Pyodide blocks execution, show a button that calls `/api/sandbox/run` instead.

Find the line in the Python renderer that says `appendOutput('Cannot run:` and replace the cannot-run block with code that shows a "Run on Server" button and handles server execution via SSE.

Add this function inside the iframe `<script>`:

```javascript
async function runOnServer() {
    var output = document.getElementById('output');
    output.innerHTML = '';
    var code = JSON.parse(document.getElementById('code-data').textContent);
    var files = JSON.parse(document.getElementById('files-data').textContent);
    var fileList = Object.keys(files).map(function(p) { return {path: p, content: files[p]}; });

    appendOutput('Connecting to server...\\n', 'repl-prompt');

    try {
        var resp = await fetch('/api/sandbox/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCSRF()},
            body: JSON.stringify({code: code, files: fileList, entry: ENTRY_NAME})
        });

        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buffer = '';

        while (true) {
            var chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, {stream: true});
            var lines = buffer.split('\\n\\n');
            buffer = lines.pop();
            for (var line of lines) {
                if (!line.startsWith('data: ')) continue;
                var event = JSON.parse(line.slice(6));
                if (event.type === 'stdout') appendOutput(event.text, 'stdout');
                else if (event.type === 'stderr') appendOutput(event.text, 'stderr');
                else if (event.type === 'install') appendOutput(event.text + '\\n', 'repl-prompt');
                else if (event.type === 'status') appendOutput(event.text + '\\n', 'repl-prompt');
                else if (event.type === 'exit') appendOutput('\\nProcess exited with code ' + event.code + '\\n', 'repl-prompt');
                else if (event.type === 'error') appendOutput('Error: ' + event.text + '\\n', 'stderr');
                else if (event.type === 'app') {
                    appendOutput('App running at: ' + event.url + '\\n', 'repl-prompt');
                    // Load app in parent iframe
                    window.parent.postMessage({type: 'sandbox-app', url: event.url}, '*');
                }
                else if (event.type === 'vnc') {
                    appendOutput('GUI running via VNC\\n', 'repl-prompt');
                    window.parent.postMessage({type: 'sandbox-vnc', url: event.url}, '*');
                }
            }
        }
    } catch (err) {
        appendOutput('Server error: ' + err.message + '\\n', 'stderr');
    }
}

function getCSRF() {
    var m = document.cookie.match(/csrf_token=([^;]+)/);
    return m ? m[1] : '';
}
```

Update the blocked packages handler to show "Run on Server" button:

Replace the `return false` in `loadPackagesFor` when unsupported packages are found with:

```javascript
    if (blocked.length > 0) {
        appendOutput('This script needs ' + blocked.join(', ') + ' which requires a server runtime.\\n', 'stderr');
        var btn = document.createElement('button');
        btn.className = 'tool-btn';
        btn.style.margin = '8px 0';
        btn.textContent = '\\u25b6 Run on Server';
        btn.onclick = function() { btn.remove(); runOnServer(); };
        document.getElementById('output').appendChild(btn);
        return false;
    }
```

- [ ] **Step 2: Add ENTRY_NAME variable to the iframe template**

Near the top of the iframe `<script>`, after the existing variable declarations, add:

```javascript
var ENTRY_NAME = '${entryName.replace(/'/g, "\\'")}';
```

- [ ] **Step 3: Handle sandbox messages from iframe in artifactPanel.js**

In the main `artifactPanel.js` (not inside the iframe), add a message listener for sandbox-app and sandbox-vnc events. Add this in the constructor's `bindEvents()`:

```javascript
window.addEventListener('message', (e) => {
    if (e.data?.type === 'sandbox-app' && e.data.url) {
        // Load the running app directly in the preview iframe
        if (this.previewFrame) {
            this.previewFrame.src = e.data.url;
            this.activeTab = 'preview';
            this.switchTab('preview');
        }
    } else if (e.data?.type === 'sandbox-vnc' && e.data.url) {
        // Load noVNC in the preview iframe
        if (this.previewFrame) {
            this.previewFrame.src = e.data.url + 'vnc.html?autoconnect=true';
            this.activeTab = 'preview';
            this.switchTab('preview');
        }
    }
});
```

- [ ] **Step 4: Commit**

```bash
git add app/static/js/artifactPanel.js
git commit -m "feat(sandbox): add Run on Server fallback in Python renderer"
```

---

### Task 6: Build Docker image and end-to-end test

- [ ] **Step 1: Build the sandbox image**

```bash
cd /root/xelas.io/xelasphere.io-/docker/sandbox
docker build -t xelasphere-sandbox .
```

- [ ] **Step 2: Restart XelaSphere**

```bash
sudo systemctl restart xelasphere
```

- [ ] **Step 3: Test script mode**

Open a new chat, ask: "write a python script that prints the first 20 fibonacci numbers"
- Should run in Pyodide first (instant)
- Verify output appears in terminal

- [ ] **Step 4: Test server fallback with pygame**

Ask: "write a snake game in python with pygame"
- Should show "This script needs pygame which requires a server runtime"
- Click "Run on Server"
- Should show container creation, then game output

- [ ] **Step 5: Test Flask app mode**

Ask: "create a simple Flask web app with a homepage that shows Hello World"
- Should detect app mode
- Click "Run on Server"
- Should show "App running at: /api/sandbox/proxy/..."
- Preview iframe should load the running Flask app

- [ ] **Step 6: Test stop**

While a container is running, close the artifact panel or navigate away.
Verify the container gets cleaned up (check `docker ps`).

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "feat(sandbox): complete server-side Python execution with Docker"
```
