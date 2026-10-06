import os
import time
from threading import Thread, Lock
from urllib.parse import quote

import av
import cv2
import numpy as np
from onvif import ONVIFCamera

from audio import AudioFanout

WSDL_DIR = os.environ.get("ONVIF_WSDL_DIR", "/home/michael/.local/lib/python3.4/site-packages/wsdl")
ONVIF_PORT = 2020
RTSP_PORT = 554
STREAM = "stream2"  # 실시간 화면용 1280x720
RECORD_STREAM = "stream1"  # 녹화용 2304x1296

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
        self.rtsp_url = self.stream_url(STREAM)
        self.record_url = self.stream_url(RECORD_STREAM)

        self._frame = self._placeholder("Connecting to Tapo...")
        self._lock = Lock()
        self._ptz_lock = Lock()
        self._ptz = None
        self._token = None
        self.last_move = 0.0  # 마지막으로 PTZ를 움직인 시각 (움직임 감지에서 사용)
        self.stopped = False

    def stream_url(self, stream):
        return f"rtsp://{quote(self.user, safe='')}:{quote(self.password, safe='')}@{self.ip}:{RTSP_PORT}/{stream}"

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
        self.last_move = time.time()
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


class TapoAudio(AudioFanout):
    """Tapo 마이크 소리: 듣는 사람이 있을 때만 RTSP에서 오디오만 받아와서
    (8kHz G.711 -> 48kHz) 960샘플 조각으로 나눠 보냄. 영상은 디코딩하지 않음"""

    def __init__(self, rtsp_url, sample_rate=48000, chunk_size=960):
        super().__init__()
        self.rtsp_url = rtsp_url
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self._thread = None
        self._thread_lock = Lock()

    def _on_first_listener(self):
        with self._thread_lock:
            if self._thread is None:
                self._thread = Thread(target=self._run, daemon=True)
                self._thread.start()

    def _run(self):
        while True:
            with self._thread_lock:
                if not self.has_listeners():
                    self._thread = None
                    return
            try:
                container = av.open(self.rtsp_url, options={"rtsp_transport": "tcp"}, timeout=10)
            except Exception:
                time.sleep(3)
                continue
            try:
                stream = container.streams.audio[0]
                resampler = av.AudioResampler(format="s16", layout="mono", rate=self.sample_rate)
                buf = np.zeros(0, np.int16)
                for packet in container.demux(stream):
                    if not self.has_listeners():
                        break
                    for frame in packet.decode():
                        for out in resampler.resample(frame):
                            buf = np.concatenate([buf, out.to_ndarray().reshape(-1)])
                    while len(buf) >= self.chunk_size:
                        self.dispatch(buf[:self.chunk_size].reshape(-1, 1).copy())
                        buf = buf[self.chunk_size:]
            except Exception:
                # 연결이 끊기면 잠시 후 다시 연결
                time.sleep(3)
            finally:
                container.close()
