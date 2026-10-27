import os
import time
from collections import deque
from datetime import datetime, timedelta
from threading import Lock, Thread

import cv2
import numpy as np

from recorder import KEEP_DAYS, camera_dir, storage_ready

# 움직임이 있었던 분(minute)을 카메라별, 날짜별 파일에 기록: <recordings>/<cam>/motion/YYYYMMDD.txt
MOTION_DIRNAME = "motion"
SETTLE_AFTER_MOVE = 4  # 카메라를 돌린 직후 이 시간(초) 동안은 감지하지 않음

# 감지 설정 (두 카메라 공통: 작은 화면을 이전 화면과 비교)
CHECK_FPS = 3
SIZE = (320, 180)
PIXEL_THRESHOLD = 25    # 밝기 차이가 이보다 크면 바뀐 픽셀로 봄
MIN_CHANGED = 0.005     # 화면의 0.5% 이상 바뀌면 움직임
MAX_CHANGED = 0.6       # 60% 넘게 한꺼번에 바뀌면 조명 변화로 보고 무시
MAX_SHAKE = 15          # 화면 전체가 이보다 많이(픽셀, 320x180 기준) 밀리면 카메라가 크게 움직인 것으로 보고 무시
CONFIRM = (2, 3)        # 최근 3번 중 2번 이상 움직임이어야 기록 (약 1초, 한두 번 툭 튀는 건 무시)


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


def shake_compensated_ratio(prev, gray):
    """두 프레임 사이 화면 전체의 흔들림(평행이동)을 먼저 맞춘 뒤, 바뀐 픽셀 비율을 계산

    서보가 살짝 떨리면 화면 전체가 몇 픽셀씩 밀려서 모든 경계선이 바뀐 것처럼 보이는데,
    밀린 만큼 되돌려 맞추면 그 차이는 사라지고 일부만 움직인 것(고양이 등)만 남음
    반환: (바뀐 픽셀 비율, 화면 전체가 밀린 양(px)), 흔들림이 너무 크면 비율은 None
    """
    h, w = gray.shape
    # 화면 가장자리가 흔들림 계산을 방해하지 않도록 가운데에 무게를 두는 창(Hanning window)을 씀
    window = cv2.createHanningWindow((w, h), cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(prev.astype(np.float32), gray.astype(np.float32), window)
    shake = (dx * dx + dy * dy) ** 0.5
    if shake > MAX_SHAKE:
        return None, shake
    aligned = cv2.warpAffine(prev, np.float32([[1, 0, dx], [0, 1, dy]]), (w, h), borderMode=cv2.BORDER_REPLICATE)
    # 밀린 만큼 가장자리는 비교할 수 없어서 제외
    m = int(np.ceil(max(abs(dx), abs(dy)))) + 2
    diff = cv2.absdiff(gray[m:h - m, m:w - m], aligned[m:h - m, m:w - m])
    _, changed = cv2.threshold(diff, PIXEL_THRESHOLD, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(changed) / changed.size, shake


class FrameMotion:
    """실시간 화면용으로 이미 받고 있는 프레임을 작게 줄여 이전 프레임과 비교 (Pi, Tapo 공통)
    (카메라 떨림으로 화면 전체가 흔들린 건 보정하고, 최근 3번 중 2번 이상 움직임일 때만 기록)

    camera: frame 속성으로 최신 프레임을 주는 객체 (VideoGet, TapoCamera)
    mover: last_move 속성으로 카메라를 마지막으로 돌린 시각을 주는 객체 (PanTiltServo, TapoCamera)
    """

    def __init__(self, cam_name, camera, mover):
        self.camera = camera
        self.mover = mover
        self.log = MotionLog(cam_name)
        self.active = False

    def start(self):
        Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        prev = None
        recent = deque(maxlen=CONFIRM[1])
        interval = 1.0 / CHECK_FPS
        while True:
            time.sleep(interval)
            if time.time() - self.mover.last_move < SETTLE_AFTER_MOVE:
                prev = None
                recent.clear()
                self.active = False
                continue
            try:
                small = cv2.resize(self.camera.frame, SIZE, interpolation=cv2.INTER_AREA)
            except Exception:
                continue
            gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
            if prev is not None:
                ratio, _ = shake_compensated_ratio(prev, gray)
                recent.append(ratio is not None and MIN_CHANGED <= ratio <= MAX_CHANGED)
                self.active = sum(recent) >= CONFIRM[0]
                if self.active:
                    self.log.mark()
            prev = gray
