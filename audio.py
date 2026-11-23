from threading import Lock


class AudioFanout:
    """오디오 조각(960샘플, int16, 48kHz mono)을 듣고 있는 모든 연결에 나눠 보냄

    on_first_listener: 첫 번째로 듣는 사람이 생겼을 때 부를 함수 (예: 그때서야 카메라에 연결)
    """

    def __init__(self, on_first_listener=None):
        self._listeners = set()
        self._lock = Lock()
        self._first_callback = on_first_listener

    def add_listener(self, callback):
        with self._lock:
            self._listeners.add(callback)
            first = len(self._listeners) == 1
        if first:
            self._on_first_listener()

    def remove_listener(self, callback):
        with self._lock:
            self._listeners.discard(callback)

    def has_listeners(self):
        with self._lock:
            return bool(self._listeners)

    def dispatch(self, data):
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            callback(data)

    def _on_first_listener(self):
        if self._first_callback is not None:
            self._first_callback()
