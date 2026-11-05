// 화면 위쪽 알림 배너 (10초마다 /status 확인)
// - Pi 카메라가 멈춰 있는 동안: 빨간 배너
// - 최근 24시간 안에 Pi 카메라가 멈췄다가 복구됐으면: 노란 배너 (닫으면 그 기록은 다시 안 보임)
// - 오류 목록을 마지막으로 본 뒤 새 경고/오류가 생겼으면: 파란 배너 + 오류 목록 링크
(function () {
    const box = document.getElementById('cameraAlert');
    if (!box) return;
    const DISMISS_KEY = 'cameraAlertDismissed';
    const ERRORS_SEEN_KEY = 'errorsSeenTs';
    const pad = (n) => String(n).padStart(2, '0');

    const cameraBox = document.createElement('div');
    const errorBox = document.createElement('div');
    box.appendChild(cameraBox);
    box.appendChild(errorBox);

    function fmt(ts) {
        const d = new Date(ts * 1000);
        return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function fmtDuration(s) {
        if (s < 60) return `${Math.round(s)}초`;
        if (s < 3600) return `${Math.round(s / 60)}분`;
        return `${(s / 3600).toFixed(1)}시간`;
    }

    function load(key, fallback) {
        try {
            const v = localStorage.getItem(key);
            return v === null ? fallback : JSON.parse(v);
        } catch (e) {
            return fallback;
        }
    }

    function save(key, value) {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
    }

    function show(target, kind, message, options = {}) {
        target.innerHTML = '';
        const div = document.createElement('div');
        div.className = `alert alert-${kind} d-flex align-items-start justify-content-between mb-2`;
        const text = document.createElement('span');
        text.textContent = message;
        if (options.link) {
            const a = document.createElement('a');
            a.href = options.link.href;
            a.className = 'alert-link ms-2';
            a.textContent = options.link.text;
            text.appendChild(a);
        }
        div.appendChild(text);
        if (options.onClose) {
            const close = document.createElement('button');
            close.type = 'button';
            close.className = 'btn-close ms-2';
            close.setAttribute('aria-label', '닫기');
            close.onclick = () => { options.onClose(); target.innerHTML = ''; };
            div.appendChild(close);
        }
        target.appendChild(div);
    }

    function renderCamera(status) {
        if (status.stalled) {
            show(cameraBox, 'danger', `Pi 카메라 영상이 ${fmt(status.stalled_since)}부터 멈춰 있어요. 자동으로 복구하는 중이에요.`);
            return;
        }
        const hidden = load(DISMISS_KEY, []);
        const events = (status.recent || []).filter((e) => !hidden.includes(e.start_ts));
        if (!events.length) {
            cameraBox.innerHTML = '';
            return;
        }
        const e = events[events.length - 1];
        let message = e.result === 'recovered'
            ? `Pi 카메라가 ${fmt(e.start_ts)}에 멈췄다가 ${fmt(e.end_ts)}에 자동 복구됐어요. ` +
              `${fmtDuration(e.duration_s)} 동안 영상과 녹화가 끊겼어요.`
            : `Pi 카메라가 ${fmt(e.start_ts)}에 멈췄고, 자동 복구가 안 돼서 ${fmt(e.end_ts)}에 서비스를 다시 시작했어요.`;
        if (events.length > 1) message += ` (최근 24시간 동안 ${events.length}번)`;
        show(cameraBox, 'warning', message, {
            onClose: () => save(DISMISS_KEY, hidden.concat(events.map((x) => x.start_ts)).slice(-50)),
        });
    }

    function renderErrors(errorTs) {
        // 오류 목록 페이지에서는 배너를 띄우지 않음
        if (location.pathname === '/errors') {
            errorBox.innerHTML = '';
            return;
        }
        const seen = load(ERRORS_SEEN_KEY, 0);
        const fresh = (errorTs || []).filter((ts) => ts > seen);
        if (!fresh.length) {
            errorBox.innerHTML = '';
            return;
        }
        show(errorBox, 'info', `새 경고/오류가 ${fresh.length}건 있어요 (마지막 ${fmt(fresh[fresh.length - 1])}).`, {
            link: { href: '/errors', text: '오류 목록 보기' },
            onClose: () => save(ERRORS_SEEN_KEY, fresh[fresh.length - 1]),
        });
    }

    async function check() {
        try {
            const response = await fetch('/status');
            if (!response.ok) return;
            const data = await response.json();
            renderCamera(data.pi);
            renderErrors(data.error_ts);
        } catch (e) {
            // 서버가 재시작 중이면 다음 확인 때 다시 시도
        }
    }

    check();
    setInterval(check, 10000);
})();
