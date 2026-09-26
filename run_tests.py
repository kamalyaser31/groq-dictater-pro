import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import audio_engine
import config
import gui
from audio_engine import AudioEngine
from config import AppSettings


class FakeStream:
    def __init__(self) -> None:
        self.active = True

    def read(self, _chunk_size: int, exception_on_overflow: bool) -> bytes:
        time.sleep(0.001)
        return b"\0" * 2048

    def is_active(self) -> bool:
        return self.active

    def stop_stream(self) -> None:
        self.active = False

    def close(self) -> None:
        self.active = False


class FakePyAudio:
    def __init__(self) -> None:
        self.stream = FakeStream()

    def open(self, **_stream_options: object) -> FakeStream:
        return self.stream

    def get_sample_size(self, _audio_format: int) -> int:
        return 2

    def terminate(self) -> None:
        pass


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> dict[str, str]:
        return {"text": "نص صحيح"}


def new_audio_engine() -> AudioEngine:
    with patch.object(
        audio_engine.pyaudio, "PyAudio", return_value=FakePyAudio()
    ):
        return AudioEngine(AppSettings(api_key="test-key"))


class ConfigTests(unittest.TestCase):
    def test_2026_07_25_settings_file_is_beside_application(self) -> None:
        self.assertEqual(config.CONFIG_FILE, config.BASE_DIR / "config.json")

    def test_saved_api_key_is_encrypted_and_can_be_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings_directory = Path(temporary_directory)
            config_file = settings_directory / "config.json"
            with (
                patch.object(config, "APP_DATA_DIR", settings_directory),
                patch.object(config, "CONFIG_FILE", config_file),
            ):
                settings = AppSettings(api_key="private-api-key")
                self.assertTrue(config.save_settings(settings))
                stored_settings = json.loads(config_file.read_text("utf-8"))
                self.assertNotIn("api_key", stored_settings)
                self.assertNotIn(
                    "private-api-key", config_file.read_text("utf-8")
                )
                self.assertEqual(
                    config.load_settings().api_key, "private-api-key"
                )

    def test_app_data_dir_and_logger_identity(self) -> None:
        self.assertEqual(config.APP_DATA_DIR.name, "GroqDictaterPro")
        self.assertEqual(config.log.name, "groq_dictater")

    def test_invalid_model_reverts_to_default(self) -> None:
        settings = config.settings_from_mapping(
            {"model_name": "invalid-model"}
        )
        self.assertEqual(settings.model_name, config.DEFAULT_MODEL_NAME)


class AudioEngineTests(unittest.TestCase):
    def test_recording_size_limit_stops_capture_and_transcribes_audio(
        self,
    ) -> None:
        engine = new_audio_engine()
        recording_limit_reached = threading.Event()
        transcription_finished = threading.Event()
        transcribed_text: list[str] = []

        def store_transcription(text: str) -> None:
            transcribed_text.append(text)
            transcription_finished.set()

        with (
            patch.object(audio_engine, "MAX_AUDIO_BYTES", 4096),
            patch.object(
                audio_engine.requests,
                "post",
                return_value=FakeResponse(),
            ),
        ):
            self.assertTrue(
                engine.start_recording(
                    on_capture_error=lambda _exc: self.fail(
                        "unexpected capture error"
                    ),
                    on_recording_limit=recording_limit_reached.set,
                )
            )
            self.assertTrue(recording_limit_reached.wait(1))
            self.assertTrue(
                engine.stop_recording(
                    on_result=store_transcription,
                    on_empty=lambda: self.fail("unexpected empty audio"),
                    on_error=lambda _exc: self.fail(
                        "unexpected transcription error"
                    ),
                )
            )
            self.assertTrue(transcription_finished.wait(1))

        self.assertEqual(transcribed_text, ["نص صحيح"])
        engine.shutdown()

    def test_api_error_does_not_expose_server_response_body(self) -> None:
        engine = new_audio_engine()
        private_server_detail = "private diagnostic detail"
        transcription_failed = threading.Event()
        transcription_errors: list[Exception] = []

        def store_transcription_error(exc: Exception) -> None:
            transcription_errors.append(exc)
            transcription_failed.set()

        with patch.object(
            audio_engine.requests,
            "post",
            return_value=FakeResponse(401, private_server_detail),
        ):
            self.assertTrue(
                engine.start_recording(
                    on_capture_error=lambda _exc: self.fail(
                        "unexpected capture error"
                    ),
                    on_recording_limit=lambda: self.fail(
                        "unexpected recording limit"
                    ),
                )
            )
            self.assertTrue(
                engine.stop_recording(
                    on_result=lambda _text: self.fail(
                        "unexpected transcription"
                    ),
                    on_empty=lambda: self.fail("unexpected empty audio"),
                    on_error=store_transcription_error,
                )
            )
            self.assertTrue(transcription_failed.wait(1))

        error_message = str(transcription_errors[0])
        self.assertIn("401", error_message)
        self.assertNotIn(private_server_detail, error_message)
        engine.shutdown()

    def test_shutdown_waits_for_active_transcription_thread(self) -> None:
        engine = new_audio_engine()
        request_completed = threading.Event()

        def delayed_response(
            _api_url: str, **_request_arguments: object
        ) -> FakeResponse:
            time.sleep(0.05)
            request_completed.set()
            return FakeResponse()

        with patch.object(
            audio_engine.requests, "post", side_effect=delayed_response
        ):
            self.assertTrue(
                engine.start_recording(
                    on_capture_error=lambda _exc: self.fail(
                        "unexpected capture error"
                    ),
                    on_recording_limit=lambda: self.fail(
                        "unexpected recording limit"
                    ),
                )
            )
            self.assertTrue(
                engine.stop_recording(
                    on_result=lambda _text: None,
                    on_empty=lambda: None,
                    on_error=lambda _exc: None,
                )
            )
            engine.shutdown()

        self.assertTrue(request_completed.is_set())

    def test_build_transcription_payload_contains_expected_fields(
        self,
    ) -> None:
        settings = AppSettings(
            model_name="whisper-large-v3",
            language="ar",
            initial_prompt="فحص صوتي",
        )
        buffer = io.BytesIO(b"fake-audio-bytes")
        headers, files, fields = audio_engine._build_transcription_payload(
            settings, "gsk_test123", buffer
        )
        self.assertEqual(headers["Authorization"], "Bearer gsk_test123")
        self.assertEqual(fields["model"], "whisper-large-v3")
        self.assertEqual(fields["language"], "ar")
        self.assertEqual(fields["prompt"], "فحص صوتي")
        self.assertIn("file", files)
        self.assertEqual(files["file"][0], "speech.wav")
        self.assertEqual(files["file"][2], "audio/wav")


class GuiConcurrencyTests(unittest.TestCase):
    def test_hotkey_is_ignored_while_settings_dialog_is_open(self) -> None:
        frame_probe = SimpleNamespace(
            _closing=False,
            _settings_open=True,
            _start_recording=Mock(),
            _stop_recording=Mock(),
        )

        gui.DictationFrame._toggle_action(frame_probe)

        frame_probe._start_recording.assert_not_called()
        frame_probe._stop_recording.assert_not_called()

    def test_queued_gui_callback_is_ignored_after_close(self) -> None:
        queued_callbacks: list[object] = []
        callback = Mock()
        frame_probe = SimpleNamespace(_closing=False)

        with patch.object(
            gui.wx,
            "CallAfter",
            side_effect=lambda queued_callback: queued_callbacks.append(
                queued_callback
            ),
        ):
            gui.DictationFrame._post_to_gui(frame_probe, callback)

        frame_probe._closing = True
        queued_callbacks[0]()
        callback.assert_not_called()

    def test_toggle_button_label_updates_on_recording_state(self) -> None:
        frame_probe = SimpleNamespace(
            settings=AppSettings(hotkey="f8", api_key="valid-key"),
            _closing=False,
            _settings_open=False,
            _st_status=Mock(),
            _set_status=Mock(),
            _btn_toggle=Mock(),
            _btn_settings=Mock(),
            _beep=Mock(),
            _engine=Mock(),
        )
        frame_probe._engine.start_recording.return_value = True

        gui.DictationFrame._start_recording(frame_probe)
        frame_probe._btn_toggle.SetLabel.assert_called_with(
            "إيقاف الإملاء (F8)"
        )

        gui.DictationFrame._on_transcription_finish(frame_probe, "اكتمل")
        frame_probe._btn_toggle.SetLabel.assert_called_with("بدء الإملاء (F8)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
