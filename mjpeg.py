import time
from threading import Condition, Thread

import cv2


class MjpegStream:
    """카메라 프레임을 JPEG로 한 번만 인코딩해서 보고 있는 모든 사람에게 같이 보냄

    보는 사람이 있을 때만 인코딩 스레드가 돌고, 모두 나가면 스레드도 멈춤
    """

    def __init__(self, camera, fps=20):
        self.camera = camera
        self.interval = 1.0 / fps
        self._cond = Condition()
        self._jpeg = None
        self._seq = 0
        self._viewers = 0
        self._thread = None

    def _encode_loop(self):
        while True:
            with self._cond:
                if self._viewers == 0:
                    self._thread = None
                    return
            start = time.time()
            try:
                ok, buf = cv2.imencode(".jpg", self.camera.frame)
            except Exception:
                ok = False
            if ok:
                with self._cond:
                    self._jpeg = buf.tobytes()
                    self._seq += 1
                    self._cond.notify_all()
            time.sleep(max(0.0, self.interval - (time.time() - start)))

    def frames(self):
        with self._cond:
            self._viewers += 1
            if self._thread is None:
                self._thread = Thread(target=self._encode_loop, daemon=True)
                self._thread.start()
        seq = 0
        try:
            while True:
                with self._cond:
                    if not self._cond.wait_for(lambda: self._seq != seq, timeout=5):
                        continue
                    seq, jpeg = self._seq, self._jpeg
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n\r\n"
        finally:
            with self._cond:
                self._viewers -= 1
