import os
import time
from datetime import datetime, timedelta
from threading import Lock, Thread

import cv2
from onvif import ONVIFCamera

from recorder import KEEP_DAYS, camera_dir, storage_ready

# 움직임이 있었던 분(minute)을 카메라별, 날짜별 파일에 기록: <recordings>/<cam>/motion/YYYYMMDD.txt
MOTION_DIRNAME = "motion"
SETTLE_AFTER_MOVE = 4  # 카메라를 돌린 직후 이 시간(초) 동안은 감지하지 않음

# Pi 카메라 감지 설정 (작은 화면을 이전 화면과 비교)
PI_CHECK_FPS = 3
PI_SIZE = (320, 180)
PI_PIXEL_THRESHOLD = 25    # 밝기 차이가 이보다 크면 바뀐 픽셀로 봄
PI_MIN_CHANGED = 0.005     # 화면의 0.5% 이상 바뀌면 움직임
PI_MAX_CHANGED = 0.6       # 60% 넘게 한꺼번에 바뀌면 조명 변화로 보고 무시

# Tapo: 카메라 자체 감지 알림(ONVIF 이벤트)을 받음
TAPO_RESUBSCRIBE = 240     # 이 라이브러리로는 구독 연장이 안 돼서 4분마다 새로 구독
TAPO_STALE = 30            # 30초 넘게 알림이 없으면 움직임이 끝난 것으로 봄


def _motion_dir(cam):
    return os.path.join(camera_dir(cam), MOTION_DIRNAME)


class MotionLog:
    """움직임이 있었던 분을 기록 (같은 분은 한 번만)"""

    def __init__(self, cam):
        self.cam = cam
        self._last_minute = None
        self._lock = Lock()

    def mark(self):
        if not storage_ready():
            return
        now = datetime.now()
        minute = now.strftime("%Y%m%d%H%M")
        with self._lock:
            if minute == self._last_minute:
                return
            self._last_minute = minute
            d = _motion_dir(self.cam)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, now.strftime("%Y%m%d") + ".txt"), "a") as f:
                f.write(minute[8:] + "\n")


def motion_minutes(cam, days):
    """주어진 날짜들(YYYYMMDD)에서 움직임이 있었던 분 집합 {'YYYYMMDDHHMM', ...}"""
    minutes = set()
    for day in days:
        path = os.path.join(_motion_dir(cam), day + ".txt")
        try:
            with open(path) as f:
                minutes.update(day + line.strip() for line in f if line.strip())
        except FileNotFoundError:
            pass
    return minutes


def clean_motion_logs():
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS + 1)).strftime("%Y%m%d")
    for cam in ("pi", "tapo"):
        d = _motion_dir(cam)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name.endswith(".txt") and name[:8] < cutoff:
                os.remove(os.path.join(d, name))


class PiMotion:
    """실시간 화면용으로 이미 받고 있는 프레임을 작게 줄여 이전 프레임과 비교"""

    def __init__(self, video_get, servo):
        self.video_get = video_get
        self.servo = servo
        self.log = MotionLog("pi")
        self.active = False

    def start(self):
        Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        prev = None
        interval = 1.0 / PI_CHECK_FPS
        while True:
            time.sleep(interval)
            if time.time() - self.servo.last_move < SETTLE_AFTER_MOVE:
                prev = None
                self.active = False
                continue
            try:
                small = cv2.resize(self.video_get.frame, PI_SIZE, interpolation=cv2.INTER_AREA)
            except Exception:
                continue
            gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
            if prev is not None:
                _, changed = cv2.threshold(cv2.absdiff(gray, prev), PI_PIXEL_THRESHOLD, 255, cv2.THRESH_BINARY)
                ratio = cv2.countNonZero(changed) / changed.size
                self.active = PI_MIN_CHANGED <= ratio <= PI_MAX_CHANGED
                if self.active:
                    self.log.mark()
            prev = gray


class TapoMotion:
    """Tapo 카메라가 직접 감지한 움직임 알림(IsMotion)을 받아 기록. 라즈베리파이 부담 거의 없음

    Tapo는 상태가 바뀔 때만이 아니라 같은 알림을 계속 반복해서 보내므로 마지막 값만 사용
    """

    def __init__(self, tapo, wsdl_dir, port):
        self.tapo = tapo
        self.wsdl_dir = wsdl_dir
        self.port = port
        self.log = MotionLog("tapo")
        self.active = False

    def start(self):
        Thread(target=self._run, daemon=True).start()
        return self

    def _subscribe(self):
        cam = ONVIFCamera(self.tapo.ip, self.port, self.tapo.user, self.tapo.password, self.wsdl_dir)
        cam.create_events_service().CreatePullPointSubscription(
            {"InitialTerminationTime": f"PT{TAPO_RESUBSCRIBE + 60}S"})
        return cam.create_pullpoint_service()

    def _run(self):
        last_msg = 0.0
        while True:
            try:
                pullpoint = self._subscribe()
                subscribed_at = time.time()
                while time.time() - subscribed_at < TAPO_RESUBSCRIBE:
                    result = pullpoint.PullMessages({"Timeout": "PT5S", "MessageLimit": 100})
                    for msg in result.NotificationMessage or []:
                        data = msg.Message._value_1.find(".//{http://www.onvif.org/ver10/schema}Data")
                        for item in data if data is not None else []:
                            if item.get("Name") == "IsMotion":
                                self.active = item.get("Value") == "true"
                                last_msg = time.time()
                    if time.time() - last_msg > TAPO_STALE:
                        self.active = False
                    moved_recently = time.time() - self.tapo.last_move < SETTLE_AFTER_MOVE
                    if self.active and not moved_recently:
                        self.log.mark()
            except Exception as e:
                print(f"tapo motion error: {e}", flush=True)
                self.active = False
                time.sleep(10)
