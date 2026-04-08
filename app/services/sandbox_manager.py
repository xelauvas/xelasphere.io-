"""
Manages disposable Docker containers for running Python code securely.
Each user gets at most one container at a time. Containers are destroyed after use.
"""

import docker
import json
import base64
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


def detect_extra_packages(code: str) -> List[str]:
    """Detect imported packages not pre-installed in the sandbox image."""
    imports = set()
    for match in re.finditer(r'(?:^|\n)\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)', code):
        imports.add(match.group(1))
    return [p for p in imports if p.lower() not in {s.lower() for s in PREINSTALLED}]


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
        extra_packages = detect_extra_packages(code)
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
        """Kill and remove a user's active container."""
        with self._lock:
            info = self.active.pop(user_id, None)
        if info:
            try:
                info.container.remove(force=True)
                logger.info(f"Sandbox: killed {info.container_id} for {user_id}")
            except Exception as e:
                logger.warning(f"Sandbox: kill failed: {e}")

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
