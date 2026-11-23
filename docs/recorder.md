# recorder.py — 24시간 녹화, 보관 정리, 녹화 목록

## 역할

- Pi 카메라와 Tapo 카메라를 **24시간 녹화**해서 USB(`/mnt/cctv/recordings/{pi,tapo}/`)에 10분 단위 MP4로 저장한다.
- **3일이 지난 파일**을 지우고, 남은 공간이 10GB보다 적으면 3일 안이라도 가장 오래된 것부터 지운다.
- 웹 녹화 페이지에 보여줄 **목록**(움직임 표시 포함)과 파일 경로를 제공한다.
- 서비스가 꺼질 때 녹화 중이던 파일을 **제대로 닫는다** (안 그러면 재생 불가).

## 코드 구조

| 이름 | 하는 일 |
|---|---|
| `storage_ready()` | `/mnt/cctv`에 USB가 연결돼 있는지. 없으면 녹화하지 않음 (SD 카드 보호) |
| `segment_args(cam)` | 10분 단위로 파일을 나누는 ffmpeg 옵션 |
| `TapoRecorder` | Tapo RTSP를 ffmpeg로 그대로 저장. 끊기면 5초 뒤 다시 시작, 끊긴 이유 기록 |
| `FfmpegAudioOutput` | Picamera2 출력 클래스 확장: H.264 영상 + 마이크 소리를 함께 ffmpeg로 |
| `PiRecorder` | Pi 카메라 하드웨어 인코더로 녹화. 카메라 복구 중엔 잠깐 멈췄다 이어감. 같은 인코더 출력을 실시간 보기에도 나눠 줌 |
| `LiveVideoOutput` | 인코더가 만든 H.264 프레임을 실시간 보기(`H264Fanout`)로 넘기는 Picamera2 출력 |
| `stop_all()` | 서비스 종료 시 모든 녹화를 정상 종료 |
| `list_recordings(cam)` | 녹화가 끝난 재생 가능한 파일 목록 + 움직임 여부 |
| `recording_path(cam, name)` | 웹이 요청한 파일 이름을 검증해서 경로로 바꿈 |
| `RecordingCleaner` | 10분마다 오래된 파일 정리 |

## 사용 라이브러리와 선택 이유

| 라이브러리/도구 | 용도 | 선택 이유 |
|---|---|---|
| **ffmpeg** (외부 프로그램) | RTSP 수신, 파일 저장, 10분 분할, 소리 변환 | 영상 다시 인코딩 없이 복사(`-c copy`)할 수 있고, segment 기능으로 분할이 쉬움 |
| **Picamera2 `H264Encoder`** | Pi 카메라 영상을 하드웨어로 H.264 압축 | 라즈베리파이 4의 하드웨어 인코더라 CPU 부담이 거의 없음 (ffmpeg 1%) |
| **python-prctl** | 앱이 죽으면 ffmpeg도 정리 | Linux `PR_SET_PDEATHSIG` 설정 (아래 참고) |

## 동작 원리

### 1. 왜 영상을 다시 압축하지 않나

- **Tapo**: 카메라가 이미 H.264로 압축해서 보내준다. 그대로 파일에 옮겨 담기만 하면(`-c:v copy`) CPU를 약 3%만 쓴다. 다시 압축하면 라즈베리파이에 큰 부담이다.
- **Pi 카메라**: 라즈베리파이 4의 하드웨어 H.264 인코더를 쓴다. CPU로 압축하지 않는다.

측정한 비트레이트는 Pi 약 1.2Mbps(하루 약 13GB), Tapo 고화질 약 1.1Mbps(조용할 때 하루 약 12GB, 움직임·야간엔 늘어남). 3일 보관이면 보통 75GB 정도로 112GB USB에 들어간다.

### 2. 10분 단위로 나누기 (ffmpeg segment)

```
-f segment -segment_time 600 -segment_atclocktime 1 -reset_timestamps 1 -strftime 1 .../%Y%m%d_%H%M%S.mp4
```

- `segment_time 600` + `segment_atclocktime 1`: 정각 기준 10분(:00, :10, :20 …)에 파일을 바꾼다.
- `strftime 1`: 파일 이름을 시작 시각으로 (`20261106_213000.mp4`).
- `reset_timestamps 1`: 각 파일이 0초부터 시작해 따로 재생 가능.

**faststart를 쓰지 않는 이유**: MP4는 재생 정보(moov, 목차)가 있어야 재생된다. `movflags=+faststart`는 파일을 닫을 때 moov를 앞으로 옮기려고 파일 전체(약 100MB)를 다시 쓰는데, 그동안 ffmpeg가 5~10초 막혀 Pi 카메라까지 멈췄다 (`docs/troubleshooting.md` 1번). 지금은 moov가 파일 끝에 붙고, 브라우저는 HTTP Range 요청으로 끝부분을 먼저 읽어 재생한다.

### 2-1. Tapo 녹화가 끊길 때

- ffmpeg 오류 출력(stderr)은 RTSP 주소를 그대로 찍는데, 주소에 카메라 계정이 들어 있다. 그래서 서비스 로그로 바로 보내지 않고 앱이 받아서 계정을 `rtsp://***@`로 가린 뒤 마지막 몇 줄만 보관한다.
- 처음 끊긴 순간에만 한 줄 기록한다: `tapo.probe_rtsp`로 구한 이유 + 가린 ffmpeg 메시지 (예: `Connection timed out`).
  - 카메라가 네트워크에서 사라진 경우는 실시간 영상 쪽이 같은 원인을 경고로 남기므로 일반 기록(INFO)으로만.
  - 카메라는 응답하는데 녹화만 실패하면 경고(WARNING).
- 끊긴 동안의 재시도(5초마다)는 기록하지 않는다. 다시 시작한 녹화가 15초 넘게 이어지면 "다시 시작됨 (N초 끊김, 재시도 M번)"을 한 줄 남긴다.

### 3. Pi 녹화: 영상과 소리를 하나로

Picamera2의 `FfmpegOutput`은 인코더가 만든 H.264를 ffmpeg 표준 입력으로 넘긴다. 여기에 소리를 더하려고 `FfmpegAudioOutput`을 만들었다.

- USB 마이크는 실시간 듣기용으로 이미 열려 있어서 ffmpeg가 다시 열 수 없다 (마이크는 한 번에 한 곳만 열림).
- 그래서 앱이 받은 소리(48kHz, mono, int16)를 `AudioFanout`에서 나눠 받아, **파이프**로 ffmpeg에 넘긴다.

```
ffmpeg  -use_wallclock_as_timestamps 1 -i -          ← 영상 (stdin)
        -f s16le -ar 48000 -ac 1
        -use_wallclock_as_timestamps 1 -i pipe:<fd>   ← 소리 (파이프)
        -map 0:v -map 1:a -c:v copy -af aresample=async=1 -c:a aac ...
```

- 원시 H.264와 원시 PCM에는 시간 정보가 없어서, ffmpeg가 **받은 시각**을 각 프레임 시간으로 쓰게 한다(`use_wallclock_as_timestamps`). 영상과 소리가 같은 시계를 쓰므로 며칠 녹화해도 어긋나지 않는다.
- `aresample=async=1`: 소리 조각이 조금 늦거나 빨리 와도 시간에 맞게 늘이고 줄인다.
- 파이프 쓰기는 non-blocking(`O_NONBLOCK`)으로 연다. 녹화 쪽이 밀려도 **마이크 콜백(실시간 듣기)이 막히지 않게** 하고, 그때는 그 조각만 녹화에서 버린다. 한 조각(1920바이트)은 파이프 원자적 쓰기 한계(4096바이트)보다 작아서 반만 쓰이는 일은 없다.

### 3-1. 실시간 보기와 인코더 나눠 쓰기

Picamera2 인코더에는 출력을 리스트로 줄 수 있다. `[LiveVideoOutput, FfmpegAudioOutput]`를 줘서 한 번 압축한 H.264를 녹화와 실시간 보기(WebRTC)가 같이 쓴다 (`docs/webrtc_video.md`).

- USB가 없으면 `LiveVideoOutput`만 붙여서 인코더를 계속 돌리고, USB가 다시 준비되면 녹화를 붙여 인코더를 다시 시작한다.
- 실시간 보기는 키프레임부터 시작할 수 있어서 키프레임 간격을 30프레임(약 1.8초, 기본값은 약 3.6초)으로 줄였다. 비트레이트가 같아서 파일 크기는 그대로다.

### 4. 서비스가 꺼질 때 파일 닫기

MP4는 녹화를 끝낼 때 moov를 써야 재생된다. ffmpeg를 강제로 죽이면 파일이 깨진다.

- systemd unit의 `KillMode=mixed`: 종료 신호(SIGTERM)를 **앱에만** 보낸다.
- 앱이 신호를 받으면 `stop_all()`:
  - Tapo: ffmpeg에 SIGINT를 **한 번만** 보내고 끝날 때까지(최대 20초) 기다림. ffmpeg는 종료 신호를 3번 넘게 받으면 정리 없이 꺼진다.
  - Pi: 인코더를 멈추면 ffmpeg 입력이 닫히고 ffmpeg가 파일을 정리한 뒤 끝남.
- 앱이 갑자기 죽는 경우를 대비해 ffmpeg를 띄울 때 `prctl.set_pdeathsig(SIGTERM)`: 부모(앱)가 죽으면 커널이 ffmpeg에 SIGTERM을 보낸다.

정전처럼 아무 신호 없이 꺼지면 녹화 중이던 파일은 moov가 없어 재생 불가다. 이런 파일은 목록에서 숨긴다(아래).

### 5. 카메라 복구와 녹화

`picam.VideoGet`이 멈춘 카메라를 다시 열 때 `PiRecorder.pause()`(인코더 멈춤) → 카메라 교체 → `resume()`이 불린다. 녹화 루프는 `camera_lock`을 잡은 상태에서만 인코더를 시작해서, 카메라를 바꾸는 도중에 옛 카메라를 건드리지 않는다.

### 6. 녹화 목록 만들기

`list_recordings(cam)`:
1. 폴더에서 `YYYYMMDD_HHMMSS.mp4` 이름의 파일을 찾는다.
2. 최근 30초 안에 수정된 파일은 **아직 녹화 중**이라 뺀다.
3. **재생 가능한지 검사**: MP4는 "상자(box)"가 이어진 구조다. 각 상자 앞 8바이트는 `[크기 4바이트][이름 4바이트]`. 처음부터 상자 크기만큼 건너뛰며 `moov`가 있는지 찾는다. 없으면(녹화 중 정전) 목록에서 뺀다. 결과는 (경로, 수정 시각, 크기) 기준으로 캐시한다.
4. 파일의 시작~끝 시각 사이 각 분(minute)이 움직임 기록(`motion.motion_minutes`)에 있으면 `motion: true`.

`recording_path()`는 카메라 이름이 `pi`/`tapo`이고 파일 이름이 정해진 형식일 때만 경로를 만든다. `../../.env` 같은 요청은 거절된다.

### 7. 보관 정리 (`RecordingCleaner`)

10분마다:
1. 3일(`KEEP_DAYS`)보다 오래된 파일을 지운다.
2. 그래도 남은 공간이 10GB(`MIN_FREE_BYTES`)보다 적으면 두 카메라 통틀어 가장 오래된 파일부터 지운다.
3. 각 카메라의 가장 최근 파일은 녹화 중일 수 있어서 지우지 않는다.
4. 움직임 기록 파일도 같이 정리한다.

## 설정값과 근거

| 상수 | 값 | 근거 |
|---|---|---|
| `SEGMENT_SECONDS` | 600 | 웹에서 고르기 쉬운 단위, 정전 시 잃는 양을 최대 10분으로 제한 |
| `KEEP_DAYS` | 3 | 사용자 요구. 112GB USB에 두 카메라 3일 |
| `MIN_FREE_BYTES` | 10GB | 움직임 많은 날 Tapo 용량이 2~3배로 늘 수 있어 안전장치 |
| `PI_BITRATE` | 1.5Mbps | 720p 실내 장면에 충분, 실측 약 1.2Mbps. 실시간 보기도 같은 화질 |
| `PI_KEYFRAME_INTERVAL` | 30프레임 | 실시간 보기 첫 화면 대기를 최대 약 1.8초로 |
| Tapo 스트림 | stream1 (2304x1296) | 사용자 선택(고화질). 실시간 화면은 stream2(720p) |
| Tapo 소리 | AAC 32kbps | G.711 A-law는 MP4에 못 넣어서 변환 |

## 한계

- 정전 시 녹화 중이던 최대 10분은 잃는다.
- USB 메모리는 24시간 쓰기에 약하다. 오래 쓰려면 USB SSD를 권장한다.
- 움직임 표시는 분 단위라, 10분 파일 안에서 정확한 위치까지는 알려주지 않는다.
