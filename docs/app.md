# app.py + templates — 웹 서버와 화면

## 역할

- 모든 구성 요소(카메라, 서보, 녹화, 감지, 소리)를 만들어 연결하고 Flask 웹 서버를 띄운다.
- 로그인, 실시간 화면, 녹화 보기, 오류 목록 페이지와 버튼용 API를 제공한다.
- 서비스가 꺼질 때 녹화를 정리하고, Pi 카메라 복구가 안 되면 스스로 종료해 systemd가 다시 띄우게 한다.

## 코드 구조

### 시작 순서 (`if __name__ == "__main__"`)

```
setup_logging()                         로그 설정
VideoGet().start()                      Pi 카메라 캡처
TapoCamera(...).start()                 Tapo 영상 (.env에 설정이 있을 때만)
MjpegStream(...) × 2                    실시간 영상
TapoAudio(...)                          Tapo 마이크 (들을 때만 연결)
PanTiltServo()                          서보 (저장된 위치로 이동)
sounddevice.InputStream(...).start()    USB 마이크 (한 번만 열고 유지)
PiRecorder / TapoRecorder / RecordingCleaner   24시간 녹화, 보관 정리
FrameMotion × 2                         움직임 감지
signal(SIGTERM/SIGINT) → 녹화 정리 후 종료
picam.on_fatal → 녹화 정리 후 os._exit(1)
app.run(host="127.0.0.1", port=5000, threaded=True)
```

Tapo 설정(`TAPO_IP`, `TAPO_USER`)이 없으면 Tapo 관련 부분을 모두 건너뛰고 Pi 카메라만으로 동작한다.

### 라우트

| 주소 | 로그인 | 하는 일 |
|---|---|---|
| `/`, `/login`, `/logout` | — | 첫 화면, 로그인/로그아웃 |
| `/get_cam` | 페이지 | 실시간 화면 |
| `/video_feed`, `/video_feed/tapo` | 페이지 | MJPEG 스트림 |
| `/offer` | 페이지 | WebRTC 소리 연결 (`docs/audio.md`) |
| `/servo/<방향>?cam=tapo` | API | 서보/PTZ 이동, Center |
| `/set/angle` | API | 버튼 한 번에 움직일 각도 |
| `/recordings`, `/recordings/list`, `/recordings/file/<cam>/<name>` | 페이지/API | 녹화 목록, 재생·다운로드 |
| `/status` | API | 카메라 멈춤 상태 + 최근 오류 시각 (배너용) |
| `/errors`, `/errors/list` | 페이지/API | 오류 목록 |

- **페이지**: 로그인 안 했으면 로그인 화면으로 보냄.
- **API**(`api_login_required`): 로그인 안 했으면 `401`. 버튼 요청은 화면이 아니라 데이터만 주고받으므로 로그인 화면으로 보내지 않는다.

## 사용 라이브러리와 선택 이유

| 라이브러리 | 용도 | 선택 이유 |
|---|---|---|
| **Flask** | 웹 서버, 세션 로그인, 템플릿 | 작은 단일 서버에 충분하고 스트리밍 응답(제너레이터)이 쉬움 |
| **python-dotenv** | `.env`에서 비밀값 읽기 | 비밀번호·키를 코드와 저장소에서 분리 |
| **Bootstrap 5** (정적 파일) | 화면 레이아웃, 버튼, 배너 | 폰과 PC 화면을 같은 HTML로 |
| Tailscale (`tailscale serve`) | HTTPS, 외부 접속 | 포트포워딩 없이 내 기기에서만 접속 |

## 보안 구조

```
폰/맥북 ──(Tailscale WireGuard 암호화)──▶ tailscale serve (HTTPS, Let's Encrypt 인증서)
                                              │
                                              ▼
                                   Flask 127.0.0.1:5000 (라즈베리파이 내부에서만 열림)
```

- 공유기 포트포워딩은 모두 닫았다. 외부에서는 Tailscale에 로그인한 기기만 접속할 수 있다.
- Flask는 `127.0.0.1`에서만 들어서, 집 Wi-Fi 주소나 Tailscale IP로 직접 접속하면 연결이 거부된다.
- HTTPS 덕분에 브라우저의 "안전하지 않은 정보" 경고가 없고, 나중에 폰 마이크(양방향 소리)를 쓸 수 있다 (브라우저는 HTTPS에서만 마이크 허용).
- 로그인: Flask 세션(서명된 쿠키, `SECRET_KEY`), 1일 유지. 계정 비밀번호는 `.env`에만 있다.
- 녹화 파일 요청은 카메라 이름과 파일 이름 형식을 검사해 경로 조작(`../`)을 막는다.

## 화면 기능 (templates/, static/)

### 실시간 화면 `get_cam.html`

- **카메라 탭**: Pi 카메라 / Tapo. 영상 주소, 버튼 요청(`?cam=tapo`), 소리 공급원이 같이 바뀐다. 마지막 탭은 `localStorage`에 기억.
- **방향 버튼, 각도 선택(1°~20°), Center**: 고른 각도도 `localStorage`에 기억하고 페이지를 열 때 서버에 다시 보낸다.
- **소리 버튼**: WebRTC (`docs/audio.md`).
- **화면 저장 버튼**: 지금 `<img>`에 보이는 프레임을 캔버스에 그려 JPEG로 만든다. 모바일은 공유 창(`navigator.share`)의 "이미지 저장"으로 사진 앱에, PC는 다운로드. iOS는 공유 창을 버튼을 누른 그 순간 열어야 해서, `toDataURL` → `atob`로 비동기 없이 파일을 만든다. 확대 중이어도 원본 1280x720 전체가 저장된다.
- **영상 확대** (브라우저 확대가 아니라 영상만):
  - 더블탭/더블클릭: 누른 곳을 중심으로 2배, 다시 하면 원래대로.
  - 두 손가락: 1~4배. 시작할 때 손가락 사이에 있던 영상 지점이 손가락 사이를 따라오도록 계산한다.
  - 확대 중 한 손가락/마우스로 끌어서 이동. 영상 가장자리 밖으로는 못 나가게 이동 범위를 제한한다.
  - 계산: `transform: translate(tx, ty) scale(s)`, `transform-origin: 0 0`. 화면 좌표 p에서 배율을 s→s'로 바꿀 때 그 지점이 그대로 있으려면 `t' = p - (p - t) / s × s'`. 이동 범위는 `tx ∈ [w × (1 - s), 0]`.
  - 버튼을 빠르게 눌러도 iOS가 페이지를 확대하지 않도록 `touch-action: manipulation`. 영상 위는 `pan-x pan-y`(한 손가락 스크롤만 허용), 확대 중에는 `none`. iOS 고유 `gesturestart` 이벤트도 막는다.

### 녹화 페이지 `recordings.html`

카메라 탭 → 날짜 → 시간별 줄(한 줄 = 1시간, 버튼 = 10분). 움직임이 있었던 구간은 주황색, "움직임 구간만" 스위치. 누르면 `<video>`로 재생하고, 끝나면 다음 10분을 이어 재생. 다운로드 버튼은 `?download=1`로 `Content-Disposition: attachment`를 붙인다.

### 배너 `static/js/status_alert.js`

10초마다 `/status`를 확인한다.
- 빨간색: Pi 카메라가 멈춰 있는 동안
- 노란색: 최근 24시간 안에 멈췄다가 복구됨 (닫으면 그 기록은 다시 안 보임)
- 파란색: 오류 목록을 마지막으로 본 뒤 새 경고/오류 (오류 목록 링크)

## 종료와 재시작

- `systemctl stop/restart` → SIGTERM은 앱에만 감 (`KillMode=mixed`) → `stop_all()`로 녹화 파일을 닫은 뒤 `os._exit(0)`.
- Pi 카메라 복구 실패 → `_camera_fatal()` → 녹화 닫기(최대 20초) → `os._exit(1)` → systemd가 5초 뒤 다시 띄움.

## 한계

- Flask 개발 서버(`app.run`)를 쓴다. 가족 몇 명이 Tailscale 안에서 쓰는 규모라 충분하지만, 많은 사용자에게 공개하는 구조는 아니다.
- 계정은 코드에 두 명(`michael`, `natalia`)으로 정해져 있고 비밀번호는 평문 비교다 (`.env`에만 저장).
