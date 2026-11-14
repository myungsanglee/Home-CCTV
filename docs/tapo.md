# tapo.py — Tapo C210 카메라 (영상, PTZ, 마이크)

## 역할

TP-Link Tapo C210 카메라를 Tapo 앱 대신 이 웹에서 쓰게 한다.
- **영상**: RTSP로 받아 실시간 화면(720p)과 움직임 감지에 공급. 녹화는 `recorder.TapoRecorder`가 고화질(2304x1296) 스트림으로 따로 받음
- **PTZ**: ONVIF로 좌우·위아래 이동, Center
- **마이크**: RTSP의 소리만 꺼내 실시간 듣기에 공급 (`docs/audio.md`)

**왜 Tapo 앱 대신 직접 붙였나**: 보안 때문이다. 영상은 집 안 네트워크(RTSP)로만 받고 웹은 Tailscale로만 열어서, TP-Link 클라우드나 앱을 거치지 않고 볼 수 있다. 공유기에서 카메라의 인터넷을 차단해도 RTSP/ONVIF는 집 안 통신이라 계속 동작한다.

## 코드 구조

| 이름 | 하는 일 |
|---|---|
| `subscription_manager(cam, address)` | ONVIF 이벤트 구독을 연장·해지하는 클라이언트 |
| `connect_onvif(ip, user, password)` | ONVIF 연결 + 라이브러리가 몰래 만든 이벤트 구독 해지 |
| `TapoCamera` | RTSP 영상 수신 스레드, 최신 프레임, PTZ (`move`, `center`), `last_move` |
| `TapoAudio` | 듣는 사람이 있을 때만 RTSP 소리 수신 (`AudioFanout` 상속) |

## 사용 라이브러리와 선택 이유

| 라이브러리 | 용도 | 선택 이유 |
|---|---|---|
| **OpenCV `VideoCapture`** | RTSP 영상 받기·디코딩 | 프레임을 바로 NumPy 배열로 받아 다른 모듈과 형식이 같음 |
| **onvif-zeep** | ONVIF(SOAP) 호출 | 카메라 표준 제어 프로토콜을 파이썬으로 호출 |
| **PyAV** | RTSP 소리만 디코딩·리샘플 | 영상은 디코딩하지 않고 소리 패킷만 처리할 수 있음 |

## 동작 원리

### 1. RTSP와 ONVIF

- **RTSP**: 카메라가 영상을 실시간으로 보내는 표준. Tapo는 `rtsp://<카메라 계정>@<IP>:554/stream1`(2304x1296, 15fps)과 `stream2`(1280x720)를 제공한다. 영상은 H.264, 소리는 G.711 A-law 8kHz.
- **ONVIF**: 네트워크 카메라 제어 표준. SOAP(XML) 웹 서비스로, Tapo는 포트 2020에서 받는다. PTZ, 장치 정보, 이벤트 등을 다룬다.
- 접속 계정은 Tapo 앱에서 따로 만드는 **카메라 계정**이다 (TP-Link 클라우드 계정 아님). 비밀번호에 특수문자가 있어도 되도록 URL에 넣을 때 `quote()`로 인코딩한다.

### 2. 영상 수신

`get()` 스레드가 `stream2`를 `cv2.VideoCapture`로 계속 읽어 최신 프레임만 보관한다 (`picam.VideoGet`과 같은 방식). 버퍼를 1로 줄여 오래된 프레임이 쌓이지 않게 한다.

연결이 끊기면 "Reconnecting…" 안내 화면을 보여주고 3초마다 다시 연결한다. 끊긴 순간과 다시 연결된 순간(끊긴 시간 포함)을 한 번씩 로그에 남긴다.

### 3. PTZ와 좌표

ONVIF PTZ 좌표는 좌우·위아래 모두 **-1 ~ 1**이다. C210 사양(좌우 360°, 상하 114°)으로 각도를 좌표로 바꾼다.

```
좌우: 1 단위 = 360° / 2 = 180°   → 10° 이동 = 10 / 180 ≈ 0.056
상하: 1 단위 = 114° / 2 = 57°    → 10° 이동 = 10 / 57 ≈ 0.175
```

- 웹 버튼(↑↓←→, 1°~20°)은 **RelativeMove**(지금 위치에서 상대 이동)로 보낸다. 보낸 값만큼 좌표가 정확히 바뀌는 것을 확인했다.
- **방향이 반대인 곳이 있다**: ContinuousMove `+x`는 실제로 오른쪽으로 돌지만 위치 값은 줄어든다. RelativeMove `+x`는 위치 값이 늘어 실제로는 왼쪽이다. 그래서 `DIRECTIONS`에서 오른쪽을 `-x`로 둔다.
- Center는 ONVIF "홈 위치"를 지원하지 않아서 **AbsoluteMove (0, 0)** 으로 보낸다.
- PTZ를 부를 때마다 `last_move`를 갱신해서 움직임 감지가 이동 직후 4초를 쉬게 한다.
- ONVIF 연결은 처음 쓸 때 만들고, 호출이 실패하면 버렸다가 다음 호출 때 다시 연결한다.

### 4. `connect_onvif`가 필요한 이유

onvif-zeep의 `ONVIFCamera()`는 생성될 때 내부에서 **만료 시간 없는 이벤트 구독(PullPoint)** 을 하나 만든다. 연결할 때마다 구독이 쌓이면 Tapo가 새 구독을 거부하는데, 라이브러리가 그 실패를 조용히 삼킨다. 예전에 이걸 모르고 4분마다 다시 연결해서 Tapo 움직임 알림이 몇 시간 뒤 끊겼다 (`docs/troubleshooting.md` 5번).

`connect_onvif`는 연결한 뒤 그 숨은 구독의 주소(`cam.xaddrs[PULLPOINT_KEY]`)를 꺼내 바로 `Unsubscribe`한다. onvif-zeep에는 구독 관리 클라이언트를 만드는 함수가 없어서, `events.wsdl`의 `SubscriptionManagerBinding`으로 `ONVIFService`를 직접 만든다(`subscription_manager`).

지금은 Tapo 움직임 감지도 화면 비교 방식이라 이벤트 구독을 쓰지 않지만, PTZ 연결도 `ONVIFCamera()`를 만들기 때문에 이 함수를 계속 쓴다.

### 5. Tapo 마이크

`docs/audio.md` 6번 참고. 듣는 사람이 있을 때만 RTSP에 따로 연결해 소리 패킷만 디코딩한다.

## 한계

- 카메라 IP가 바뀌면 연결이 끊긴다. 공유기에서 IP를 고정해야 한다.
- Tapo가 조용할 때 잡음 제거로 거의 무음을 보내서, 마이크 동작은 실제로 소리가 날 때만 확인할 수 있다.
- 각도 변환은 사양값 기준이라 실제 회전 각도와 조금 다를 수 있다.
