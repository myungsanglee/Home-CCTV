import os
from datetime import timedelta

import cv2
from dotenv import load_dotenv
from flask import Flask, render_template, Response, request, redirect, url_for, session, flash

from picam import VideoGet
from pan_tilt import PanTiltServo

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
# app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=10)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=1)


def gen_frames():
    while True:
        try:
            frame = picam.frame
            success, frame = cv2.imencode(".jpg", frame)
            if not success:
                continue
            yield (b"--frame\r\n" b"Content-Type: image/jpeg\r\n\r\n" + frame.tobytes() + b"\r\n\r\n")
        except Exception:
            continue


def valid_login(id, password):
    try:
        value = login_db[id]
        if value == password:
            return True
        else:
            return False

    except KeyError:
        return False


@app.route("/")
def index():
    if "id" in session:
        return render_template("index.html")
    else:
        flash("Please Login")
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        id = request.form["id"]
        password = request.form["password"]
        print(f"ID: {id}, Password: {password}")
        if valid_login(id, password):
            session.permanent = True
            session["id"] = id
            return redirect(url_for("index"))
        else:
            flash("Invalid ID or Password")

    return render_template("/login.html")


@app.route("/logout")
def logout():
    session.pop("id", None)
    return redirect(url_for("login"))


@app.route("/get_cam")
def get_cam():
    if "id" in session:
        return render_template("get_cam.html")
    else:
        flash("Please Login")
        return redirect(url_for("login"))


@app.route("/video_feed")
def video_feed():
    if "id" not in session:
        return redirect(url_for("login"))
    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/servo/center")
def servo_center():
    pan_tilt_servo.set_pan_angle(45)
    pan_tilt_servo.set_tilt_angle(45)
    return "ok"


@app.route("/servo/right")
def servo_right():
    angle = min(90, pan_tilt_servo.get_pan_angle() + per_angle)
    pan_tilt_servo.set_pan_angle(angle)
    return "ok"


@app.route("/servo/left")
def servo_left():
    angle = max(0, pan_tilt_servo.get_pan_angle() - per_angle)
    pan_tilt_servo.set_pan_angle(angle)
    return "ok"


@app.route("/servo/up")
def servo_up():
    angle = min(90, pan_tilt_servo.get_tilt_angle() + per_angle)
    pan_tilt_servo.set_tilt_angle(angle)
    return "ok"


@app.route("/servo/down")
def servo_down():
    angle = max(0, pan_tilt_servo.get_tilt_angle() - per_angle)
    pan_tilt_servo.set_tilt_angle(angle)
    return "ok"


@app.route("/set/angle", methods=["POST"])
def set_angle():
    global per_angle
    angle = request.json["angle"]
    per_angle = int(angle)
    return "ok"


if __name__ == "__main__":
    login_db = {
        "michael": os.environ["MICHAEL_PASSWORD"],
        "natalia": os.environ["NATALIA_PASSWORD"],
    }

    picam = VideoGet().start()
    pan_tilt_servo = PanTiltServo()
    per_angle = 5

    app.run(host="0.0.0.0", port="5000", debug=False, threaded=True)
