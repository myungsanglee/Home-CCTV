import json
import logging
import os
import time
from threading import Thread, Lock, RLock

import cv2
from picamera2 import MappedArray, Picamera2
from libcamera import controls
import numpy as np

from applog import supervised_thread

log = logging.getLogger("pi-camera")

# 영상 왼쪽 위 날짜/시각 (Tapo와 같은 형식)
TIMESTAMP_FORMAT = "%Y-%m-%d %I:%M:%S %p"
TIMESTAMP_ORIGIN = (16, 44)
TIMESTAMP_FONT = cv2.FONT_HERSHEY_SIMPLEX
TIMESTAMP_SCALE = 1.2

# 카메라 멈춤 감시
FRAME_TIMEOUT = 5            # 이 시간(초) 안에 프레임이 안 오면 멈춘 것으로 보고 복구
MAX_RESTART_ATTEMPTS = 3     # 카메라를 다시 열어도 이만큼 실패하면 서비스 전체를 재시작
RESTART_RETRY_DELAY = 5
REOPEN_TIMEOUT = 30          # 카메라를 닫고 다시 여는 데 이보다 오래 걸리면(멈춤) 서비스 전체를 재시작
CAMERA_EVENT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "camera_events.log")


def draw_timestamp(request):
    """카메라가 프레임을 만든 직후에 날짜/시각을 그려 넣음
    (실시간 화면, 녹화, 화면 저장, 움직임 감지 모두 같은 프레임을 씀)"""
    text = time.strftime(TIMESTAMP_FORMAT)
    with MappedArray(request, "main") as m:
        # 밝은 배경에서도 보이도록 검은 테두리 위에 흰 글씨
        cv2.putText(m.array, text, TIMESTAMP_ORIGIN, TIMESTAMP_FONT, TIMESTAMP_SCALE, (0, 0, 0), 6, cv2.LINE_AA)
        cv2.putText(m.array, text, TIMESTAMP_ORIGIN, TIMESTAMP_FONT, TIMESTAMP_SCALE, (255, 255, 255), 2, cv2.LINE_AA)


def run_with_timeout(fn, timeout):
    """fn을 별도 스레드에서 실행하고 timeout(초) 안에 안 끝나면 TimeoutError
    (멈춘 카메라를 닫는 동작 자체가 영원히 끝나지 않을 수 있어서 필요)"""
    result = {}

    def target():
        try:
            result["value"] = fn()
        except Exception as e:
            result["error"] = e

    worker = Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"{fn.__name__}이(가) {timeout}초 안에 끝나지 않음")
    if "error" in result:
        raise result["error"]
    return result.get("value")


class CameraEventLog:
    """카메라가 멈췄다가 복구된 기록. 서비스 로그와 logs/camera_events.log(JSON 한 줄씩)에 남김"""

    def __init__(self, path=CAMERA_EVENT_LOG):
        self.path = path
        self._lock = Lock()

    def write(self, event):
        line = json.dumps(event, ensure_ascii=False)
        log.warning(f"카메라 멈춤 기록: {event.get('message')} (멈춘 시간 {event.get('duration_s')}초, 시도 {event.get('attempts')}번)")
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a") as f:
                f.write(line + "\n")

    def recent(self, hours=24):
        cutoff = time.time() - hours * 3600
        events = []
        try:
            with open(self.path) as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("end_ts", e.get("start_ts", 0)) >= cutoff:
                        events.append(e)
        except FileNotFoundError:
            pass
        return events


class VideoGet:
    """
    Class that continuously gets frames from a VideoCapture object
    with a dedicated thread.

    카메라 센서가 프레임을 보내지 않고 멈추면(libcamera "Camera frontend has timed out")
    라이브러리가 스스로 살아나지 않으므로, 5초 넘게 프레임이 안 오면 카메라를 닫았다가 다시 열어서 복구함
    """

    def __init__(self):
        self.camera_lock = RLock()   # 카메라를 다시 여는 동안 녹화가 카메라를 건드리지 않게 함
        self.before_restart = []     # 카메라를 닫기 전에 호출 (녹화 일시정지)
        self.after_restart = []      # 카메라를 다시 연 뒤 호출 (녹화 재개)
        self.on_fatal = None         # 여러 번 복구에 실패하면 호출 (서비스 재시작)
        self.events = CameraEventLog()
        self.stall = None            # 멈춘 동안의 정보 (정상이면 None)

        self.picam2 = self._open_camera()
        self._frame = self.picam2.capture_array()
        self.last_frame_time = time.time()
        self._lock = Lock()
        self.stopped = False

    @staticmethod
    def _open_camera():
        picam2 = Picamera2()
        # picam2.set_controls({"AfMode": controls.AfModeEnum.Continuous, "AfSpeed": controls.AfSpeedEnum.Fast})
        picam2.preview_configuration.main.size = (1280, 720)
        picam2.preview_configuration.main.format = "RGB888"
        # picam2.video_configuration.controls.FrameRate = 60.
        picam2.preview_configuration.align()
        picam2.configure("preview")
        picam2.pre_callback = draw_timestamp
        picam2.start()
        return picam2

    @property
    def frame(self):
        with self._lock:
            return self._frame.copy()

    def status(self):
        """웹 배너용 상태: 지금 멈춰 있는지 + 최근 24시간 멈춤/복구 기록"""
        return {
            "stalled": self.stall is not None,
            "stalled_since": self.stall["start_ts"] if self.stall else None,
            "last_frame_ts": self.last_frame_time,
            "recent": self.events.recent(),
        }

    def start(self):
        self.stopped = False
        supervised_thread("pi-camera", self.get)
        return self

    def get(self):
        while not self.stopped:
            try:
                # 시간 제한 없이 기다리면 센서가 멈췄을 때 영원히 기다리므로 제한을 둠
                job = self.picam2.capture_array(wait=False)
                new_frame = self.picam2.wait(job, timeout=FRAME_TIMEOUT)
            except Exception as e:
                if self.stopped:
                    break
                self._recover(e)
                continue
            with self._lock:
                self._frame = new_frame
            self.last_frame_time = time.time()
            if self.stall is not None:
                self._recovered()
        with self._lock:
            self._frame = np.zeros((720, 1280, 3), np.uint8)
        # self._frame = np.zeros((480, 640, 3), np.uint8)

    def _recover(self, error):
        if self.stall is None:
            self.stall = {
                "camera": "pi",
                "start_ts": self.last_frame_time,
                "start": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_frame_time)),
                "error": f"{type(error).__name__}: {error}" if str(error) else type(error).__name__,
                "attempts": 0,
            }
            log.warning(f"Pi 카메라 프레임이 {FRAME_TIMEOUT}초 넘게 오지 않음, 복구 시작 ({self.stall['error']})")
        self.stall["attempts"] += 1
        if self.stall["attempts"] > MAX_RESTART_ATTEMPTS:
            self._fatal("카메라를 여러 번 다시 열어도 프레임이 오지 않아 서비스를 재시작함")
            return
        try:
            run_with_timeout(self._reopen, REOPEN_TIMEOUT)
        except TimeoutError as e:
            # 닫기/녹화 멈추기 자체가 멈춘 경우: 이 프로세스로는 복구할 수 없음
            self._fatal(f"카메라 다시 열기가 멈춰서 서비스를 재시작함 ({e})")
        except Exception as e:
            log.error(f"Pi 카메라 다시 열기 실패 (시도 {self.stall['attempts']}): {e}")
            time.sleep(RESTART_RETRY_DELAY)

    def _reopen(self):
        with self.camera_lock:
            for callback in self.before_restart:
                try:
                    callback()
                except Exception:
                    log.exception("카메라 다시 열기 전 처리(녹화 멈춤) 실패")
            for close in (self.picam2.stop, self.picam2.close):
                try:
                    close()
                except Exception:
                    pass
            time.sleep(1)
            self.picam2 = self._open_camera()
            for callback in self.after_restart:
                try:
                    callback()
                except Exception:
                    log.exception("카메라 다시 연 뒤 처리(녹화 재개) 실패")

    def _fatal(self, message):
        self._finish_stall("failed", message)
        if self.on_fatal:
            self.on_fatal()
        time.sleep(RESTART_RETRY_DELAY)

    def _recovered(self):
        self._finish_stall("recovered", "카메라를 다시 열어 자동 복구됨")

    def _finish_stall(self, result, message):
        stall, self.stall = self.stall, None
        now = time.time()
        stall.update({
            "result": result,
            "message": message,
            "end_ts": now,
            "end": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "duration_s": round(now - stall["start_ts"], 1),
        })
        self.events.write(stall)

    def stop(self):
        self.stopped = True


def no_threading():
    picam2 = Picamera2()
    # picam2.set_controls({"AfMode": controls.AfModeEnum.Continuous, "AfSpeed": controls.AfSpeedEnum.Fast})
    picam2.preview_configuration.main.size = (1280, 720)
    picam2.preview_configuration.main.format = "RGB888"
    # picam2.video_configuration.controls.FrameRate = 60.
    picam2.preview_configuration.align()
    picam2.configure("preview")
    picam2.start()
    fps=0
    pos=(30,60)
    font=cv2.FONT_HERSHEY_SIMPLEX
    height=1.5
    weight=3
    myColor=(0,0,255)
    cv2.namedWindow('Camera', cv2.WINDOW_NORMAL)
    cv2.resizeWindow('Camera', 640, 480)
    while True:
        tStart=time.time()
        frame = picam2.capture_array()
        # tEnd=time.time()
        # loopTime=tEnd-tStart
        # fps=.9*fps + .1*(1/loopTime)
        
        cv2.putText(frame, str(int(fps))+' FPS', pos, font, height, myColor, weight)
        cv2.imshow("Camera", frame)
        key = cv2.waitKey(1)
        if key == 27 or key==ord('q'):
            break
        
        tEnd=time.time()
        loopTime=tEnd-tStart
        fps=.9*fps + .1*(1/loopTime)
        # print(f'\rFPS: {fps}', end='')
    
    cv2.destroyAllWindows()
    

def thread_video_get():
    video_getter = VideoGet().start()
    fps=0
    pos=(30,60)
    font=cv2.FONT_HERSHEY_SIMPLEX
    height=1.5
    weight=3
    myColor=(0,0,255)
    cv2.namedWindow('Camera', cv2.WINDOW_NORMAL)
    cv2.resizeWindow('Camera', 640, 480)
    while True:
        tStart=time.time()
        frame = video_getter.frame

        cv2.putText(frame, str(int(fps))+' FPS', pos, font, height, myColor, weight)
        cv2.imshow("Camera", frame)
        key = cv2.waitKey(1)
        if (key == ord("q")):
            video_getter.stop()
            break
        
        tEnd=time.time()
        loopTime=tEnd-tStart
        fps=.9*fps + .1*(1/loopTime)
        
        # print(f'\rFPS: {fps}', end='')
    # print('')
    cv2.destroyAllWindows()

def opencv_video_get():
    cap = cv2.VideoCapture()
    if not cap.isOpened():
        raise IOError("Can not open camera")
    fps=0
    pos=(30,60)
    font=cv2.FONT_HERSHEY_SIMPLEX
    height=1.5
    weight=3
    myColor=(0,0,255)
    cv2.namedWindow('Camera', cv2.WINDOW_NORMAL)
    cv2.resizeWindow('Camera', 640, 480)
    while True:
        tStart=time.time()
        
        ret, frame = cap.read() 
        if not ret:
            break
        
        cv2.putText(frame, str(int(fps))+' FPS', pos, font, height, myColor, weight)
        cv2.imshow("Camera", frame)
        key = cv2.waitKey(1)
        if key == 27 or key==ord('q'):
            break
        
        tEnd=time.time()
        loopTime=tEnd-tStart
        fps=.9*fps + .1*(1/loopTime)

    cv2.destroyAllWindows()

if __name__ == '__main__':
    # no_threading()
    
    # thread_video_get()

    opencv_video_get()
