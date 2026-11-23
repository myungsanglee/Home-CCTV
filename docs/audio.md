# audio.py + 실시간 소리 듣기 (WebRTC)

## 역할

브라우저에서 소리 버튼을 누르면 카메라 근처의 소리를 실시간으로 듣는다.
- Pi 카메라 탭: 라즈베리파이 USB 마이크
- Tapo 탭: Tapo 카메라 마이크

관련 코드는 세 곳에 나뉘어 있다.

| 위치 | 하는 일 |
|---|---|
| `audio.py` `AudioFanout` | 소리 조각을 듣고 있는 모든 곳에 나눠 줌 |
| `app.py` `MicrophoneTrack`, `/offer` | 나눠 받은 소리를 WebRTC 오디오 트랙으로 브라우저에 보냄 |
| `tapo.py` `TapoLive` | Tapo RTSP에서 소리를 꺼내 `AudioFanout`으로 공급 (같은 연결로 실시간 영상도 공급) |
| `templates/get_cam.html` `toggleAudio()` | 브라우저 쪽 WebRTC 연결, iOS 잠금 해제 |

## 사용 라이브러리와 선택 이유

| 라이브러리 | 용도 | 선택 이유 |
|---|---|---|
| **aiortc** | 파이썬 WebRTC 서버 | 브라우저가 플러그인 없이 재생, 지연이 짧음(0.1~0.5초) |
| **sounddevice** (PortAudio) | USB 마이크 캡처 | aiortc가 쓰는 PyAV의 내장 ALSA로는 이 라즈베리파이의 마이크를 열 수 없었음 (`docs/troubleshooting.md` 7번) |
| **PyAV** | 오디오 프레임 객체, Tapo 소리 디코딩·리샘플 | aiortc의 의존성이라 이미 설치됨 |

**WebRTC를 고른 이유**: HTTP로 MP3 같은 오디오를 계속 보내는 방식은 버퍼 때문에 1~3초 늦는다. WebRTC는 실시간 통화용이라 지연이 짧다. 나중에 폰에서 고양이에게 말하는 양방향 기능으로 넓히기에도 맞다.

## 동작 원리

### 1. 소리 데이터 단위

모든 소리는 **48kHz, mono, 16bit 정수(int16)** 로 맞추고, **960샘플(20ms)** 씩 조각으로 다룬다. WebRTC 기본 코덱인 Opus가 20ms 단위로 압축하기 때문이다.

```
48,000 샘플/초 × 0.02초 = 960 샘플
960 샘플 × 2바이트 = 1,920바이트 / 조각
```

### 2. 마이크는 한 번만 열고 나눠 준다 (`AudioFanout`)

USB 마이크는 한 번에 한 프로그램만 열 수 있다. 연결마다 마이크를 열었더니, 새로고침할 때 이전 연결이 정리되기 전에 새 연결이 열려고 해서 `Device unavailable` 오류가 났다.

지금은:
- 앱이 시작할 때 `sounddevice.InputStream`을 한 번 열고 끝까지 유지한다. 20ms마다 콜백이 불린다.
- 콜백은 `pi_audio.dispatch(조각)`으로 등록된 모든 리스너에게 같은 조각을 준다.
- 실시간 듣기 연결(`MicrophoneTrack`)과 Pi 녹화(`FfmpegAudioOutput`)가 각각 리스너로 붙었다 떨어진다.

`AudioFanout`은 리스너 목록과 잠금만 가진 작은 클래스다. 첫 리스너가 생길 때 생성할 때 받은 `on_first_listener` 함수를 부른다. `TapoLive`는 이걸로 **누군가 듣거나 볼 때만** Tapo에 연결한다. 실시간 영상의 `H264Fanout`(`docs/webrtc_video.md`)도 같은 클래스를 상속한다.

### 3. 스레드와 asyncio 잇기

- Flask는 요청마다 일반 스레드에서 동작하고, aiortc는 `asyncio`(비동기) 기반이다.
- 그래서 `webrtc_loop`라는 asyncio 이벤트 루프를 전용 스레드에서 계속 돌린다.
- `/offer` 요청은 `asyncio.run_coroutine_threadsafe(process(), webrtc_loop)`로 루프에 작업을 넘기고 결과를 기다린다.
- 마이크 콜백(PortAudio 스레드)에서 받은 조각도 `run_coroutine_threadsafe(queue.put(조각), webrtc_loop)`로 각 연결의 `asyncio.Queue`에 넣는다.

### 4. `MicrophoneTrack`

aiortc의 `AudioStreamTrack`을 상속해서 `recv()`를 구현한다. aiortc가 다음 소리를 원할 때마다 `recv()`를 부르면, 큐에서 조각을 꺼내 `av.AudioFrame`(s16, mono, 48kHz)으로 만들어 돌려준다. `pts`(재생 시각)는 0부터 960씩 늘린다. 연결이 끊기면 리스너에서 빠진다.

### 5. WebRTC 연결 과정 (시그널링)

```
브라우저                                   서버(/offer)
RTCPeerConnection 생성
addTransceiver('audio', recvonly)  ← 받기만 함
createOffer → setLocalDescription
POST /offer {sdp, type, cam,  ───────────▶ RTCPeerConnection(iceServers=[]) 생성
            media: 'audio'}                cam에 맞는 소리 공급원 선택 (Pi 또는 Tapo)
                                           MicrophoneTrack 추가
                                           setRemoteDescription(offer)
                                           createAnswer → setLocalDescription
                                           ICE 후보 수집이 끝날 때까지 대기
                         ◀─────────────── {sdp, type} (answer)
setRemoteDescription(answer)
ontrack → <audio>.srcObject = 스트림
```

- **SDP**: 양쪽이 쓸 코덱, 방향(보내기/받기) 등을 적은 문서.
- **ICE 후보**: 서로에게 닿을 수 있는 주소·포트 목록. 서버가 후보를 다 모으기 전에 answer를 보내면 브라우저가 연결 경로를 몰라 소리가 안 들린다. 그래서 `iceGatheringState == "complete"`까지 기다린 뒤 응답한다 (trickle ICE를 구현하지 않는 대신).
- 폰과 라즈베리파이가 같은 Tailscale 네트워크에 있어서 중계(TURN) 서버는 쓰지 않는다. 서버 쪽은 STUN 서버도 쓰지 않는다(`iceServers=[]`). 라즈베리파이 자체 주소로 충분하고, STUN을 쓰면 공인 주소 확인에 연결마다 5초씩 걸렸다 (`docs/troubleshooting.md` 16번). 브라우저 쪽 소리 연결 설정에는 공개 STUN 서버가 남아 있지만 연결에 쓰이지 않는다.

### 6. Tapo 마이크 (`TapoLive`)

- Tapo RTSP 스트림에는 G.711 A-law, 8kHz 소리가 같이 들어 있다.
- PyAV로 RTSP(stream2)를 열고 오디오 패킷을 디코딩한다. 영상 패킷은 디코딩하지 않고 그대로 실시간 영상 쪽에 넘긴다 (`docs/webrtc_video.md`). 한 연결로 둘 다 받아서 보면서 소리를 켜도 카메라 연결이 늘지 않는다.
- `AudioResampler`로 8kHz → 48kHz, int16 mono로 바꾸고, 960샘플씩 잘라 `dispatch`한다.
- 듣거나 보는 사람이 없으면 연결을 끊는다.

### 7. 탭을 바꾸면

소리를 켠 상태에서 Pi ↔ Tapo 탭을 바꾸면 `toggleAudio()`를 두 번 불러 연결을 닫고 새 카메라로 다시 연다. 탭 클릭이 사용자 제스처라 iOS에서도 다시 재생된다.

### 8. iOS 잠금 해제

iOS(크롬 포함, 모두 WebKit)는 **사용자가 탭한 그 순간** 시작한 재생만 허용한다. WebRTC 트랙은 연결이 끝난 뒤 비동기로 도착해서 그냥 `play()`하면 막힌다.

해결: 버튼을 누른 순간, 다른 비동기 작업을 하기 전에 0샘플 무음 WAV를 `loop = true`로 `play()`해서 `<audio>` 요소를 "재생 중" 상태로 만들어 둔다. 나중에 트랙이 오면 `srcObject`만 바꾸고 다시 `play()`한다. 무음 WAV는 44바이트 헤더를 `DataView`로 직접 만들어 Blob URL로 쓴다. (`loop` 없이 0초짜리를 한 번 재생하면 바로 끝나서 아이폰에서 버튼을 두 번 눌러야 했다.)

## 측정

| 상황 | 전체 CPU (코어 4개 기준) |
|---|---|
| 녹화만 | 약 23% |
| 녹화 + 소리 1개 듣기 | 28~30% |

소리 연결 하나에 앱 CPU 약 15~18%(코어 1개 기준)를 쓴다. 대부분 aiortc의 Opus 인코딩이다.

## 한계

- 영상과 소리가 별도 WebRTC 연결이라 싱크가 조금 어긋날 수 있다.
- Tapo는 조용하면 잡음 제거 때문에 거의 0에 가까운 값을 보낸다. 조용할 때 측정으로는 "소리가 작다"와 "안 들어온다"를 구분하기 어렵다.
- 아직 듣기만 된다. 말하기(양방향)는 라즈베리파이에 스피커가 필요하다.
