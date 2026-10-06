# Home CCTV Project
Raspberry Pi 4를 이용하여 Pan/Tilt가 가능한 Home CCTV Project
<div>
<img src="https://user-images.githubusercontent.com/55565351/226543166-81bb9354-4ac6-414c-bbcd-6d22005ac036.jpg" width="300" height="300"/>
<img src="https://user-images.githubusercontent.com/55565351/226543218-d7310746-eb78-4f4a-a747-d684d840af0c.jpg" width="300" height="300"/>
<img src="https://user-images.githubusercontent.com/55565351/226543259-cae6fb1e-a769-46a7-96a7-86174dbb4e45.jpg" width="300" height="300"/>
<img src="https://user-images.githubusercontent.com/55565351/226543273-aa051578-4431-484a-afdb-705e6a658bef.jpg" width="300" height="300"/>
</div>

## Implementations
 * 실시간 영상 스트리밍 (MJPEG, 시청자가 여러 명이어도 프레임당 JPEG 인코딩은 한 번)
 * 카메라 2대 탭 전환: Pi 카메라 + Tapo C210 (RTSP)
 * Pan/Tilt
   * Pi 카메라: PCA9685 + GH-S37D 서보 (Arducam B0283), 마지막 위치 저장/복원
   * Tapo: ONVIF PTZ (RelativeMove, Center는 AbsoluteMove (0, 0))
 * 카메라 이동 각도 설정 (1°, 5°, 10°, 15°, 20°)
 * 실시간 소리 듣기 (WebRTC): Pi 탭은 USB 마이크, Tapo 탭은 Tapo 카메라 마이크
 * 영상 확대: 더블탭/더블클릭 2배, 두 손가락 1~4배, 확대 중 끌어서 이동
 * 현재 화면 저장 (모바일은 공유 창의 "이미지 저장", PC는 다운로드)
 * 24시간 녹화 (USB 저장, 10분 단위 MP4, 3일 보관)
   * Pi 카메라: 하드웨어 H.264 1280x720 + USB 마이크 소리
   * Tapo: 고화질 2304x1296 원본 그대로 + 카메라 마이크 소리
   * 웹에서 날짜/시간별로 골라 보기, 이어서 재생, 다운로드
 * 움직임 감지: 움직임이 있었던 10분 구간을 녹화 목록에 표시, "움직임 구간만" 보기
   * Pi 카메라: 작은 화면(320x180) 프레임 비교, 초당 3번
   * Tapo: 카메라 자체 감지 알림(ONVIF 이벤트) 사용 (Tapo 앱에서 움직임 감지를 켜야 함)
 * 로그인/로그아웃 (카메라 제어 요청도 로그인 필요)
 * Tailscale HTTPS로만 접속 가능

## TODOs
- [ ] Object Tracking
- [ ] Object Detection

## Requirements
* Raspberry Pi 4, Raspberry Pi OS (Bookworm)
* Pi 카메라 + Arducam Pan-Tilt Platform (B0283, PCA9685 I2C 0x40)
* USB 마이크
* Tapo C210 (선택, 카메라 계정 생성 필요)
* Tailscale
* 녹화용 USB 저장장치 (ext4, `/mnt/cctv`에 연결)

## 설치
```bash
# 1. 시스템 패키지
sudo apt install python3-flask python3-dotenv python3-opencv python3-numpy python3-picamera2 libportaudio2

# 2. pip 패키지
pip install -r requirements.txt --break-system-packages

# 3. I2C 켜기 (Interface Options -> I2C)
sudo raspi-config
```

`onvif-zeep`는 WSDL 파일을 `~/.local/lib/python3.4/site-packages/wsdl`에 설치함 (`tapo.py`의 기본 경로).
다른 곳에 있으면 환경 변수 `ONVIF_WSDL_DIR`로 지정.

녹화용 USB는 ext4로 포맷하고 `/etc/fstab`에 등록 (빠져 있어도 부팅되도록 `nofail`):
```
UUID=<USB 파티션 UUID>  /mnt/cctv  ext4  defaults,noatime,nofail,x-systemd.device-timeout=10s  0  2
```
녹화 파일은 `/mnt/cctv/recordings/{pi,tapo}/`에 저장되고, USB가 연결돼 있지 않으면 녹화하지 않음.

USB 마이크 장치 번호는 `app.py`의 `AUDIO_DEVICE`로 지정 (`python -c "import sounddevice as sd; print(sd.query_devices())"`로 확인).

## 설정 (`.env`)
```
SECRET_KEY=<임의의 긴 문자열>
MICHAEL_PASSWORD=<로그인 비밀번호>
NATALIA_PASSWORD=<로그인 비밀번호>

# Tapo 카메라 (없으면 Pi 카메라만 사용)
TAPO_IP=<카메라 고정 IP>
TAPO_USER=<Tapo 앱에서 만든 카메라 계정>
TAPO_PASSWORD=<카메라 계정 비밀번호>
```

## 실행
```bash
python app.py
```

웹 서버는 `127.0.0.1:5000`에서만 열리고, Tailscale HTTPS로 접속함.
Tailscale 관리 페이지에서 MagicDNS와 HTTPS Certificates를 켠 뒤 한 번만 실행:
```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:5000
```
접속 주소: `https://<기기 이름>.<tailnet 이름>.ts.net`

부팅 시 자동 실행은 systemd 서비스(`/etc/systemd/system/cctv.service`)로 설정:
```ini
[Unit]
Description=M&N Home CCTV Service
After=network.target

[Service]
User=michael
Group=michael
WorkingDirectory=/home/michael/project/Home-CCTV
ExecStart=/usr/bin/python -u /home/michael/project/Home-CCTV/app.py
Restart=always
RestartSec=5

# 종료 신호는 앱에만 보내고, 앱이 녹화 파일을 닫은 뒤 ffmpeg를 정리함
KillMode=mixed
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
```
