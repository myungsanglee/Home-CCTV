import cv2
from flask import Flask, render_template, Response

from realsense import RealSense

app = Flask(__name__)

def gen(camera):
    while True:
        frame = camera.get_numpy_color_image()
        _, frame = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame.tobytes() + b'\r\n\r\n')

@app.route('/')
def default():
    return render_template('index.html')

@app.route('/get_cam')
def get_cam():
    return render_template('get_cam.html')

@app.route('/video_feed')
def video_feed():
    return Response(gen(RealSense()), mimetype='multipart/x-mixed-replace; boundary=frame')