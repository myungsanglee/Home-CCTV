import asyncio
import fractions
import os
import threading
from datetime import timedelta

import av
import cv2
import sounddevice as sd
from aiortc import AudioStreamTrack, RTCPeerConnection, RTCSessionDescription
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, Response, request, redirect, url_for, session, flash

from picam import VideoGet
from pan_tilt import PanTiltServo

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=1)

AUDIO_DEVICE = 1   # sounddevice index: USB PnP Sound Device (hw:3,0)
SAMPLE_RATE = 48000
CHUNK_SIZE = 960   # 20ms @ 48kHz

webrtc_loop = asyncio.new_event_loop()
threading.Thread(target=webrtc_loop.run_forever, daemon=True).start()
pcs = set()

# 전역 오디오 스트림 - 서버 시작 시 한 번만 열고 유지
_audio_queues = set()
_audio_queues_lock = threading.Lock()


def _global_audio_callback(indata, frames, time_info, status):
    data = indata.copy()
    with _audio_queues_lock:
        for q in list(_audio_queues):
            asyncio.run_coroutine_threadsafe(q.put(data), webrtc_loop)


class MicrophoneTrack(AudioStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self._queue = asyncio.Queue()
        self._pts = 0
        with _audio_queues_lock:
            _audio_queues.add(self._queue)

    async def recv(self):
        data = await self._queue.get()
        frame = av.AudioFrame(format="s16", layout="mono", samples=CHUNK_SIZE)
        frame.sample_rate = SAMPLE_RATE
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        frame.pts = self._pts
        frame.planes[0].update(data.tobytes())
        self._pts += CHUNK_SIZE
        return frame

    def stop(self):
        with _audio_queues_lock:
            _audio_queues.discard(self._queue)
        super().stop()


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


@app.route("/offer", methods=["POST"])
def offer():
    if "id" not in session:
        return redirect(url_for("login"))

    params = request.json

    async def process():
        pc = RTCPeerConnection()
        mic = MicrophoneTrack()
        pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            if pc.connectionState in ("failed", "closed", "disconnected"):
                mic.stop()
                await pc.close()
                pcs.discard(pc)

        pc.addTrack(mic)

        sdp_offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
        await pc.setRemoteDescription(sdp_offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        while pc.iceGatheringState != "complete":
            await asyncio.sleep(0.1)

        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    future = asyncio.run_coroutine_threadsafe(process(), webrtc_loop)
    result = future.result(timeout=10)
    return jsonify(result)


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

    global_audio_stream = sd.InputStream(
        device=AUDIO_DEVICE,
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="int16",
        blocksize=CHUNK_SIZE,
        callback=_global_audio_callback,
    )
    global_audio_stream.start()

    app.run(host="0.0.0.0", port="5000", debug=False, threaded=True)
