import fcntl
import logging
import os
import pty
import select
import signal
import struct
import subprocess
import threading
import time
import termios
import uuid

from flask import jsonify, request

from web_app.app import csrf
from web_app.config import ConfigManager
from web_app.helpers import limiter
from web_app.logging_utils import log_event


_sessions: dict[str, "TerminalSession"] = {}
_sessions_lock = threading.Lock()


class TerminalSession:
    def __init__(self, sid: str, shell: str, buffer_cap: int):
        self.sid = sid
        self.buffer_cap = buffer_cap
        self.buffer = bytearray()
        self.dropped = 0
        self.lock = threading.Lock()
        self.last_active = time.time()
        self.alive = True

        master_fd, slave_fd = pty.openpty()
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        except OSError:
            pass

        env = {**os.environ, 'TERM': 'xterm-256color'}
        self.process = subprocess.Popen(
            [shell, '-l'],
            preexec_fn=os.setsid,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            env=env,
            close_fds=True,
        )
        os.close(slave_fd)

        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.master_fd = master_fd

        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    def _read_loop(self):
        chunk_size = ConfigManager().dev.terminal_read_chunk
        while self.alive:
            try:
                ready, _, _ = select.select([self.master_fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not ready:
                if self.process.poll() is not None:
                    break
                continue
            try:
                data = os.read(self.master_fd, chunk_size)
            except OSError:
                break
            if not data:
                break
            with self.lock:
                self.buffer.extend(data)
                excess = len(self.buffer) - self.buffer_cap
                if excess > 0:
                    del self.buffer[:excess]
                    self.dropped += excess
        self.alive = False

    def write(self, data: bytes):
        try:
            os.write(self.master_fd, data)
            self.last_active = time.time()
        except OSError:
            self.alive = False

    def read_since(self, since: int) -> tuple[bytes, int]:
        with self.lock:
            end = self.dropped + len(self.buffer)
            start = max(since, self.dropped)
            offset = start - self.dropped
            return bytes(self.buffer[offset:]), end

    def resize(self, rows: int, cols: int):
        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ,
                        struct.pack('HHHH', rows, cols, 0, 0))
        except OSError:
            pass

    def close(self):
        self.alive = False
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.process.terminate()
        except Exception:
            pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass


def _reap_idle_sessions():
    now = time.time()
    timeout = ConfigManager().dev.terminal_idle_timeout_s
    stale = [sid for sid, session in _sessions.items()
             if not session.alive or (now - session.last_active) > timeout]
    for sid in stale:
        session = _sessions.pop(sid, None)
        if session:
            session.close()


def _get_session(sid: str | None) -> TerminalSession | None:
    if not sid:
        return None
    return _sessions.get(sid)


def register_terminal_routes(dev_api):
    @dev_api.route('/terminal/start', methods=['POST'])
    @csrf.exempt
    @limiter.exempt
    def terminal_start():
        config = ConfigManager()
        with _sessions_lock:
            _reap_idle_sessions()
            if len(_sessions) >= config.dev.terminal_max_sessions:
                log_event(
                    "dev",
                    "terminal.start_rejected",
                    level=logging.WARNING,
                    reason="session_limit",
                )
                return jsonify({'error': 'session limit reached'}), 429
            sid = uuid.uuid4().hex
            sess = TerminalSession(sid, config.dev.terminal_shell, config.dev.terminal_buffer_bytes)
            _sessions[sid] = sess
        log_event("dev", "terminal.started", session_id=sid)
        return jsonify({'sid': sid})

    @dev_api.route('/terminal/output', methods=['GET'])
    @limiter.exempt
    def terminal_output():
        sess = _get_session(request.args.get('sid'))
        if not sess:
            return jsonify({'error': 'no session'}), 404
        since = request.args.get('since', 0, type=int)
        data, total = sess.read_since(since)
        return jsonify({
            'data': data.decode('utf-8', errors='replace'),
            'total': total,
            'alive': sess.alive,
        })

    @dev_api.route('/terminal/input', methods=['POST'])
    @csrf.exempt
    @limiter.exempt
    def terminal_input():
        sess = _get_session(request.args.get('sid'))
        if not sess or not sess.alive:
            log_event(
                "dev",
                "terminal.input_rejected",
                level=logging.WARNING,
                session_id=request.args.get('sid'),
                reason="no_session",
            )
            return jsonify({'error': 'no session'}), 404
        data = request.get_data()
        sess.write(data)
        log_event(
            "dev",
            "terminal.input_written",
            session_id=sess.sid,
            bytes=len(data),
        )
        return ('', 204)

    @dev_api.route('/terminal/resize', methods=['POST'])
    @csrf.exempt
    @limiter.exempt
    def terminal_resize():
        sess = _get_session(request.args.get('sid'))
        if not sess or not sess.alive:
            return jsonify({'error': 'no session'}), 404
        body = request.get_json(silent=True) or {}
        rows = max(1, min(int(body.get('rows', 24)), 500))
        cols = max(1, min(int(body.get('cols', 80)), 500))
        sess.resize(rows, cols)
        log_event(
            "dev",
            "terminal.resized",
            session_id=sess.sid,
            rows=rows,
            cols=cols,
        )
        return ('', 204)

    @dev_api.route('/terminal/close', methods=['POST'])
    @csrf.exempt
    @limiter.exempt
    def terminal_close():
        sid = request.args.get('sid')
        with _sessions_lock:
            sess = _sessions.pop(sid, None)
        if sess:
            sess.close()
        log_event(
            "dev",
            "terminal.closed",
            session_id=sid,
            existed=sess is not None,
        )
        return ('', 204)
