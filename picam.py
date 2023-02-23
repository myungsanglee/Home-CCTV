import time
from threading import Thread

import cv2
from picamera2 import Picamera2
import numpy as np

def no_threading():
    picam2 = Picamera2()
    picam2.preview_configuration.main.size = (1080, 720)
    picam2.preview_configuration.main.format = "RGB888"
    picam2.video_configuration.controls.FrameRate = 60.
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
        if cv2.waitKey(1)==ord('q'):
            break
        
        tEnd=time.time()
        loopTime=tEnd-tStart
        fps=.9*fps + .1*(1/loopTime)
        print(f'\rFPS: {fps}', end='')
    cv2.destroyAllWindows()


class VideoGet:
    """
    Class that continuously gets frames from a VideoCapture object
    with a dedicated thread.
    """

    def __init__(self):
        self.picam2 = Picamera2()
        self.picam2.preview_configuration.main.size = (1080, 720)
        self.picam2.preview_configuration.main.format = "RGB888"
        self.picam2.video_configuration.controls.FrameRate = 60.
        self.picam2.preview_configuration.align()
        self.picam2.configure("preview")
        self.picam2.start()
        self.frame = self.picam2.capture_array()
        self.stopped = False

    def start(self):
        self.stopped = False
        Thread(target=self.get, args=(), daemon=True).start()
        return self
    
    def get(self):
        while not self.stopped:
            self.frame = self.picam2.capture_array()
        self.frame = np.zeros((720, 1080, 3), np.uint8)
    
    def stop(self):
        self.stopped = True


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
        
        print(f'\rFPS: {fps}', end='')
    print('')
    cv2.destroyAllWindows()

if __name__ == '__main__':
    # no_threading()
    
    thread_video_get()