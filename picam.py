import time
from threading import Thread, Lock

import cv2
from picamera2 import Picamera2
from libcamera import controls
import numpy as np


class VideoGet:
    """
    Class that continuously gets frames from a VideoCapture object
    with a dedicated thread.
    """

    def __init__(self):
        self.picam2 = Picamera2()
        # self.picam2.set_controls({"AfMode": controls.AfModeEnum.Continuous, "AfSpeed": controls.AfSpeedEnum.Fast})
        self.picam2.preview_configuration.main.size = (1280, 720)
        self.picam2.preview_configuration.main.format = "RGB888"
        # self.picam2.video_configuration.controls.FrameRate = 60.
        self.picam2.preview_configuration.align()
        self.picam2.configure("preview")
        self.picam2.start()
        self._frame = self.picam2.capture_array()
        self._lock = Lock()
        self.stopped = False

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
            new_frame = self.picam2.capture_array()
            with self._lock:
                self._frame = new_frame
        with self._lock:
            self._frame = np.zeros((720, 1280, 3), np.uint8)
        # self._frame = np.zeros((480, 640, 3), np.uint8)

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
