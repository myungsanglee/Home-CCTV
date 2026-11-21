# Home CCTV

집 고양이를 밖에서 보기 위한 CCTV. Raspberry Pi 4 한 대가 웹 서버, 카메라 2대 영상/소리, 팬틸트 제어, 24시간 녹화, 움직임 감지를 모두 맡음. 기능 목록과 실행 방법은 README.md, 컴포넌트별 동작 원리는 `docs/<모듈명>.md`, 문제 해결 기록과 저장소 밖 설정(systemd, fstab, Tailscale 등)은 `docs/troubleshooting.md` 참고.

이 저장소는 GitHub에 공개돼 있음. IP, Tailscale 주소, 계정, 비밀번호 같은 값은 코드·문서·커밋 메시지에 넣지 말 것 (`.env`에만).

## 구성

- `app.py`: Flask 앱, 라우트, 모든 구성 요소를 `if __name__ == "__main__"`에서 생성
- `picam.py`: Pi 카메라 캡처(`VideoGet`), 날짜/시각 표시, 멈춤 감시와 자동 복구
- `tapo.py`: Tapo C210 RTSP 영상(`TapoCamera`), ONVIF PTZ, 마이크(`TapoAudio`)
- `pan_tilt.py`: PCA9685 + GH-S37D 서보 2개, 부드럽게 움직이기(백그라운드 스레드)
- `recorder.py`: 24시간 녹화(10분 MP4), 3일 보관 정리, 녹화 목록
- `motion.py`: 두 카메라 공통 움직임 감지(`FrameMotion`, 흔들림 보정)
- `mjpeg.py`: 실시간 영상(프레임당 JPEG 한 번만 인코딩해서 공유)
- `audio.py`: 소리 나눠 보내기(`AudioFanout`), 실시간 소리는 WebRTC(aiortc)
- `applog.py`: 로그 설정, `supervised_thread`(백그라운드 작업이 죽으면 다시 시작)

하드웨어: Pi 카메라 Module v2(IMX219, 1640x1232 센서 모드로 가로 62°), Arducam B0283 팬틸트(PCA9685 I2C 0x40, 채널 0=틸트, 1=팬), USB 마이크, 녹화용 USB(ext4, `/mnt/cctv`), Tapo C210.

## 운영

- 서비스: `sudo systemctl restart cctv` (`/etc/systemd/system/cctv.service`, 저장소 밖)
- 웹은 `127.0.0.1:5000`에서만 열리고 Tailscale HTTPS(`tailscale serve`)로만 접속. 테스트는 `.env`의 계정으로 로그인한 `requests.Session`을 쓰고, 주소는 `tailscale status --json`의 `Self.DNSName`으로 얻을 것
- 로그: `logs/app.log`(일반), `logs/error.log`(경고/오류), `logs/errors.jsonl`(웹 `/errors`용), `logs/camera_events.log`(카메라 멈춤), 그 밖은 `journalctl -u cctv`
- 녹화: `/mnt/cctv/recordings/{pi,tapo}/`, 움직임 기록은 그 아래 `motion/`
- 의존성: README의 "시작하기 > 설치" (apt + `requirements.txt`, pip은 `--break-system-packages`)

## 테스트할 때

- Pi 카메라, USB 마이크, 서보 보드는 서비스가 붙잡고 있음. 직접 다루는 시험은 `sudo systemctl stop cctv` 후 하고 끝나면 반드시 다시 `start`. 서비스를 멈추면 녹화도 멈추므로 짧게
- 웹 기능은 서비스를 띄운 채 HTTP로 시험
- 영상/사진 내용은 보지 않음(사적인 장면이 찍힘). 프레임 간 이동량, 밝기, 픽셀 수 같은 숫자로만 확인하고, 시험용 파일은 끝나면 지움
- 서보나 PTZ를 움직이는 시험은 끝나면 원래 위치(`servo_state.json`)로 되돌림
- 시험용 스크립트와 임시 파일은 프로젝트 폴더가 아니라 세션 임시 폴더에 둠

## 다시 하면 안 되는 것 (측정으로 확인한 이유)

- **녹화에 `movflags=+faststart` 금지**: 10분 파일(약 100MB)을 닫을 때 전체를 다시 써서 ffmpeg가 5~10초 막히고, Pi 인코더가 막혀 실시간 캡처까지 멈춤. 멈춤 감시가 10분마다 오작동했던 원인. 재생 정보가 파일 끝에 있어도 Range 요청으로 재생됨
- **Pi 녹화 출력에 대기열(버퍼) 넣지 말 것**: ffmpeg가 `-use_wallclock_as_timestamps`로 받은 시각을 쓰기 때문에, 몰아서 넘기면 그 구간이 빨리감기처럼 기록됨
- **`ONVIFCamera()`를 직접 만들지 말고 `tapo.connect_onvif()` 사용**: onvif-zeep은 연결할 때마다 만료 없는 이벤트 구독을 몰래 만들어 Tapo에 쌓이고, 결국 이벤트가 조용히 끊김. Tapo 움직임 감지를 ONVIF 이벤트에서 화면 비교로 바꾼 이유이기도 함
- **서보를 목표 각도로 한 번에 보내지 말 것**: 급정지 충격으로 특정 각도(예: 20°, 29° 근처)에서 떨림(헌팅)이 시작되고 고정된 받침대에서 계속 이어짐. 부드럽게 움직이면 115~174px → 10~12px. 이동 후 PWM 신호를 끄는 방법은 오히려 더 떨려서 되돌림
- **서보 각도 표기는 실제와 다름**: 0~90 범위가 실제로는 150° 이상 회전(명령 10° ≈ 실제 17°). 펄스 600~2400μs
- **서비스 종료 신호 관리**: ffmpeg는 종료 신호를 3번 넘게 받으면 파일을 닫지 않고 바로 꺼짐. 그래서 unit에 `KillMode=mixed`, 앱이 SIGTERM을 받아 `recorder.stop_all()`로 녹화를 먼저 닫음. ffmpeg를 SIGKILL로 죽이지 말 것
- **USB 마이크는 sounddevice(PortAudio)로**: pip로 설치한 PyAV(aiortc)의 내장 ALSA는 마이크를 못 염. 마이크는 한 번만 열고 `AudioFanout`으로 실시간 듣기와 녹화에 나눠 줌
- **Pi 카메라 `capture_array()`를 시간 제한 없이 부르지 말 것**: 센서가 멈추면("Camera frontend has timed out") 영원히 기다림. `wait=False` + `wait(job, timeout)` 사용
- **`picam2.stop()`/`stop_encoder()`는 멈출 수 있음**: 다시 열기는 `run_with_timeout`으로 감싸고, 안 되면 서비스 재시작으로 넘김
- iOS: 오디오는 버튼을 누른 그 순간에 무음 WAV를 loop 재생해서 잠금 해제, 공유 창(`navigator.share`)도 같은 순간에 열어야 함

## 알려진 하드웨어 문제 (아직 해결 안 됨)

- Pi 카메라 센서가 1~4주에 한 번 멈춤("Camera frontend has timed out"). 리본 케이블을 새것으로 바꿨는데도 생김. 감시 기능이 자동 복구하므로 사용자는 이대로 두기로 함
- 서보 보드 I2C 통신 오류(`OSError: [Errno 5]`)가 가끔 남
- 서보 보드 VCC는 Pi 5V 핀에 연결됨. 서보(GH-S37D) 사양은 3.6~4.8V라 약간 높고, 서보 전류가 Pi 5V 전원을 같이 씀. 위 두 문제의 남은 의심 원인 (서보용 별도 5V 전원 + GND 공통 연결로 분리 가능)

## 문서 규칙

- 취업 포트폴리오를 위해 코드와 문서(README, 커밋)를 깔끔하게 유지한다.
- **컴포넌트 개발이 끝나면 반드시 `docs/<모듈명>.md` 문서를 작성한다.** (이 프로젝트는 패키지가 아니라 모듈 파일 단위라 모듈 이름을 씀. 예: `recorder.py` → `docs/recorder.md`)
  내용: 코드 구조, 사용 라이브러리와 선택 이유, 무엇을 어떻게 개발했는지,
  그리고 관련 개념을 이 분야를 전혀 모르는 사람도
  코드와 문서만 보고 "어떤 원리로 계산되고 왜 이렇게 작성했는지" 이해할 수
  있는 수준으로 설명.
- **비자명한 문제를 해결하면 `docs/troubleshooting.md` 에 기록한다.**
  "증상 → 진단 과정 → 근본 원인 → 해결 → 재발 방지" 형식. 특히 git 에 안 남는
  설정은 여기가 유일한 기록이므로 반드시 남긴다.
  (컴포넌트 "정상 동작 방식"은 docs/<모듈명>.md, 문제 해결 "여정"은 여기)
- **개발·변경이 끝나면 `README.md` 를 같은 작업에서 갱신한다** — 컴포넌트 개발뿐
  아니라 아키텍처·스택·실행 절차·진행 상태가 바뀌는 모든 변경이 트리거다.
  점검할 섹션: ① 아키텍처 다이어그램 ② 기술 스택 표 ③ 레포 구조 ④ "개발한 내용"
  표(상태 포함) ⑤ 시작하기(실행 명령). (docs 는 상세, README 는 개요·실행법 요약)
- **아키텍처급 변경(통신 방식·스택 교체 등) 후에는 문서 전체 잔재 스윕을 한다.**
  폐기된 개념의 키워드로 `CLAUDE.md·README·docs/` 전체를 grep 해서 낡은 서술을
  일괄 갱신한다 (CLAUDE.md 자신도 대상).

## 작업 방식

- 답변과 코드 주석은 한국어
- 기능마다 새 브랜치에서 작업하고, 사용자가 말하면 `main`에 fast-forward로 합침
- 커밋은 사용자가 요청할 때만. 푸시는 사용자가 직접 함
- 실제 동작은 측정해서 확인하고, 확인하지 못한 부분은 그렇다고 말함
- `.env` 값, 비밀번호, 토큰은 출력하지 않음 (명령 출력에서 `grep -v -i password`로 거르기)
- 서비스 로그(journal)를 읽을 때는 반드시 RTSP 계정을 가릴 것: `| sed -E 's#rtsp://[^/ ]*@#rtsp://***@#g'`. 2026-11-21 이전 journal에는 ffmpeg가 찍은 카메라 계정이 그대로 남아 있음
- 외부 프로그램(ffmpeg 등) 출력을 서비스 로그로 그대로 흘려보내지 말 것. 받아서 `applog.redact`를 거친 뒤 로그로
