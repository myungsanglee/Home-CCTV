from datetime import timedelta

import cv2
from flask import Flask, render_template, Response, request, redirect, url_for, session, flash

from picam import VideoGet

app = Flask(__name__)
app.secret_key = 'secretkey'
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=10)

picam = VideoGet().start()

def gen_frames():
    while True:
        frame = picam.frame
        _, frame = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame.tobytes() + b'\r\n\r\n')

def valid_login(id, password):
    if id == 'natalia' and password == 'natalia':
        return True
    else:
        return False

@app.route('/')
def index():
    if 'id' in session:
        return render_template('index.html')
    else:
        flash('Please Login')
        return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        id = request.form['id']
        password = request.form['password']
        print(f'ID: {id}, Password: {password}')
        if valid_login(id, password):
            session['id'] = id
            # return render_template('index.html')
            return redirect(url_for('index'))
        else:
            flash('Invalid ID / Password')

    return render_template('/login.html')

@app.route('/logout')
def logout():
    session.pop('id', None)
    return redirect(url_for('login'))

@app.route('/get_cam')
def get_cam():
    if 'id' in session:
        return render_template('get_cam.html')
    else:
        flash('Please Login')
        return redirect(url_for('login'))

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/servo/left')
def left():
    print('Left')
    return 'ok'

if __name__ == '__main__':
    # app.run(host='172.30.1.26', port='5000', debug=False, threaded=True)
    app.run(host='0.0.0.0', port='5000', debug=False, threaded=True)