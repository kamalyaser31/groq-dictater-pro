"""
audio_engine.py — محرك الصوت والنسخ السحابي عبر Groq API
يضم: دورة التسجيل الصوتي، وإرسال الصوت إلى Groq API لتحويل الكلام إلى نص.
"""

import io
import threading
import wave
from enum import Enum, auto
from typing import Callable, List, Optional

import pyaudio
import requests

from config import AppSettings, get_effective_api_key, log

# ---------------------------------------------------------------------------
# ثوابت الصوت
# ---------------------------------------------------------------------------
_AUDIO_FORMAT = pyaudio.paInt16
_CHANNELS = 1
_RATE = 16000
_CHUNK = 1024
MAX_AUDIO_BYTES = 20 * 1024 * 1024


class EngineState(Enum):
    IDLE = auto()
    RECORDING = auto()
    TRANSCRIBING = auto()
    SHUTTING_DOWN = auto()


def _build_transcription_payload(
    settings: AppSettings, api_key: str, buffer: io.BytesIO
) -> tuple[
    dict[str, str], dict[str, tuple[str, io.BytesIO, str]], dict[str, str]
]:
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": ("speech.wav", buffer, "audio/wav")}
    request_fields = {
        "model": settings.model_name,
        "language": settings.language,
    }
    if settings.initial_prompt.strip():
        request_fields["prompt"] = settings.initial_prompt.strip()
    return headers, files, request_fields


class AudioEngine:
    """
    يُدير التسجيل الصوتي وإرسال الملف إلى Groq API لتحويل الكلام إلى نص.
    """

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._state = EngineState.IDLE
        self._state_lock = threading.Lock()

        self._p_audio = pyaudio.PyAudio()
        self._sample_width = self._p_audio.get_sample_size(_AUDIO_FORMAT)
        self._stream: Optional[pyaudio.Stream] = None
        self._stream_lock = threading.Lock()
        self._frames: List[bytes] = []
        self._frames_lock = threading.Lock()
        self._record_thread: Optional[threading.Thread] = None
        self._transcription_thread: Optional[threading.Thread] = None
        self._recorded_bytes = 0

    @property
    def is_recording(self) -> bool:
        return self.state is EngineState.RECORDING

    @property
    def state(self) -> EngineState:
        with self._state_lock:
            return self._state

    def _replace_state(
        self, expected_state: EngineState, next_state: EngineState
    ) -> bool:
        with self._state_lock:
            if self._state is not expected_state:
                return False
            self._state = next_state
            return True

    def update_settings(self, new_settings: AppSettings) -> None:
        """تستبدل نسخة الإعدادات حين يكون المحرك ساكنًا."""
        if self.state is not EngineState.IDLE:
            raise RuntimeError("لا يمكن تغيير الإعدادات والمحرك مشغول")
        self.settings = new_settings

    # ------------------------------------------------------------------
    # التسجيل
    # ------------------------------------------------------------------
    def start_recording(
        self,
        on_capture_error: Callable[[Exception], None],
        on_recording_limit: Callable[[], None],
    ) -> bool:
        if not self._replace_state(EngineState.IDLE, EngineState.RECORDING):
            return False

        with self._frames_lock:
            self._frames = []
            self._recorded_bytes = 0

        try:
            stream = self._p_audio.open(
                format=_AUDIO_FORMAT,
                channels=_CHANNELS,
                rate=_RATE,
                input=True,
                frames_per_buffer=_CHUNK,
            )
        except OSError as exc:
            log.error("تعذَّر فتح جهاز الصوت: %s", exc)
            self._replace_state(EngineState.RECORDING, EngineState.IDLE)
            raise

        with self._stream_lock:
            self._stream = stream
        self._record_thread = threading.Thread(
            target=self._record_loop,
            args=(stream, on_capture_error, on_recording_limit),
        )
        self._record_thread.start()
        log.info("بدأ التسجيل.")
        return True

    def _record_loop(
        self,
        stream: pyaudio.Stream,
        on_capture_error: Callable[[Exception], None],
        on_recording_limit: Callable[[], None],
    ) -> None:
        while self.state is EngineState.RECORDING:
            try:
                chunk = stream.read(_CHUNK, exception_on_overflow=False)
                with self._frames_lock:
                    self._frames.append(chunk)
                    self._recorded_bytes += len(chunk)
                    reached_limit = self._recorded_bytes >= MAX_AUDIO_BYTES
                if reached_limit:
                    log.info("بلغ التسجيل حد الحجم الآمن؛ سيُوقف تلقائياً.")
                    on_recording_limit()
                    break
            except (OSError, AttributeError) as exc:
                if self._replace_state(
                    EngineState.RECORDING, EngineState.IDLE
                ):
                    log.error("توقف التقاط الصوت: %s", exc)
                    self._close_stream()
                    on_capture_error(exc)
                break
        log.debug("انتهى خيط التسجيل.")

    def stop_recording(
        self,
        on_result: Callable[[str], None],
        on_empty: Callable[[], None],
        on_error: Callable[[Exception], None],
    ) -> bool:
        if not self._replace_state(
            EngineState.RECORDING, EngineState.TRANSCRIBING
        ):
            return False

        self._transcription_thread = threading.Thread(
            target=self._transcribe_thread,
            args=(on_result, on_empty, on_error),
        )
        self._transcription_thread.start()
        log.info("طلب إيقاف التسجيل — بدأ خيط إرسال الصوت لـ Groq API.")
        return True

    def _close_stream(self) -> None:
        with self._stream_lock:
            stream = self._stream
            self._stream = None
        if stream is None:
            return
        try:
            if stream.is_active():
                stream.stop_stream()
            stream.close()
            log.info("تم إغلاق stream الصوت بنجاح.")
        except OSError as exc:
            log.warning("خطأ عند إغلاق stream الصوت: %s", exc)

    # ------------------------------------------------------------------
    # النسخ السحابي عبر Groq API
    # ------------------------------------------------------------------
    def _build_wav_buffer(self) -> io.BytesIO:
        with self._frames_lock:
            recorded_frames = self._frames
            self._frames = []
            self._recorded_bytes = 0

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_stream:
            wav_stream.setnchannels(_CHANNELS)
            wav_stream.setsampwidth(self._sample_width)
            wav_stream.setframerate(_RATE)
            for recorded_frame in recorded_frames:
                wav_stream.writeframesraw(recorded_frame)

        buffer.seek(0)
        return buffer

    def _transcribe_thread(
        self,
        on_result: Callable[[str], None],
        on_empty: Callable[[], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        try:
            if self._record_thread is not None:
                self._record_thread.join()
            self._close_stream()
            buffer = self._build_wav_buffer()
            transcribed_text = self._transcribe_api(buffer)
        except Exception as exc:
            log.error("خطأ في النسخ عبر Groq API: %s", exc)
            completion_callback = on_error
            callback_argument = exc
        else:
            if transcribed_text:
                log.info("نجح النسخ عبر Groq API.")
                completion_callback = on_result
                callback_argument = transcribed_text
            else:
                log.info("لم يُرصد صوت أو النص المنسوخ فارغ.")
                completion_callback = on_empty
                callback_argument = None

        should_notify = self._replace_state(
            EngineState.TRANSCRIBING, EngineState.IDLE
        )
        if not should_notify:
            return
        if callback_argument is None:
            completion_callback()
        else:
            completion_callback(callback_argument)

    def _transcribe_api(self, buffer: io.BytesIO) -> str:
        settings_snapshot = self.settings
        api_key = get_effective_api_key(settings_snapshot)
        if not api_key:
            raise ValueError(
                "مفتاح Groq API غير متوفر. احفظه في الإعدادات "
                "أو عيّن متغير البيئة GROQ_API_KEY."
            )

        headers, files, request_fields = _build_transcription_payload(
            settings_snapshot, api_key, buffer
        )
        log.info(
            "إرسال الصوت إلى Groq API [%s] بالنموذج [%s]...",
            settings_snapshot.api_url,
            settings_snapshot.model_name,
        )

        response = requests.post(
            settings_snapshot.api_url,
            headers=headers,
            files=files,
            data=request_fields,
            timeout=30,
        )

        if response.status_code != 200:
            error_msg = (
                f"فشل طلب Groq API (رمز الاستجابة: {response.status_code})"
            )
            log.error(error_msg)
            raise RuntimeError(error_msg)

        json_response = response.json()
        text = json_response.get("text", "")
        return str(text).strip()

    # ------------------------------------------------------------------
    # الإغلاق الآمن
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        with self._state_lock:
            self._state = EngineState.SHUTTING_DOWN
        self._close_stream()
        if self._record_thread and self._record_thread.is_alive():
            self._record_thread.join(timeout=1)
        if (
            self._transcription_thread
            and self._transcription_thread.is_alive()
        ):
            self._transcription_thread.join(timeout=1)
        try:
            self._p_audio.terminate()
        except OSError as exc:
            log.warning("خطأ عند إنهاء pyaudio: %s", exc)
        log.info("AudioEngine: أُغلق بسلام.")
