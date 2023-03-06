from datetime import timedelta

import cv2
from flask import Flask, render_template, Response, request, redirect, url_for, session, flash

# from picam import VideoGet
from pan_tilt import PanTiltServo

app = Flask(__name__)
app.secret_key = 'secretkey'
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=10)

# def gen_frames():
#     while True:
#         frame = picam.frame
#         _, frame = cv2.imencode('.jpg', frame)
#         yield (b'--frame\r\n'
#                b'Content-Type: image/jpeg\r\n\r\n' + frame.tobytes() + b'\r\n\r\n')

def valid_login(id, password):
    login_db = {
        'michael': 'leeprs0577',
        'natalia': 'natalia0114',
    }
    
    try:
        value = login_db[id]
        if value == password:
            return True
        else:
            return False
        
    except KeyError:
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
            flash('Invalid ID or Password')

    return render_template('/login.html')

@app.route('/logout')
def logout():
    session.pop('id', None)
    return redirect(url_for('login'))

# @app.route('/get_cam')
# def get_cam():
#     if 'id' in session:
#         return render_template('get_cam.html')
#     else:
#         flash('Please Login')
#         return redirect(url_for('login'))

# @app.route('/video_feed')
# def video_feed():
#     return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

# @app.route('/servo/right')
# def servo_right():
#     angle = min(180, pan_tilt_servo.get_pan_angle() + per_angle)
#     pan_tilt_servo.set_pan_angle(angle)
#     return 'ok'

# @app.route('/servo/left')
# def servo_left():
#     angle = max(0, pan_tilt_servo.get_pan_angle() - per_angle)
#     pan_tilt_servo.set_pan_angle(angle)
#     return 'ok'

# @app.route('/servo/up')
# def servo_up():
#     angle = min(180, pan_tilt_servo.get_tilt_angle() + per_angle)
#     pan_tilt_servo.set_tilt_angle(angle)
#     return 'ok'

# @app.route('/servo/down')
# def servo_down():
#     angle = max(0, pan_tilt_servo.get_tilt_angle() - per_angle)
#     pan_tilt_servo.set_tilt_angle(angle)
#     return 'ok'

if __name__ == '__main__':
    # picam = VideoGet().start()
    # pan_tilt_servo = PanTiltServo()
    # per_angle = 5
    
    app.run(host='0.0.0.0', port='5000', debug=False, threaded=True)