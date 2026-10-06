import fcntl
import os
import re
import shutil
import signal
import struct
import subprocess
import time
from datetime import datetime, timedelta
from threading import Thread

import prctl
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput
from picamera2.outputs.output import Output

RECORD_MOUNT = os.environ.get("RECORD_MOUNT", "/mnt/cctv")
RECORD_ROOT = os.path.join(RECORD_MOUNT, "recordings")
CAMERAS = ("pi", "tapo")

SEGMENT_SECONDS = 600  # 10분 단위 파일
KEEP_DAYS = 3
MIN_FREE_BYTES = 10 * 1024 ** 3  # 남은 공간이 10GB 아래면 3일이 안 됐어도 오래된 것부터 삭제
PI_BITRATE = 1_500_000
PI_AUDIO_RATE = 48000

FILENAME_RE = re.compile(r"^(\d{8}_\d{6})\.mp4$")
WRITING_GRACE_SECONDS = 30  # 이 시간 안에 수정된 파일은 아직 녹화 중으로 보고 목록에서 뺌


def storage_ready():
    """USB가 연결돼 있을 때만 녹화 (빠져 있으면 SD 카드에 쓰지 않음)"""
    return os.path.ismount(RECORD_MOUNT)


def camera_dir(cam):
    return os.path.join(RECORD_ROOT, cam)


def segment_args(cam):
    # 10분 단위(정각 기준)로 나눠서 저장, 파일 이름은 시작 시각
    return [
        "-f", "segment",
        "-segment_time", str(SEGMENT_SECONDS),
        "-segment_atclocktime", "1",
        "-reset_timestamps", "1",
        "-strftime", "1",
        "-segment_format_options", "movflags=+faststart",
        os.path.join(camera_dir(cam), "%Y%m%d_%H%M%S.mp4"),
    ]


_recorders = []


def stop_all():
    """서비스 종료 시 호출: 녹화 중인 파일을 제대로 닫고 끝냄"""
    for rec in _recorders:
        try:
            rec.stop()
        except Exception as e:
            print(f"recorder stop error: {e}", flush=True)


def _kill_with_parent():
    # 앱이 꺼지면 ffmpeg도 정상 종료(SIGTERM)시켜서 녹화 중이던 파일을 제대로 닫게 함
    # (SIGKILL로 죽이면 MP4 목차(moov)가 안 써져서 재생 불가)
    prctl.set_pdeathsig(signal.SIGTERM)


class TapoRecorder:
    """Tapo 고화질 스트림을 다시 인코딩하지 않고 그대로 파일에 저장 (소리는 AAC로 변환)"""

    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self._proc = None
        self._stopping = False

    def start(self):
        _recorders.append(self)
        Thread(target=self._run, daemon=True).start()
        return self

    def stop(self):
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            # 종료 요청은 한 번만 (여러 번 받으면 ffmpeg가 파일 정리 없이 바로 꺼짐)
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _run(self):
        while not self._stopping:
            if not storage_ready():
                time.sleep(30)
                continue
            os.makedirs(camera_dir("tapo"), exist_ok=True)
            cmd = [
                "ffmpeg", "-nostdin", "-loglevel", "error",
                "-rtsp_transport", "tcp", "-timeout", "10000000",
                "-i", self.rtsp_url,
                "-c:v", "copy", "-c:a", "aac", "-b:a", "32k",
            ] + segment_args("tapo")
            self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, preexec_fn=_kill_with_parent)
            self._proc.wait()
            # 연결이 끊기거나 USB가 빠지면 잠시 후 다시 시작
            time.sleep(5)


class FfmpegAudioOutput(FfmpegOutput):
    """Picamera2 H.264 영상(stdin) + 마이크 소리(파이프)를 함께 ffmpeg로 녹화

    마이크는 실시간 듣기용으로 이미 열려 있어서 다시 열 수 없으므로,
    AudioFanout으로 나눠 받은 소리(48kHz mono int16)를 파이프로 넘겨줌
    """

    def __init__(self, output_args, audio_source):
        super().__init__(output_args)
        self.audio_source = audio_source
        self._audio_w = None
        self.timeout = 20  # 종료 시 ffmpeg가 파일을 닫을 때까지 기다리는 최대 시간

    def start(self):
        r, w = os.pipe()
        # 녹화 쪽이 밀려도 실시간 듣기(마이크 콜백)가 막히지 않도록 쓰기를 non-blocking으로
        fcntl.fcntl(w, fcntl.F_SETFL, fcntl.fcntl(w, fcntl.F_GETFL) | os.O_NONBLOCK)
        cmd = [
            "ffmpeg", "-loglevel", "error", "-y",
            # 영상과 소리 모두 실제 시각 기준으로 맞춰서 오래 녹화해도 어긋나지 않게 함
            "-use_wallclock_as_timestamps", "1", "-thread_queue_size", "64", "-i", "-",
            "-f", "s16le", "-ar", str(PI_AUDIO_RATE), "-ac", "1",
            "-use_wallclock_as_timestamps", "1", "-thread_queue_size", "1024", "-i", f"pipe:{r}",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-af", "aresample=async=1", "-c:a", "aac", "-b:a", "48k",
        ] + self.output_filename.split()
        self.ffmpeg = subprocess.Popen(cmd, stdin=subprocess.PIPE, pass_fds=(r,), preexec_fn=_kill_with_parent)
        os.close(r)
        self._audio_w = w
        self.audio_source.add_listener(self._on_audio)
        Output.start(self)

    def _on_audio(self, data):
        w = self._audio_w
        if w is None:
            return
        try:
            os.write(w, data.tobytes())
        except (BlockingIOError, BrokenPipeError, OSError):
            pass  # 녹화가 밀리거나 끝난 경우 이 조각만 버림

    def stop(self):
        self.audio_source.remove_listener(self._on_audio)
        w, self._audio_w = self._audio_w, None
        if w is not None:
            os.close(w)
        super().stop()


class PiRecorder:
    """Pi 카메라를 하드웨어 H.264 인코더로 녹화 (실시간 화면 캡처와 동시에 동작)
    소리는 USB 마이크(실시간 듣기와 같은 소스)에서 함께 녹음"""

    def __init__(self, video_get, audio_source):
        self.picam2 = video_get.picam2
        self.audio_source = audio_source
        self._stopping = False

    def start(self):
        _recorders.append(self)
        Thread(target=self._run, daemon=True).start()
        return self

    def stop(self):
        self._stopping = True
        try:
            # 인코더를 멈추면 ffmpeg 입력이 닫히고, ffmpeg가 파일을 정리한 뒤 끝남
            self.picam2.stop_encoder()
        except Exception:
            pass

    def _run(self):
        while not self._stopping:
            if not storage_ready():
                time.sleep(30)
                continue
            os.makedirs(camera_dir("pi"), exist_ok=True)
            output = FfmpegAudioOutput(" ".join(segment_args("pi")), self.audio_source)
            try:
                self.picam2.start_encoder(H264Encoder(bitrate=PI_BITRATE), output)
            except Exception:
                time.sleep(30)
                continue
            # ffmpeg가 끝나면(USB 빠짐 등) 인코더를 멈추고 다시 시작
            while not self._stopping and output.ffmpeg is not None and output.ffmpeg.poll() is None:
                time.sleep(1)
            if self._stopping:
                return
            try:
                self.picam2.stop_encoder()
            except Exception:
                pass
            time.sleep(5)


def _parse_start(name):
    m = FILENAME_RE.match(name)
    return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S") if m else None


def _segments(cam):
    """(시작 시각, 경로) 목록, 오래된 순"""
    d = camera_dir(cam)
    if not os.path.isdir(d):
        return []
    items = []
    for name in os.listdir(d):
        start = _parse_start(name)
        if start:
            items.append((start, os.path.join(d, name)))
    return sorted(items)


_playable_cache = {}


def _is_playable(path, st):
    """MP4 목차(moov)가 있는지 확인. 정전 등으로 녹화 중 끊긴 파일은 목차가 없어서 재생 불가"""
    key = (path, st.st_mtime, st.st_size)
    if key in _playable_cache:
        return _playable_cache[key]
    playable = False
    try:
        with open(path, "rb") as f:
            pos = 0
            while pos + 8 <= st.st_size:
                f.seek(pos)
                size, box = struct.unpack(">I4s", f.read(8))
                if box == b"moov":
                    playable = True
                    break
                if size == 1:
                    size = struct.unpack(">Q", f.read(8))[0]
                elif size == 0:
                    break
                if size < 8:
                    break
                pos += size
    except (OSError, struct.error):
        pass
    _playable_cache[key] = playable
    return playable


def list_recordings(cam):
    """웹에 보여줄 녹화 목록 (녹화가 끝난 파일만), 움직임이 있었던 구간은 motion=True"""
    from motion import motion_minutes

    now = time.time()
    segments = _segments(cam)
    minutes = motion_minutes(cam, {start.strftime("%Y%m%d") for start, _ in segments})
    result = []
    for start, path in segments:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            continue
        if now - st.st_mtime < WRITING_GRACE_SECONDS or st.st_size == 0:
            continue
        if not _is_playable(path, st):
            continue
        end = datetime.fromtimestamp(st.st_mtime)
        t = start.replace(second=0)
        motion = False
        while t <= end:
            if t.strftime("%Y%m%d%H%M") in minutes:
                motion = True
                break
            t += timedelta(minutes=1)
        result.append({
            "name": os.path.basename(path),
            "start": start.isoformat(),
            "end": end.isoformat(timespec="seconds"),
            "size": st.st_size,
            "motion": motion,
        })
    return result


def recording_path(cam, name):
    if cam not in CAMERAS or not FILENAME_RE.match(name):
        return None
    path = os.path.join(camera_dir(cam), name)
    return path if os.path.isfile(path) else None


class RecordingCleaner:
    """3일이 지난 영상을 지우고, 공간이 모자라면 3일 안이라도 가장 오래된 것부터 지움"""

    def __init__(self, interval=600):
        self.interval = interval

    def start(self):
        Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        while True:
            if storage_ready():
                try:
                    self.clean()
                except Exception as e:
                    print(f"recording cleaner error: {e}", flush=True)
            time.sleep(self.interval)

    def clean(self):
        from motion import clean_motion_logs

        clean_motion_logs()
        cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
        # 각 카메라의 가장 최근 파일은 녹화 중일 수 있어서 지우지 않음
        candidates = []
        for cam in CAMERAS:
            candidates += _segments(cam)[:-1]
        candidates.sort()

        for start, path in list(candidates):
            if start < cutoff:
                self._remove(path)
                candidates.remove((start, path))

        while candidates and shutil.disk_usage(RECORD_MOUNT).free < MIN_FREE_BYTES:
            _, path = candidates.pop(0)
            self._remove(path)

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
