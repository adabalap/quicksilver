import sys, logging, io
from pathlib import Path
from datetime import datetime, timezone

log = logging.getLogger("memos_daemon")


class _ISTFormatter(logging.Formatter):
    """Formats log timestamps in Asia/Kolkata (IST) regardless of the system
    timezone. Uses zoneinfo if available; otherwise a fixed +05:30 offset."""
    def __init__(self, fmt):
        super().__init__(fmt)
        self._tz = None
        try:
            from zoneinfo import ZoneInfo
            self._tz = ZoneInfo("Asia/Kolkata")
        except Exception:
            from datetime import timedelta
            self._tz = timezone(timedelta(hours=5, minutes=30), "IST")

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, self._tz)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S") + f",{int(record.msecs):03d} IST"


class _StreamToLogger(io.TextIOBase):
    """Redirects writes (e.g. raw print / C-extension stdout/stderr) to the
    Python logger so everything lands in daemon.log with a proper timestamp."""
    def __init__(self, logger, level=logging.INFO):
        super().__init__()
        self._log = logger
        self._level = level
        self._buf = ""

    def write(self, msg):
        self._buf += msg
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip()
            if line:
                self._log.log(self._level, line)
        return len(msg)

    def flush(self):
        if self._buf.strip():
            self._log.log(self._level, self._buf.rstrip())
            self._buf = ""


def setup_logging(log_file: str | None, level=logging.INFO,
                  redirect_stdio: bool = True):
    log.setLevel(level)
    fmt = _ISTFormatter("%(asctime)s [%(levelname)s] %(message)s")
    sh = logging.StreamHandler(sys.__stdout__)   # always write to the REAL stdout
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if log_file:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file)
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except Exception as e:
            log.warning(f"Could not open log file {log_file}: {e}")

    if redirect_stdio and log_file:
        # After the file handler is attached, redirect Python's sys.stdout and
        # sys.stderr so that any stray print() or C-level output goes to the log.
        # NOTE: stderr is used by libraries for warnings, deprecation notices and
        # progress bars — NOT just errors — so it is logged at WARNING, not ERROR.
        # Our own genuine errors call log.error(...) explicitly and are unaffected.
        sys.stdout = _StreamToLogger(log, logging.INFO)
        sys.stderr = _StreamToLogger(log, logging.WARNING)

