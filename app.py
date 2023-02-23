import cv2
from flask import Flask, render_template, Response

from picam import VideoGet

app = Flask(__name__)
picam = VideoGet().start()

def gen_frames():
    while True:
        frame = picam.frame
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
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host='172.30.1.26', port='5000', debug=False, threaded=True)