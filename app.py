import asyncio
import fractions
import os
import threading
from datetime import timedelta
from functools import wraps

import av
import sounddevice as sd
from aiortc import AudioStreamTrack, RTCPeerConnection, RTCSessionDescription
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, Response, request, redirect, url_for, session, flash

from audio import AudioFanout
from mjpeg import MjpegStream
from picam import VideoGet
from pan_tilt import PanTiltServo
from tapo import TapoAudio, TapoCamera

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=1)

AUDIO_DEVICE = 1  # sounddevice index: USB PnP Sound Device (hw:3,0)
SAMPLE_RATE = 48000
CHUNK_SIZE = 960  # 20ms @ 48kHz

webrtc_loop = asyncio.new_event_loop()
threading.Thread(target=webrtc_loop.run_forever, daemon=True).start()
pcs = set()

# Pi USB 마이크 - 서버 시작 시 한 번만 열고 유지
pi_audio = AudioFanout()


def _pi_audio_callback(indata, frames, time_info, status):
    pi_audio.dispatch(indata.copy())


class MicrophoneTrack(AudioStreamTrack):
    kind = "audio"

    def __init__(self, source):
        super().__init__()
        self._queue = asyncio.Queue()
        self._pts = 0
        self._source = source
        source.add_listener(self._on_audio)

    def _on_audio(self, data):
        asyncio.run_coroutine_threadsafe(self._queue.put(data), webrtc_loop)

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
        self._source.remove_listener(self._on_audio)
        super().stop()


def api_login_required(view):
    """버튼에서 보내는 요청용: 로그인하지 않았으면 로그인 화면 대신 401로 거절"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "id" not in session:
            return "unauthorized", 401
        return view(*args, **kwargs)
    return wrapper


def tapo_ptz(method, *args):
    if tapo is None:
        return "tapo not configured", 503
    try:
        getattr(tapo, method)(*args)
    except Exception as e:
        return f"tapo error: {e}", 502
    return "ok"


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
        return render_template("get_cam.html", tapo_enabled=tapo is not None)
    else:
        flash("Please Login")
        return redirect(url_for("login"))


@app.route("/video_feed")
def video_feed():
    if "id" not in session:
        return redirect(url_for("login"))
    return Response(pi_stream.frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/video_feed/tapo")
def video_feed_tapo():
    if "id" not in session:
        return redirect(url_for("login"))
    if tapo is None:
        return "tapo not configured", 503
    return Response(tapo_stream.frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/offer", methods=["POST"])
def offer():
    if "id" not in session:
        return redirect(url_for("login"))

    params = request.json

    async def process():
        pc = RTCPeerConnection()
        source = tapo_audio if params.get("cam") == "tapo" and tapo_audio else pi_audio
        mic = MicrophoneTrack(source)
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
@api_login_required
def servo_center():
    if request.args.get("cam") == "tapo":
        return tapo_ptz("center")
    pan_tilt_servo.set_pan_angle(45)
    pan_tilt_servo.set_tilt_angle(45)
    return "ok"


@app.route("/servo/right")
@api_login_required
def servo_right():
    if request.args.get("cam") == "tapo":
        return tapo_ptz("move", "right", per_angle)
    angle = min(90, pan_tilt_servo.get_pan_angle() + per_angle)
    pan_tilt_servo.set_pan_angle(angle)
    return "ok"


@app.route("/servo/left")
@api_login_required
def servo_left():
    if request.args.get("cam") == "tapo":
        return tapo_ptz("move", "left", per_angle)
    angle = max(0, pan_tilt_servo.get_pan_angle() - per_angle)
    pan_tilt_servo.set_pan_angle(angle)
    return "ok"


@app.route("/servo/up")
@api_login_required
def servo_up():
    if request.args.get("cam") == "tapo":
        return tapo_ptz("move", "up", per_angle)
    angle = min(90, pan_tilt_servo.get_tilt_angle() + per_angle)
    pan_tilt_servo.set_tilt_angle(angle)
    return "ok"


@app.route("/servo/down")
@api_login_required
def servo_down():
    if request.args.get("cam") == "tapo":
        return tapo_ptz("move", "down", per_angle)
    angle = max(0, pan_tilt_servo.get_tilt_angle() - per_angle)
    pan_tilt_servo.set_tilt_angle(angle)
    return "ok"


@app.route("/set/angle", methods=["POST"])
@api_login_required
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
    tapo = None
    if os.environ.get("TAPO_IP") and os.environ.get("TAPO_USER"):
        tapo = TapoCamera(os.environ["TAPO_IP"], os.environ["TAPO_USER"], os.environ["TAPO_PASSWORD"]).start()
    pi_stream = MjpegStream(picam)
    tapo_stream = MjpegStream(tapo) if tapo else None
    tapo_audio = TapoAudio(tapo.rtsp_url) if tapo else None
    pan_tilt_servo = PanTiltServo()
    per_angle = 5

    global_audio_stream = sd.InputStream(
        device=AUDIO_DEVICE,
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="int16",
        blocksize=CHUNK_SIZE,
        callback=_pi_audio_callback,
    )
    global_audio_stream.start()

    # Tailscale HTTPS(tailscale serve)를 거친 요청만 받도록 라즈베리파이 내부에서만 열어둠
    # 접속 주소: https://raspberrypi.tailae04df.ts.net
    app.run(host="127.0.0.1", port="5000", debug=False, threaded=True)
