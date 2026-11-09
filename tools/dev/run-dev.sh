#!/usr/bin/env bash
# Start/stop the isolated dev instance (127.0.0.1:5056). Logs: /root/xelas.io/devdeps/devserver.log
#   run-dev.sh start|stop|restart|status
set -u
WT=/root/xelas.io/claude-ui; DEPS=/root/xelas.io/devdeps; LOG=$DEPS/devserver.log; PID=$DEPS/devserver.pid
start() {
  [ -f $PID ] && kill -0 "$(cat $PID)" 2>/dev/null && { echo "already running (pid $(cat $PID))"; return; }
  cd $WT || exit 1
  set -a; [ -f $DEPS/dev.env ] && . $DEPS/dev.env; set +a
  export OLLAMA_API_URL=http://127.0.0.1:11434/v1/chat/completions
  PYTHONPATH=$DEPS:$WT nohup /opt/xelasphere/venv/bin/python tools/dev/devserver.py --port 5056 > $LOG 2>&1 &
  echo $! > $PID
  for i in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:5056/auth/login && break; sleep 1; done
  curl -s -o /dev/null -w "dev instance: HTTP %{http_code}\n" http://127.0.0.1:5056/auth/login
}
stop() { [ -f $PID ] && kill "$(cat $PID)" 2>/dev/null; rm -f $PID; sleep 1; echo stopped; }
case "${1:-start}" in
  start) start ;; stop) stop ;; restart) stop; start ;;
  status) [ -f $PID ] && kill -0 "$(cat $PID)" 2>/dev/null && echo "running (pid $(cat $PID))" || echo "not running" ;;
esac
