# mjpeg.py — 예비 실시간 영상 (MJPEG)

## 역할

카메라의 최신 프레임을 JPEG로 압축해서 브라우저에 계속 보낸다. `<img src="/video_feed">` 한 줄로 실시간 영상을 보여준다. Pi 카메라와 Tapo 카메라마다 `MjpegStream`이 하나씩 있다.

**지금은 예비 경로다.** 실시간 영상은 WebRTC(H.264)로 보낸다 (`docs/webrtc_video.md`). 페이지는 WebRTC가 3번 연속 실패할 때, 또는 주소에 `?mjpeg`를 붙였을 때만 MJPEG를 쓴다.

## 코드 구조

| 이름 | 하는 일 |
|---|---|
| `MjpegStream(camera, fps=20)` | 카메라 하나에 대한 스트림 |
| `_encode_loop()` | 보는 사람이 있는 동안 초당 20번 최신 프레임을 JPEG로 인코딩 |
| `frames()` | 시청자 한 명용 제너레이터. 새 JPEG가 나올 때마다 multipart 조각을 내보냄 |

## 사용 라이브러리와 선택 이유

| 라이브러리 | 용도 | 선택 이유 |
|---|---|---|
| **OpenCV `imencode`** | JPEG 압축 | 프레임이 이미 OpenCV/NumPy 배열 |
| **Flask `Response`** + 제너레이터 | 끝나지 않는 HTTP 응답 | 별도 스트리밍 서버 없이 기존 웹 서버로 처리 |

**처음 MJPEG를 쓴 이유**: 브라우저가 `<img>` 태그만으로 재생하고, 별도 플레이어나 JavaScript가 필요 없다. 지연이 짧다.

**WebRTC로 바꾼 이유**: 프레임마다 JPEG(품질 95)라 Pi 52Mbps, Tapo 30Mbps를 썼다. 버퍼가 없는 방식이라 Wi-Fi 속도가 잠깐만 떨어져도 화면이 멈췄다 (`docs/troubleshooting.md` 16번). 압축된 H.264를 그대로 보내는 WebRTC로 바꿔 1.5Mbps / 0.2Mbps가 됐다. 예비 경로로 쓸 때도 품질은 그대로(OpenCV 기본값 95)다.

## 동작 원리

### 1. MJPEG = JPEG를 계속 이어 보내기

HTTP 응답의 `Content-Type`을 `multipart/x-mixed-replace; boundary=frame`으로 보내면, 브라우저는 경계 문자열(`--frame`)이 나올 때마다 이전 그림을 새 그림으로 **교체**한다.

```
--frame
Content-Type: image/jpeg

<JPEG 바이트>
--frame
Content-Type: image/jpeg

<다음 JPEG 바이트>
...
```

### 2. 인코딩은 한 번, 시청자는 여럿

처음에는 시청자마다 자기 제너레이터에서 매번 JPEG를 만들었다. 두 사람이 같은 카메라를 보면 같은 프레임을 두 번 인코딩해서, 한 명당 CPU가 약 35%씩 늘었다.

지금은 카메라마다 인코딩 스레드 하나가 JPEG를 만들어 `_jpeg`에 두고, 번호(`_seq`)를 올린 뒤 `Condition.notify_all()`로 기다리는 시청자들을 깨운다. 각 시청자는 자기가 마지막으로 보낸 번호와 다를 때만 새 JPEG를 가져간다.

```
인코딩 스레드:  frame → JPEG → (_jpeg, _seq+1) → notify_all
시청자 A/B/C:   wait_for(_seq != 내 seq) → 같은 JPEG를 각자 전송
```

측정 결과 (Pi 영상, 앱 CPU, 코어 1개 = 100%):

| 시청자 | 전 (시청자마다 인코딩) | 후 (한 번만 인코딩) |
|---|---|---|
| 1명 | 81% | 85% |
| 2명 | 약 120% | 91% |
| 4명 | — | 97% |

### 3. 보는 사람이 있을 때만 일함

`frames()`가 시작되면 시청자 수를 올리고, 인코딩 스레드가 없으면 시작한다. 브라우저가 연결을 끊으면 Flask가 제너레이터를 닫아 `finally`에서 시청자 수를 내린다. 0명이 되면 인코딩 스레드가 스스로 끝난다.

### 4. 속도 제한

초당 20장으로 고정한다. 인코딩에 걸린 시간을 빼고 남은 시간만 쉬어서 실제 간격을 맞춘다. 제한이 없으면 같은 프레임을 쉬지 않고 계속 인코딩해 CPU를 낭비한다.

## 한계

- H.264보다 수십 배 많은 대역폭을 써서 Wi-Fi나 모바일 데이터에서 쉽게 버벅인다. 그래서 예비 경로로만 쓴다.
