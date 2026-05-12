import json
import os
import time
from threading import Timer
from adafruit_pca9685 import PCA9685
from board import SCL, SDA
import busio

# MIN_PULSE = 143  # 최소 펄스 길이 (0도, 700μs @ 50Hz, 143 = 700 * 4096 / 20000)
# MAX_PULSE = 471  # 최대 펄스 길이 (90도, 2300μs @ 50Hz, 471 = 2300 * 4096 / 20000)

MIN_PULSE = 123  # 최소 펄스 길이 (0도, 600μs @ 50Hz, 123 = 700 * 4096 / 20000)
MAX_PULSE = 492  # 최대 펄스 길이 (90도, 2400μs @ 50Hz, 492 = 2300 * 4096 / 20000)

DEFAULT_TILT = 45
DEFAULT_PAN = 45
STATE_FILE = os.path.join(os.path.dirname(__file__), "servo_state.json")


class PanTiltServo:
    def __init__(self):
        i2c = busio.I2C(SCL, SDA)
        time.sleep(1)
        self.pca = PCA9685(i2c)
        self.pca.frequency = 50

        saved = self._load_state()
        if saved:
            # 저장된 마지막 위치로 이동 (고정 기본값이 아닌 마지막 위치로 복원)
            self.tilt_angle = saved["tilt"]
            self.pan_angle = saved["pan"]
        else:
            # 최초 실행: 기본 위치로 이동
            self.tilt_angle = DEFAULT_TILT
            self.pan_angle = DEFAULT_PAN
        self._save_timer = None
        self.set_tilt_angle(self.tilt_angle)
        self.set_pan_angle(self.pan_angle)

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
            json.dump({"tilt": self.tilt_angle, "pan": self.pan_angle}, f)

    def get_tilt_angle(self):
        return self.tilt_angle

    def set_tilt_angle(self, angle):
        pulse = MIN_PULSE + (angle / 90.0) * (MAX_PULSE - MIN_PULSE)
        duty_cycle = int((pulse / 4096.0) * 0xFFFF)
        self.pca.channels[0].duty_cycle = duty_cycle
        time.sleep(0.03)
        self.tilt_angle = angle
        self._schedule_save()

    def get_pan_angle(self):
        return self.pan_angle

    def set_pan_angle(self, angle):
        pulse = MIN_PULSE + (angle / 90.0) * (MAX_PULSE - MIN_PULSE)
        duty_cycle = int((pulse / 4096.0) * 0xFFFF)
        self.pca.channels[1].duty_cycle = duty_cycle
        time.sleep(0.03)
        self.pan_angle = angle
        self._schedule_save()
