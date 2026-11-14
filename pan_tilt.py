import json
import logging
import math
import os
import time
from threading import Lock, Timer
from adafruit_pca9685 import PCA9685
from board import SCL, SDA
import busio

from applog import supervised_thread

log = logging.getLogger("pan-tilt")

# PCA9685 펄스 길이 (12bit tick, 50Hz에서 1 tick = 20000μs / 4096 ≈ 4.88μs)
# 코드의 각도 0~90을 이 범위에 대응시킴. 실제 회전은 150° 이상 (명령 10° ≈ 실제 17°)
MIN_PULSE = 123  # 600μs  (123 = 600 * 4096 / 20000)
MAX_PULSE = 492  # 2400μs (492 = 2400 * 4096 / 20000)

DEFAULT_TILT = 45
DEFAULT_PAN = 45
STATE_FILE = os.path.join(os.path.dirname(__file__), "servo_state.json")

TILT_CHANNEL = 0
PAN_CHANNEL = 1

# 부드럽게 움직이기: 목표 펄스를 한 번에 보내면 서보가 최고 속도로 달려가 급정지하면서
# 그 충격으로 떨림(헌팅)이 시작되고, 바닥에 단단히 고정한 상태에서는 그 떨림이 계속 이어짐.
# 펄스를 조금씩 나눠 보내서 천천히 출발하고 천천히 멈추게 함 (29°에서 측정: 흔들림 115~174px -> 10~12px)
STEP_INTERVAL = 0.02       # 20ms마다 펄스 갱신
MIN_DURATION = 0.3         # 아주 작은 이동도 최소 이 시간에 걸쳐 움직임
MAX_DURATION = 1.0         # 큰 이동(Center 등)도 최대 이 시간
SECONDS_PER_DEGREE = 0.04  # 이동 거리 1도당 추가 시간 (10도 -> 0.6초)


def _duration(distance):
    return min(MAX_DURATION, max(MIN_DURATION, 0.2 + SECONDS_PER_DEGREE * distance))


def _ease(t):
    """0~1 진행률 -> 천천히 출발, 천천히 멈추는 곡선"""
    return 0.5 - 0.5 * math.cos(math.pi * t)


class _Axis:
    def __init__(self, channel, angle):
        self.channel = channel
        self.current = float(angle)   # 지금 서보에 보내고 있는 각도
        self.target = float(angle)    # 가려는 각도
        self.start = float(angle)
        self.start_time = 0.0
        self.duration = 0.0

    @property
    def moving(self):
        return self.current != self.target


class PanTiltServo:
    def __init__(self):
        i2c = busio.I2C(SCL, SDA)
        time.sleep(1)
        self.pca = PCA9685(i2c)
        self.pca.frequency = 50

        saved = self._load_state()
        if saved:
            # 저장된 마지막 위치로 이동 (고정 기본값이 아닌 마지막 위치로 복원)
            tilt, pan = saved["tilt"], saved["pan"]
        else:
            # 최초 실행: 기본 위치로 이동
            tilt, pan = DEFAULT_TILT, DEFAULT_PAN
        self._save_timer = None
        self._lock = Lock()
        self.last_move = 0.0  # 마지막으로 서보를 움직인 시각 (움직임 감지에서 사용)
        self._last_error_log = 0.0
        self.tilt = _Axis(TILT_CHANNEL, tilt)
        self.pan = _Axis(PAN_CHANNEL, pan)

        # 켜질 때는 서보의 실제 위치를 알 수 없어서 저장된 위치로 바로 보냄
        self._write(self.tilt, self.tilt.target)
        self._write(self.pan, self.pan.target)
        supervised_thread("pan-tilt", self._run)

    def _load_state(self):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _schedule_save(self):
        # 마지막 이동 후 1초간 추가 이동이 없을 때만 저장
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = Timer(1.0, self._save_state)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump({"tilt": self.tilt.target, "pan": self.pan.target}, f)

    def _write(self, axis, angle):
        pulse = MIN_PULSE + (angle / 90.0) * (MAX_PULSE - MIN_PULSE)
        self.pca.channels[axis.channel].duty_cycle = int((pulse / 4096.0) * 0xFFFF)
        axis.current = angle
        self.last_move = time.time()

    def _set_target(self, axis, angle):
        with self._lock:
            # 움직이는 도중이면 지금 위치에서 새 목표로 이어서 움직임
            axis.start = axis.current
            axis.target = float(angle)
            axis.start_time = time.time()
            axis.duration = _duration(abs(axis.target - axis.start))
        self._schedule_save()

    def _run(self):
        while True:
            time.sleep(STEP_INTERVAL)
            with self._lock:
                for axis in (self.tilt, self.pan):
                    if not axis.moving:
                        continue
                    t = (time.time() - axis.start_time) / axis.duration
                    angle = axis.target if t >= 1 else axis.start + (axis.target - axis.start) * _ease(t)
                    try:
                        self._write(axis, angle)
                    except OSError as e:
                        # 서보 보드(I2C) 통신 실패: 다음 갱신 때 다시 시도 (로그는 10초에 한 번만)
                        if time.time() - self._last_error_log > 10:
                            self._last_error_log = time.time()
                            log.warning(f"서보 보드 통신 실패 (채널 {axis.channel}): {e}")
                        time.sleep(0.1)

    def get_tilt_angle(self):
        return self.tilt.target

    def set_tilt_angle(self, angle):
        self._set_target(self.tilt, angle)

    def get_pan_angle(self):
        return self.pan.target

    def set_pan_angle(self, angle):
        self._set_target(self.pan, angle)
