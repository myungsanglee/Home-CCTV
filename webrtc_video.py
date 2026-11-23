"""실시간 영상 WebRTC 전송: 카메라 쪽에서 이미 압축된 H.264를 다시 압축하지 않고 그대로 보냄

- Pi 카메라: 녹화용 하드웨어 인코더가 만든 H.264를 녹화와 나눠 씀 (recorder.PiRecorder)
- Tapo: 카메라가 RTSP로 보내는 H.264를 그대로 전달 (tapo.TapoLive)

MJPEG(사진 연속 전송)는 매 장면을 사진 한 장으로 따로 압축해서 720p 한 화면에 50Mbps 가까이 썼다.
H.264는 바뀐 부분만 보내서 같은 화면이 0.2~1.5Mbps로 줄어든다 (docs/webrtc_video.md).
"""
import asyncio
import fractions
import time

import av
from aiortc import MediaStreamTrack, RTCRtpSender

from audio import AudioFanout

VIDEO_CLOCK = 90000  # RTP 영상 타임스탬프 단위 (H.264는 90kHz로 정해져 있음)
QUEUE_FRAMES = 30    # 연결 하나에 쌓아둘 최대 프레임 수 (약 2초). 넘으면 밀린 것으로 보고 버림


class H264Fanout(AudioFanout):
    """압축된 H.264 프레임 (bytes, 키프레임 여부)을 보고 있는 모든 연결에 나눠 보냄
    리스너 관리는 AudioFanout과 같고, 넘기는 값만 다름"""


class EncodedVideoTrack(MediaStreamTrack):
    """H264Fanout에서 받은 프레임을 aiortc에 av.Packet으로 넘김

    aiortc는 track.recv()가 영상 프레임(av.VideoFrame)을 주면 직접 압축하고,
    이미 압축된 av.Packet을 주면 압축 없이 RTP 패킷으로 나누기만 한다.
    라즈베리파이 CPU로 720p를 소프트웨어 압축하는 것은 너무 무거워서 후자를 쓴다.
    """

    kind = "video"

    def __init__(self, source, loop):
        super().__init__()
        self._source = source
        self._loop = loop
        self._queue = asyncio.Queue(maxsize=QUEUE_FRAMES)
        # 키프레임(화면 전체) 없이 차이 프레임부터 받으면 화면이 깨져서, 첫 키프레임부터 보냄
        self._waiting_keyframe = True
        self._start = None
        source.add_listener(self._on_frame)

    def _on_frame(self, item):
        # 카메라/인코더 스레드에서 불림 -> WebRTC 이벤트 루프로 넘김
        self._loop.call_soon_threadsafe(self._put, item, time.monotonic())

    def _put(self, item, received):
        data, keyframe = item
        if self._queue.full():
            # 네트워크가 느려서 밀림: 쌓인 프레임을 버리고 다음 키프레임부터 다시 보냄
            # (차이 프레임만 몇 장 빼고 보내면 다음 키프레임까지 화면이 깨짐)
            while not self._queue.empty():
                self._queue.get_nowait()
            self._waiting_keyframe = True
        if self._waiting_keyframe:
            if not keyframe:
                return
            self._waiting_keyframe = False
        self._queue.put_nowait((data, received))

    async def recv(self):
        data, received = await self._queue.get()
        if self._start is None:
            self._start = received
        packet = av.Packet(data)
        # 받은 시각 기준 타임스탬프: 카메라가 잠깐 끊겼다 이어져도 시간이 거꾸로 가지 않음
        packet.pts = packet.dts = int((received - self._start) * VIDEO_CLOCK)
        packet.time_base = fractions.Fraction(1, VIDEO_CLOCK)
        return packet

    def stop(self):
        self._source.remove_listener(self._on_frame)
        super().stop()


def prefer_h264(pc):
    """영상 코덱을 H.264로 고정 (브라우저는 VP8 등을 먼저 제안하지만, 그대로 보낼 수 있는 건 H.264뿐)"""
    codecs = [c for c in RTCRtpSender.getCapabilities("video").codecs
              if c.mimeType in ("video/H264", "video/rtx")]
    for transceiver in pc.getTransceivers():
        if transceiver.kind == "video":
            transceiver.setCodecPreferences(codecs)


def has_sps(data):
    """H.264 프레임 안에 SPS(해상도 등 재생에 필요한 설정)가 들어 있는지"""
    i = data.find(b"\x00\x00\x01")
    while i >= 0 and i + 3 < len(data):
        if data[i + 3] & 0x1F == 7:
            return True
        i = data.find(b"\x00\x00\x01", i + 3)
    return False
