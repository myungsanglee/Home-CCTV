import fcntl
import logging
import os
import re
import shutil
import signal
import struct
import subprocess
import time
from collections import deque
from datetime import datetime, timedelta
from threading import Thread
from urllib.parse import urlsplit

import prctl
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput
from picamera2.outputs.output import Output

from applog import redact, supervised_thread
from tapo import camera_offline, probe_rtsp

log = logging.getLogger("recorder")

RECORD_MOUNT = os.environ.get("RECORD_MOUNT", "/mnt/cctv")
RECORD_ROOT = os.path.join(RECORD_MOUNT, "recordings")
CAMERAS = ("pi", "tapo")

SEGMENT_SECONDS = 600  # 10분 단위 파일
KEEP_DAYS = 3
MIN_FREE_BYTES = 10 * 1024 ** 3  # 남은 공간이 10GB 아래면 3일이 안 됐어도 오래된 것부터 삭제
PI_BITRATE = 1_500_000
# 키프레임(화면 전체) 간격, 프레임 수. 실시간 보기는 키프레임부터 시작할 수 있어서 간격이 곧 첫 화면 대기 시간.
# 기본값(60프레임 = 약 3.6초)을 30프레임(약 1.8초)으로 줄임. 비트레이트가 같아서 파일 크기는 그대로
PI_KEYFRAME_INTERVAL = 30
PI_AUDIO_RATE = 48000
TAPO_RECOVERED_AFTER = 15  # 다시 시작한 Tapo 녹화가 이 시간(초) 넘게 이어지면 복구된 것으로 봄

FILENAME_RE = re.compile(r"^(\d{8}_\d{6})\.mp4$")
WRITING_GRACE_SECONDS = 30  # 이 시간 안에 수정된 파일은 아직 녹화 중으로 보고 목록에서 뺌


def storage_ready():
    """USB가 연결돼 있을 때만 녹화 (빠져 있으면 SD 카드에 쓰지 않음)"""
    return os.path.ismount(RECORD_MOUNT)


def camera_dir(cam):
    return os.path.join(RECORD_ROOT, cam)


def segment_args(cam):
    # 10분 단위(정각 기준)로 나눠서 저장, 파일 이름은 시작 시각
    # faststart(파일을 닫을 때 재생 정보를 앞으로 옮기려고 파일 전체를 다시 쓰는 작업)는 쓰지 않음:
    # 10분 파일(약 100MB)을 USB에 다시 쓰느라 ffmpeg가 5~10초 멈추고, 그동안 Pi 카메라 인코더가 막혀
    # 실시간 화면 프레임까지 멈춤 (카메라 멈춤 감시가 10분마다 오작동한 원인).
    # 재생 정보가 파일 끝에 있어도 브라우저는 Range 요청으로 끝부분을 먼저 읽어서 재생함
    return [
        "-f", "segment",
        "-segment_time", str(SEGMENT_SECONDS),
        "-segment_atclocktime", "1",
        "-reset_timestamps", "1",
        "-strftime", "1",
        os.path.join(camera_dir(cam), "%Y%m%d_%H%M%S.mp4"),
    ]


_recorders = []


def stop_all():
    """서비스 종료 시 호출: 녹화 중인 파일을 제대로 닫고 끝냄"""
    for rec in _recorders:
        try:
            rec.stop()
        except Exception:
            log.exception("녹화 종료 처리 실패")


def _kill_with_parent():
    # 앱이 꺼지면 ffmpeg도 정상 종료(SIGTERM)시켜서 녹화 중이던 파일을 제대로 닫게 함
    # (SIGKILL로 죽이면 MP4 목차(moov)가 안 써져서 재생 불가)
    prctl.set_pdeathsig(signal.SIGTERM)


_storage_warned = {"at": 0.0}


def _warn_no_storage_once_per_hour():
    if time.time() - _storage_warned["at"] > 3600:
        _storage_warned["at"] = time.time()
        log.warning(f"녹화용 USB({RECORD_MOUNT})가 연결돼 있지 않아 녹화하지 않음")


class TapoRecorder:
    """Tapo 고화질 스트림을 다시 인코딩하지 않고 그대로 파일에 저장 (소리는 AAC로 변환)"""

    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self._proc = None
        self._stopping = False

    def start(self):
        _recorders.append(self)
        supervised_thread("tapo-recorder", self._run)
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

    @staticmethod
    def _read_errors(proc, errors):
        # ffmpeg 오류 출력은 RTSP 주소(카메라 계정 포함)를 그대로 찍어서, 서비스 로그로 바로 보내지 않고
        # 앱이 받아서 계정을 가린 뒤 마지막 몇 줄만 끊긴 이유로 씀
        for line in proc.stderr:
            line = redact(line.strip())
            if line:
                errors.append(line)

    def _run(self):
        host = urlsplit(self.rtsp_url).hostname
        down_since, retries = None, 0
        while not self._stopping:
            if not storage_ready():
                _warn_no_storage_once_per_hour()
                time.sleep(30)
                continue
            os.makedirs(camera_dir("tapo"), exist_ok=True)
            cmd = [
                "ffmpeg", "-nostdin", "-loglevel", "error",
                "-rtsp_transport", "tcp", "-timeout", "10000000",
                "-i", self.rtsp_url,
                "-c:v", "copy", "-c:a", "aac", "-b:a", "32k",
            ] + segment_args("tapo")
            self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                          text=True, errors="replace", preexec_fn=_kill_with_parent)
            errors = deque(maxlen=5)
            reader = Thread(target=self._read_errors, args=(self._proc, errors), daemon=True)
            reader.start()
            try:
                code = self._proc.wait(timeout=TAPO_RECOVERED_AFTER)
            except subprocess.TimeoutExpired:
                # 녹화가 이어지고 있음: 끊겨 있었다면 복구 기록을 한 줄 남김
                if down_since is not None:
                    log.info(f"Tapo 녹화 다시 시작됨 ({time.time() - down_since:.0f}초 끊김, 재시도 {retries}번)")
                    down_since, retries = None, 0
                code = self._proc.wait()
            reader.join(timeout=2)
            if self._stopping:
                break
            # 끊긴 동안 5초마다 하는 재시도는 기록하지 않고, 처음 끊긴 순간만 이유와 함께 기록
            if down_since is None:
                down_since = time.time()
                reason = probe_rtsp(host)
                detail = errors[-1] if errors else f"종료 코드 {code}"
                message = f"Tapo 녹화가 끊김: {reason} (ffmpeg: {detail})"
                if camera_offline(reason):
                    # 카메라가 네트워크에서 사라진 경우는 실시간 영상 쪽(tapo)이 같은 원인을 경고로 남김
                    log.info(message)
                else:
                    log.warning(message)
            else:
                retries += 1
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
    소리는 USB 마이크(실시간 듣기와 같은 소스)에서 함께 녹음

    카메라가 멈춰서 VideoGet이 카메라를 다시 열 때는 녹화를 잠깐 멈췄다가 새 카메라로 이어서 녹화함
    녹화용 USB가 없어도 실시간 보기를 위해 인코더는 계속 돌림
    """

    def __init__(self, video_get, audio_source, live_video):
        self.video_get = video_get
        self.audio_source = audio_source
        # 같은 H.264를 실시간 보기(WebRTC)에도 나눠 줌. 인코더가 하나라 압축은 한 번만 함
        self.live_output = LiveVideoOutput(live_video)
        self._stopping = False
        self._paused = False
        video_get.before_restart.append(self.pause)
        video_get.after_restart.append(self.resume)

    def start(self):
        _recorders.append(self)
        supervised_thread("pi-recorder", self._run)
        return self

    def _stop_encoder(self):
        try:
            # 인코더를 멈추면 ffmpeg 입력이 닫히고, ffmpeg가 파일을 정리한 뒤 끝남
            self.video_get.picam2.stop_encoder()
        except Exception:
            pass

    def stop(self):
        self._stopping = True
        self._stop_encoder()

    def pause(self):
        self._paused = True
        self._stop_encoder()

    def resume(self):
        self._paused = False

    def _run(self):
        while not self._stopping:
            if self._paused:
                time.sleep(1)
                continue
            outputs = [self.live_output]
            recording = None
            if storage_ready():
                os.makedirs(camera_dir("pi"), exist_ok=True)
                recording = FfmpegAudioOutput(" ".join(segment_args("pi")), self.audio_source)
                outputs.append(recording)
            with self.video_get.camera_lock:
                if self._paused:
                    continue
                picam2 = self.video_get.picam2
                try:
                    picam2.start_encoder(H264Encoder(bitrate=PI_BITRATE, iperiod=PI_KEYFRAME_INTERVAL), outputs)
                except Exception:
                    picam2 = None
            if picam2 is None:
                time.sleep(30)
                continue
            if recording is not None:
                # ffmpeg가 끝나면(USB 빠짐, 카메라 재시작 등) 인코더를 멈추고 다시 시작
                while not self._stopping and not self._paused and recording.ffmpeg is not None and recording.ffmpeg.poll() is None:
                    time.sleep(1)
            else:
                # USB가 없는 동안은 실시간 보기만: USB가 다시 준비되면 녹화를 붙여서 다시 시작
                while not self._stopping and not self._paused and not storage_ready():
                    time.sleep(30)
            if self._stopping:
                return
            if recording is not None and not self._paused:
                code = recording.ffmpeg.poll() if recording.ffmpeg is not None else None
                log.warning(f"Pi 녹화(ffmpeg)가 끝남 (종료 코드 {code}), 5초 뒤 다시 시작")
            with self.video_get.camera_lock:
                try:
                    picam2.stop_encoder()
                except Exception:
                    pass
            if recording is not None and not self._paused:
                time.sleep(5)


class LiveVideoOutput(Output):
    """Picamera2 인코더가 만든 H.264 프레임을 실시간 보기(H264Fanout)로 넘김"""

    def __init__(self, fanout):
        super().__init__()
        self.fanout = fanout

    def outputframe(self, frame, keyframe=True, timestamp=None, packet=None, audio=False):
        if not audio and self.fanout.has_listeners():
            self.fanout.dispatch((bytes(frame), keyframe))


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
        supervised_thread("recording-cleaner", self._run)
        return self

    def _run(self):
        while True:
            if storage_ready():
                try:
                    self.clean()
                except Exception:
                    log.exception("오래된 녹화 정리 실패")
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
