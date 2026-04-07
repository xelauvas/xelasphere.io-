#!/bin/bash
# Don't use set -e — background processes (Xvfb, x11vnc) may return non-zero

MODE="${MODE:-script}"
ENTRY="${ENTRY:-main.py}"

# Write user files from base64-encoded JSON
if [ -n "$FILES_JSON" ]; then
    echo "$FILES_JSON" | base64 -d | python3 -c "
import json, sys, os
files = json.load(sys.stdin)
for path, content in files.items():
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)
"
fi

# Install extra packages if any
if [ -n "$PACKAGES" ]; then
    echo "SANDBOX_EVENT:{\"type\":\"install\",\"text\":\"Installing packages...\"}"
    pip install --no-cache-dir --user $PACKAGES 2>&1 | while IFS= read -r line; do
        echo "SANDBOX_EVENT:{\"type\":\"install\",\"text\":\"$line\"}"
    done
    echo "SANDBOX_EVENT:{\"type\":\"install\",\"text\":\"Packages installed.\"}"
fi

echo "SANDBOX_EVENT:{\"type\":\"status\",\"text\":\"Running $ENTRY ($MODE mode)...\"}"

case "$MODE" in
    script)
        python3 -u "$ENTRY" 2>&1
        EXIT_CODE=$?
        echo ""
        echo "SANDBOX_EVENT:{\"type\":\"exit\",\"code\":$EXIT_CODE}"
        ;;

    app)
        python3 -u "$ENTRY" &
        APP_PID=$!

        # Wait up to 15s for a port to open
        PORT=""
        for i in $(seq 1 30); do
            PORT=$(ss -tlnp 2>/dev/null | grep -oP ':\K[0-9]{4,5}(?=\s)' | head -1 || true)
            if [ -n "$PORT" ]; then
                echo "SANDBOX_EVENT:{\"type\":\"app\",\"port\":\"$PORT\"}"
                break
            fi
            sleep 0.5
        done

        if [ -z "$PORT" ]; then
            echo "SANDBOX_EVENT:{\"type\":\"error\",\"text\":\"No port detected after 15s\"}"
            kill $APP_PID 2>/dev/null || true
            exit 1
        fi

        wait $APP_PID 2>/dev/null || true
        ;;

    gui)
        export DISPLAY=:99
        export SDL_AUDIODRIVER=dummy

        # Start virtual framebuffer
        Xvfb :99 -screen 0 800x600x24 -ac +extension GLX +render -noreset &
        XVFB_PID=$!
        sleep 1

        # Verify Xvfb started
        if ! kill -0 $XVFB_PID 2>/dev/null; then
            echo "SANDBOX_EVENT:{\"type\":\"error\",\"text\":\"Failed to start virtual display\"}"
            exit 1
        fi

        # Start VNC server on the virtual display
        x11vnc -display :99 -nopw -shared -forever -noxdamage 2>/dev/null &
        sleep 0.5

        # Start noVNC websocket bridge
        websockify --web /usr/share/novnc 6080 localhost:5900 &
        sleep 0.5

        echo "SANDBOX_EVENT:{\"type\":\"vnc\",\"port\":\"6080\"}"

        python3 -u "$ENTRY" 2>&1
        EXIT_CODE=$?
        echo ""
        echo "SANDBOX_EVENT:{\"type\":\"exit\",\"code\":$EXIT_CODE}"
        ;;
esac
