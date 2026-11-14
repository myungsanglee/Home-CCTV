# applog.py — 로그와 백그라운드 작업 감시

## 역할

- 프로그램 기록을 **목적별 파일**로 나눠 남긴다 (일반, 경고/오류, 웹 오류 목록용).
- 백그라운드 작업(카메라 캡처, 녹화, 움직임 감지 등)이 예상 못 한 오류로 죽으면 **기록하고 다시 시작**한다.
- 웹 오류 목록(`/errors`)과 배너에 쓸 최근 경고/오류를 제공한다.

## 왜 만들었나

예전에는 모든 출력이 `print()`로 systemd 서비스 로그(journal) 한 곳에 섞여 있었다. 7일치 3,798줄 중 88%가 웹 접속 기록이라 오류가 묻혔고, 심각도 구분도 없었다. 또 백그라운드 스레드가 오류로 죽으면 그 기능만 조용히 멈춘 채로 남았다. Pi 카메라가 12시간 멈춰 있었던 것도 아무 알림이 없었다.

## 코드 구조

| 이름 | 하는 일 |
|---|---|
| `setup_logging()` | 루트 로거에 출력 대상 4개 설정, 처리되지 않은 오류 훅 등록 |
| `_NoAccessLog` | 파일에서 웹 접속 기록(werkzeug INFO) 제외 |
| `_JsonFormatter` | 한 기록을 JSON 한 줄로 (웹 목록용) |
| `supervised_thread(name, target)` | 작업이 오류로 죽으면 로그 남기고 5초 뒤 다시 실행 |
| `recent_errors(hours=24)` | 최근 경고/오류 목록 |

## 사용 라이브러리와 선택 이유

파이썬 표준 `logging`만 쓴다. 출력 대상(handler)을 여러 개 붙여 같은 기록을 목적별로 나눌 수 있고, `RotatingFileHandler`로 파일 크기 관리까지 된다. 외부 라이브러리가 필요 없다.

## 동작 원리

### 1. 로거와 핸들러

각 모듈은 `logging.getLogger("이름")`으로 자기 로거를 쓴다 (`pi-camera`, `tapo`, `recorder`, `pan-tilt` 등). 로거에 남긴 기록은 루트 로거로 올라가고, 루트에 붙은 핸들러들이 각자 조건에 맞는 것만 받아 쓴다.

| 핸들러 | 받는 기록 | 형식 | 크기 제한 |
|---|---|---|---|
| 콘솔(stdout) | 전부 | `LEVEL 이름: 메시지` | — (systemd journal이 저장) |
| `logs/app.log` | INFO 이상, 웹 접속 기록 제외 | 시각 포함 한 줄 + Traceback | 5MB × 6개 |
| `logs/error.log` | WARNING 이상 | 위와 같음 | 2MB × 6개 |
| `logs/errors.jsonl` | WARNING 이상 | JSON 한 줄 | 2MB × 3개 |

**RotatingFileHandler**: 파일이 크기를 넘으면 `app.log` → `app.log.1`로 이름을 바꾸고 새 파일에 쓴다. 정해진 개수를 넘는 가장 오래된 파일은 지운다. 그래서 로그가 SD 카드를 채우지 않는다 (전부 합쳐 50MB 이하).

### 2. 처리되지 않은 오류

- `sys.excepthook`: 메인 스레드에서 잡히지 않은 오류를 CRITICAL로 남긴다.
- `threading.excepthook`: 다른 스레드에서 잡히지 않은 오류를 ERROR로 남긴다.
- Flask가 요청 처리 중 잡은 오류("Exception on /servo/left …")는 Flask 로거를 거쳐 같은 핸들러들로 간다.

### 3. `supervised_thread`

```python
def runner():
    while True:
        try:
            target()
            return            # 정상 종료면 끝
        except Exception:
            log.exception(...)  # 오류 + Traceback 기록
            time.sleep(5)       # 잠깐 쉬고 다시 실행
```

카메라 캡처, Pi/Tapo 녹화, 움직임 감지, 실시간 영상 인코딩, Tapo 소리, WebRTC 이벤트 루프, 녹화 정리, 서보 이동이 모두 이걸로 시작된다. 한 기능이 죽어도 나머지는 영향이 없고, 죽은 기능은 5초 뒤 살아난다.

### 4. 웹에서 보기

- `recent_errors()`는 `errors.jsonl`과 바로 이전 파일(`.1`)을 읽어 최근 24시간 기록을 돌려준다.
- `/status`는 그 시각 목록을, `/errors/list`는 전체 내용을 준다.
- `static/js/status_alert.js`는 사용자가 오류 목록을 마지막으로 본 시각(브라우저 `localStorage`) 이후 새 기록이 있으면 파란 배너를 띄운다.
- `/errors` 페이지는 같은 출처+같은 메시지를 한 줄로 묶어 횟수와 기간을 보여주고, 누르면 Traceback을 펼친다.

## 한계

- libcamera(C++)가 직접 출력하는 메시지는 파이썬 `logging`을 거치지 않아 journal에만 남는다. 그 결과인 카메라 멈춤은 `picam.py`가 잡아서 기록한다.
- 같은 오류가 계속 반복되면 기록이 많이 쌓일 수 있다. 서보 I2C 오류처럼 반복될 만한 곳은 해당 모듈에서 10초에 한 번만 남긴다.
