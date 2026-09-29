import os
import time
from threading import Thread, Lock
from urllib.parse import quote

import cv2
import numpy as np
from onvif import ONVIFCamera

WSDL_DIR = os.environ.get("ONVIF_WSDL_DIR", "/home/michael/.local/lib/python3.4/site-packages/wsdl")
ONVIF_PORT = 2020
RTSP_PORT = 554
STREAM = "stream2"  # 1280x720 (stream1은 2304x1296)

# ONVIF 좌표(-1 ~ 1)를 각도로 변환하기 위한 C210 스펙 (좌우 360도, 상하 114도)
PAN_DEG_PER_UNIT = 360.0 / 2.0
TILT_DEG_PER_UNIT = 114.0 / 2.0

# RelativeMove 방향 (C210은 pan 좌표가 실제 방향과 반대)
DIRECTIONS = {
    "right": (-1, 0),
    "left": (1, 0),
    "up": (0, 1),
    "down": (0, -1),
}


class TapoCamera:
    """RTSP 영상은 전용 스레드로 계속 받아오고, PTZ는 ONVIF로 제어"""

    def __init__(self, ip, user, password):
        self.ip = ip
        self.user = user
        self.password = password
        self.rtsp_url = f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@{ip}:{RTSP_PORT}/{STREAM}"

        self._frame = self._placeholder("Connecting to Tapo...")
        self._lock = Lock()
        self._ptz_lock = Lock()
        self._ptz = None
        self._token = None
        self.stopped = False

    @staticmethod
    def _placeholder(text):
        frame = np.zeros((720, 1280, 3), np.uint8)
        cv2.putText(frame, text, (40, 360), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
        return frame

    @property
    def frame(self):
        with self._lock:
            return self._frame.copy()

    def start(self):
        self.stopped = False
        Thread(target=self.get, args=(), daemon=True).start()
        return self

    def get(self):
        while not self.stopped:
            cap = cv2.VideoCapture(self.rtsp_url)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            while not self.stopped:
                ok, new_frame = cap.read()
                if not ok:
                    break
                with self._lock:
                    self._frame = new_frame
            cap.release()
            if not self.stopped:
                # 연결이 끊기면 안내 화면을 보여주고 재연결
                with self._lock:
                    self._frame = self._placeholder("Tapo disconnected. Reconnecting...")
                time.sleep(3)

    def stop(self):
        self.stopped = True

    def _get_ptz(self):
        if self._ptz is None:
            cam = ONVIFCamera(self.ip, ONVIF_PORT, self.user, self.password, WSDL_DIR)
            self._token = cam.create_media_service().GetProfiles()[0].token
            self._ptz = cam.create_ptz_service()
        return self._ptz

    def _call_ptz(self, method, params):
        with self._ptz_lock:
            try:
                ptz = self._get_ptz()
                getattr(ptz, method)({"ProfileToken": self._token, **params})
            except Exception:
                # 다음 호출 때 ONVIF에 다시 연결
                self._ptz = None
                raise

    def move(self, direction, degrees):
        dx, dy = DIRECTIONS[direction]
        self._call_ptz("RelativeMove", {
            "Translation": {"PanTilt": {
                "x": dx * degrees / PAN_DEG_PER_UNIT,
                "y": dy * degrees / TILT_DEG_PER_UNIT,
            }},
        })

    def center(self):
        self._call_ptz("AbsoluteMove", {"Position": {"PanTilt": {"x": 0.0, "y": 0.0}}})
