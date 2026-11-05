"""로그 설정과 백그라운드 작업 감시

- logs/app.log      : 일반 기록 + 오류 (웹 접속 기록 제외)
- logs/error.log    : 경고(WARNING) 이상만, 사람이 읽는 형식
- logs/errors.jsonl : 경고 이상만, 웹 화면(오류 목록)용 JSON 한 줄씩
- 서비스 로그(journalctl -u cctv)에도 모두 출력
"""
import json
import logging
import os
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
ERRORS_JSONL = os.path.join(LOG_DIR, "errors.jsonl")
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

log = logging.getLogger("cctv")


class _NoAccessLog(logging.Filter):
    """파일에는 웹 접속 기록(werkzeug INFO)을 남기지 않음 (전체 로그의 대부분이라 오류가 묻힘)"""

    def filter(self, record):
        return not (record.name == "werkzeug" and record.levelno < logging.WARNING)


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts": record.created,
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "source": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["detail"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)

    app_file = RotatingFileHandler(os.path.join(LOG_DIR, "app.log"), maxBytes=5 * 1024 * 1024, backupCount=5)
    app_file.setFormatter(logging.Formatter(FORMAT))
    app_file.addFilter(_NoAccessLog())
    root.addHandler(app_file)

    error_file = RotatingFileHandler(os.path.join(LOG_DIR, "error.log"), maxBytes=2 * 1024 * 1024, backupCount=5)
    error_file.setLevel(logging.WARNING)
    error_file.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(error_file)

    json_file = RotatingFileHandler(ERRORS_JSONL, maxBytes=2 * 1024 * 1024, backupCount=2)
    json_file.setLevel(logging.WARNING)
    json_file.setFormatter(_JsonFormatter())
    root.addHandler(json_file)

    # 잡히지 않은 오류도 로그에 남김
    def excepthook(exc_type, exc, tb):
        logging.getLogger("cctv").critical("처리되지 않은 오류", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args):
        logging.getLogger("cctv").error(f"스레드 {args.thread.name if args.thread else '?'}에서 처리되지 않은 오류",
                                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


def supervised_thread(name, target, restart_delay=5):
    """백그라운드 작업 실행. 예상 못 한 오류로 죽으면 오류 로그에 남기고 restart_delay초 뒤 다시 시작
    (target이 정상적으로 return하면 끝냄)"""
    def runner():
        while True:
            try:
                target()
                return
            except Exception:
                logging.getLogger(name).exception(f"백그라운드 작업 '{name}'이(가) 오류로 멈춰서 {restart_delay}초 뒤 다시 시작함")
                time.sleep(restart_delay)

    thread = threading.Thread(target=runner, name=name, daemon=True)
    thread.start()
    return thread


def recent_errors(hours=24, limit=500):
    """웹 오류 목록용: 최근 hours시간 경고/오류 (오래된 순)"""
    cutoff = time.time() - hours * 3600
    entries = []
    for path in (ERRORS_JSONL + ".1", ERRORS_JSONL):
        try:
            with open(path) as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("ts", 0) >= cutoff:
                        entries.append(entry)
        except FileNotFoundError:
            pass
    entries.sort(key=lambda e: e["ts"])
    return entries[-limit:]
