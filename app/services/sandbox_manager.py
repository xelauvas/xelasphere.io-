"""
Manages disposable Docker containers for running Python code securely.
Each user gets at most one container at a time. Containers are destroyed after use.
"""

import docker
import json
import base64
import sys
import time
import logging
import re
import threading
from dataclasses import dataclass
from typing import Optional, Dict, List, Generator

logger = logging.getLogger(__name__)

SCRIPT_TIMEOUT = 30
APP_TIMEOUT = 300
GUI_TIMEOUT = 300
IMAGE_NAME = 'xelasphere-sandbox'
JANITOR_INTERVAL = 60  # seconds between sweeps for expired containers

# Pre-installed in the Docker image
PREINSTALLED = {
    'pygame', 'numpy', 'pandas', 'matplotlib', 'scipy', 'pillow', 'pil',
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

# Every stdlib module name for the running interpreter (Python 3.10+). This is
# the real fix for "pip install curses" failing: the hand-maintained list above
# only covered ~60 of ~300 stdlib modules, so curses/tty/select/etc. leaked into
# the pip install list. Union with PREINSTALLED (which still covers third-party
# packages baked into the image).
STDLIB = set(getattr(sys, 'stdlib_module_names', ()))

# Libraries that drive a full-screen terminal UI. They need a live TTY plus
# keyboard input, which script mode (captured stdout, no stdin) can't provide,
# so they no-op and exit 0 with a blank screen. Detected up front so we can tell
# the user instead of silently running nothing.
TUI_MODULES = {'curses', '_curses', 'urwid', 'blessed', 'prompt_toolkit',
               'textual', 'npyscreen', 'asciimatics'}


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
        if self.mode == 'script':
            return SCRIPT_TIMEOUT
        return APP_TIMEOUT


def detect_mode(code: str) -> str:
    """Detect execution mode from Python source code."""
    gui_patterns = ['pygame.init', 'pygame.display', 'import tkinter', 'from tkinter', 'Tk()', 'import turtle', 'from turtle', 'turtle.Screen', 'turtle.Turtle']
    app_patterns = ['Flask(__name__)', 'app.run(', 'uvicorn.run', 'import streamlit', 'from streamlit']

    for p in gui_patterns:
        if p in code:
            return 'gui'
    for p in app_patterns:
        if p in code:
            return 'app'
    return 'script'


def detect_extra_packages(code: str, local_files: Optional[Dict[str, str]] = None) -> List[str]:
    """Detect imported packages not pre-installed in the sandbox image.
    Filters out local project imports (directories/files in the artifact)."""
    imports = set()
    for match in re.finditer(r'(?:^|\n)\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)', code):
        imports.add(match.group(1))

    # Build set of local module names from artifact files
    local_modules = set()
    if local_files:
        for path in local_files:
            # "scraper_project/items.py" → "scraper_project"
            top = path.split('/')[0].replace('.py', '')
            if top:
                local_modules.add(top.lower())

    preinstalled_lower = {s.lower() for s in PREINSTALLED}
    return [p for p in imports
            if p.lower() not in preinstalled_lower
            and p not in STDLIB
            and p.lower() not in local_modules]


def requires_interactive_terminal(code: str) -> Optional[str]:
    """Return the first TUI module imported, or None.

    Terminal-interactive programs (curses and friends) need a live TTY and
    keyboard input that script mode can't give them. We surface a clear message
    instead of spinning up a container that exits 0 with a blank screen.
    """
    for match in re.finditer(r'(?:^|\n)\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)', code):
        if match.group(1) in TUI_MODULES:
            return match.group(1)
    return None


class SandboxManager:
    def __init__(self):
        self.active: Dict[str, ContainerInfo] = {}
        self._lock = threading.Lock()
        try:
            self.client = docker.from_env()
            self.available = True
            logger.info("SandboxManager: Docker connected")
            self._start_janitor()
        except Exception as e:
            self.available = False
            logger.warning(f"SandboxManager: Docker not available: {e}")

    def _start_janitor(self):
        """Daemon thread that sweeps expired containers — without it, gui/app
        containers leak forever (their event loops never exit)."""
        def loop():
            while True:
                try:
                    time.sleep(JANITOR_INTERVAL)
                    self._sweep_orphaned()
                    self.cleanup_expired()
                except Exception as e:
                    logger.warning(f"SandboxManager janitor: {e}")
        t = threading.Thread(target=loop, name='sandbox-janitor', daemon=True)
        t.start()

    def _sweep_orphaned(self):
        """Kill containers older than their timeout that no live worker is tracking.
        Spans gunicorn workers via Docker labels."""
        try:
            now = time.time()
            for c in self.client.containers.list(
                filters={'label': ['xelasphere.sandbox=true'], 'status': 'running'}
            ):
                created = c.attrs.get('Created', '')
                # Created is ISO 8601 e.g. "2026-04-30T12:00:00.000000000Z"
                try:
                    from datetime import datetime
                    # Trim nanoseconds to microseconds for fromisoformat
                    iso = created.replace('Z', '+00:00')
                    if '.' in iso:
                        head, tail = iso.split('.', 1)
                        frac, tz = tail.split('+', 1) if '+' in tail else (tail.rstrip('Z'), '00:00')
                        iso = f"{head}.{frac[:6]}+{tz}"
                    age = now - datetime.fromisoformat(iso).timestamp()
                except Exception:
                    age = now  # unknown age → treat as old
                mode = (c.labels or {}).get('xelasphere.mode', 'script')
                limit = SCRIPT_TIMEOUT if mode == 'script' else APP_TIMEOUT
                if age > limit:
                    try:
                        c.remove(force=True)
                        logger.info(f"Sandbox janitor: removed {c.id[:12]} (mode={mode}, age={int(age)}s)")
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Sandbox janitor sweep failed: {e}")

    def _find_running_container(self, user_id: str) -> Optional[object]:
        """Find a running sandbox container for this user via Docker labels.
        Works across gunicorn workers since it queries Docker directly."""
        try:
            containers = self.client.containers.list(
                filters={
                    'label': [f'xelasphere.user={user_id}', 'xelasphere.sandbox=true'],
                    'status': 'running'
                }
            )
            if containers:
                container = containers[0]
                # Ensure it's tracked locally too. Read the actual mode from the
                # label so reuse decisions don't assume 'script'.
                mode = (container.labels or {}).get('xelasphere.mode', 'script')
                with self._lock:
                    if user_id not in self.active:
                        self.active[user_id] = ContainerInfo(
                            container_id=container.id[:12],
                            user_id=user_id,
                            mode=mode,
                            created_at=time.time(),
                            container=container,
                        )
                return container
        except Exception as e:
            logger.warning(f"Sandbox: container lookup failed: {e}")
        return None

    def _exec_in_container(self, container, files: Dict[str, str],
                           entry: str) -> Generator[dict, None, None]:
        """Execute a script in an existing running container via docker exec."""
        # Update files in container
        try:
            write_script = 'import json,os\nfiles=' + repr(files) + '\nfor p,c in files.items():\n d=os.path.dirname(p)\n if d: os.makedirs(d,exist_ok=True)\n open(p,"w").write(c)\n'
            container.exec_run(['python3', '-c', write_script], workdir='/home/sandbox')
        except Exception as e:
            logger.warning(f"Sandbox: failed to update files: {e}")

        yield {"type": "status", "text": f"Running {entry}..."}

        # Execute the script
        try:
            exit_code, output = container.exec_run(
                ['python3', '-u', entry],
                workdir='/home/sandbox',
                environment={'PYTHONUNBUFFERED': '1', 'DISPLAY': ':99', 'SDL_AUDIODRIVER': 'dummy'},
                demux=True,
            )
            if output:
                stdout_data, stderr_data = output
                if stdout_data:
                    yield {"type": "stdout", "text": stdout_data.decode('utf-8', errors='replace')}
                if stderr_data:
                    yield {"type": "stderr", "text": stderr_data.decode('utf-8', errors='replace')}
            yield {"type": "exit", "code": exit_code}
        except Exception as e:
            yield {"type": "error", "text": str(e)}

    def run_code(self, user_id: str, code: str, files: Dict[str, str],
                 entry: str = 'main.py') -> Generator[dict, None, None]:
        """
        Run Python code in a sandbox container.
        Reuses existing container if available. Yields SSE event dicts.
        """
        if not self.available:
            yield {"type": "error", "text": "Docker not available on this server"}
            return

        mode = detect_mode(code)

        # Terminal-interactive programs (curses/TUI) can't run here: script mode
        # captures stdout and has no stdin TTY, so they'd no-op and exit 0 with a
        # blank screen. Tell the user instead of pretending to run it.
        tui_lib = requires_interactive_terminal(code)
        if tui_lib:
            yield {"type": "error", "text": (
                f"'{tui_lib}' needs an interactive terminal (live keyboard + TTY), "
                f"which the Run sandbox doesn't provide. Terminal UI programs can't "
                f"run here — try a script that prints output instead."
            )}
            return

        # Reuse existing container only when both the existing container and the
        # new run are plain 'script' mode. gui/app containers run their own
        # event loop (pygame / Flask / VNC) and re-execing into them blocks
        # forever and never re-emits the vnc/app event the iframe needs.
        existing = self._find_running_container(user_id)
        if existing:
            existing_mode = (existing.labels or {}).get('xelasphere.mode', 'script')
            if existing_mode == 'script' and mode == 'script':
                yield {"type": "status", "text": "Reusing existing container..."}
                yield from self._exec_in_container(existing, files, entry)
                return
            # Mismatched / non-script mode → tear down before recreating
            self.kill(user_id)

        # Kill any tracked (but stopped) containers
        self.kill(user_id)
        extra_packages = detect_extra_packages(code, local_files=files)
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
                labels={
                    'xelasphere.sandbox': 'true',
                    'xelasphere.user': user_id,
                    'xelasphere.mode': mode,
                },
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
            yield {"type": "error", "text": f"Sandbox image '{IMAGE_NAME}' not found. Run: docker build -t {IMAGE_NAME} docker/sandbox/"}
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

        yield {"type": "status", "text": f"Container {info.container_id} started"}

        try:
            timeout = info.timeout
            start = time.time()

            for log_line in container.logs(stream=True, follow=True):
                if time.time() - start > timeout:
                    yield {"type": "error", "text": f"Timeout ({timeout}s) exceeded"}
                    break

                line = log_line.decode('utf-8', errors='replace').rstrip('\n')

                # Parse sandbox events from entrypoint.sh
                if line.startswith('SANDBOX_EVENT:'):
                    event_json = line[len('SANDBOX_EVENT:'):]
                    try:
                        event = json.loads(event_json)

                        # Resolve Docker-mapped ports
                        if event.get('type') == 'app' and event.get('port'):
                            container.reload()
                            port_bindings = container.attrs['NetworkSettings']['Ports']
                            for cp, hb in port_bindings.items():
                                if hb:
                                    info.app_port = int(hb[0]['HostPort'])
                                    event['url'] = f'/api/sandbox/proxy/{info.container_id}/'
                                    break

                        elif event.get('type') == 'vnc' and event.get('port'):
                            container.reload()
                            port_bindings = container.attrs['NetworkSettings']['Ports']
                            vnc_bind = port_bindings.get('6080/tcp')
                            if vnc_bind:
                                info.vnc_port = int(vnc_bind[0]['HostPort'])
                                event['url'] = f'/api/sandbox/vnc/{info.container_id}/'
                                event['vnc_port'] = info.vnc_port

                        yield event
                        continue
                    except json.JSONDecodeError:
                        pass

                # Regular output
                yield {"type": "stdout", "text": line + "\n"}

        except Exception as e:
            yield {"type": "error", "text": str(e)}
        # Container stays alive for subsequent runs — cleaned up by
        # kill() on next run, or cleanup_expired() on timeout

    def kill(self, user_id: str):
        """Kill and remove a user's active container (checks Docker labels too)."""
        with self._lock:
            info = self.active.pop(user_id, None)
        if info:
            try:
                info.container.remove(force=True)
                logger.info(f"Sandbox: killed {info.container_id} for {user_id}")
            except Exception as e:
                logger.warning(f"Sandbox: kill failed: {e}")
        # Also kill any containers found via Docker labels (cross-worker)
        try:
            for c in self.client.containers.list(
                all=True,
                filters={'label': [f'xelasphere.user={user_id}', 'xelasphere.sandbox=true']}
            ):
                try:
                    c.remove(force=True)
                    logger.info(f"Sandbox: killed labeled container {c.id[:12]} for {user_id}")
                except Exception:
                    pass
        except Exception:
            pass

    def _remove(self, user_id: str):
        with self._lock:
            info = self.active.pop(user_id, None)
        if info:
            try:
                info.container.remove(force=True)
            except Exception:
                pass

    def get_container(self, container_id: str) -> Optional[ContainerInfo]:
        with self._lock:
            for info in self.active.values():
                if info.container_id == container_id:
                    return info
        return None

    def cleanup_expired(self):
        now = time.time()
        to_remove = []
        with self._lock:
            for uid, info in self.active.items():
                if now - info.created_at > info.timeout:
                    to_remove.append(uid)
        for uid in to_remove:
            logger.info(f"Sandbox: cleaning expired container for {uid}")
            self.kill(uid)


# Singleton
sandbox_manager = SandboxManager()
