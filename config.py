"""
config.py — مركز الإعدادات والتهيئة
يضم: ثوابت المسارات، إعداد logging، AppSettings، load/save المخصص لـ Groq API.
"""

import base64
import binascii
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping

import pywintypes
import win32crypt

# ---------------------------------------------------------------------------
# ثوابت المسارات والخيارات الافتراضية الخاصة بـ Groq API
# ---------------------------------------------------------------------------
BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
APP_DATA_DIR = (
    Path(os.environ.get("LOCALAPPDATA", BASE_DIR)) / "GroqDictaterPro"
)
LOG_FILE = APP_DATA_DIR / "app.log"
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_MODEL_NAME = "whisper-large-v3-turbo"
ALLOWED_MODELS = ("whisper-large-v3-turbo", "whisper-large-v3")

LANGUAGES = ("ar", "en", "de", "fr", "es")
_PROTECTED_API_KEY_FIELD = "protected_api_key"

# ---------------------------------------------------------------------------
# إعداد نظام السجلات (logging)
# ---------------------------------------------------------------------------
log = logging.getLogger("groq_dictater")
log.addHandler(logging.NullHandler())
_logging_configured = False


def setup_logging() -> None:
    """تهيئ سجل الطرفية والملف مرة واحدة عند بدء التطبيق."""
    global _logging_configured
    if _logging_configured:
        return

    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.addHandler(stream_handler)
    log.addHandler(file_handler)
    log.propagate = False
    _logging_configured = True


# ---------------------------------------------------------------------------
# نموذج الإعدادات
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AppSettings:
    hotkey: str = "f8"
    api_key: str = ""
    api_url: str = DEFAULT_API_URL
    model_name: str = DEFAULT_MODEL_NAME
    language: str = "ar"
    auto_paste: bool = False
    sound_enabled: bool = True
    stay_on_top: bool = False
    initial_prompt: str = "اللغة العربية، فصحى، مصطلحات إسلامية، تعليم"


def get_effective_api_key(settings: AppSettings) -> str:
    """
    تستخرج مفتاح Groq من الضبط أو من متغير البيئة GROQ_API_KEY.
    """
    if settings.api_key.strip():
        return settings.api_key.strip()
    return os.environ.get("GROQ_API_KEY", "").strip()


def _protect_api_key(api_key: str) -> str:
    protected_bytes = win32crypt.CryptProtectData(
        api_key.encode("utf-8"),
        "Groq Dictater Pro API key",
        None,
        None,
        None,
        0,
    )
    return base64.b64encode(protected_bytes).decode("ascii")


def _unprotect_api_key(protected_api_key: object) -> str:
    if not isinstance(protected_api_key, str) or not protected_api_key:
        return ""
    try:
        protected_bytes = base64.b64decode(protected_api_key, validate=True)
        return win32crypt.CryptUnprotectData(
            protected_bytes, None, None, None, 0
        )[1].decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, pywintypes.error) as exc:
        log.error("تعذَّر فك مفتاح API المحفوظ: %s", exc)
        return ""


def settings_from_mapping(raw_settings: Mapping[str, object]) -> AppSettings:
    """ينشئ ضبطاً صحيحاً متجاهلاً القيم التالفة والنماذج القديمة."""
    validated_settings = asdict(AppSettings())
    for setting_name, default_setting in tuple(validated_settings.items()):
        candidate_setting = raw_settings.get(setting_name, default_setting)
        if type(candidate_setting) is not type(default_setting):
            continue
        if setting_name == "language" and candidate_setting not in LANGUAGES:
            continue
        if setting_name == "hotkey" and not str(candidate_setting).strip():
            continue
        if (
            setting_name == "model_name"
            and candidate_setting not in ALLOWED_MODELS
        ):
            # تصحيح تلقائي إذا كانت هناك قيمة قديمة مخزنة مثل "base" أو "tiny"
            candidate_setting = DEFAULT_MODEL_NAME
        validated_settings[setting_name] = candidate_setting
    return AppSettings(**validated_settings)


# ---------------------------------------------------------------------------
# دوال التحميل والحفظ
# ---------------------------------------------------------------------------
def load_settings() -> AppSettings:
    """
    تُحمِّل الإعدادات من CONFIG_FILE وتتحقق من قيمها.
    عند أي فشل تُعيد AppSettings الافتراضية وتُسجِّل الخطأ.
    """
    if not CONFIG_FILE.exists():
        return AppSettings()
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as settings_stream:
            stored_settings = json.load(settings_stream)
        if not isinstance(stored_settings, dict):
            raise ValueError("جذر ملف الإعدادات ليس JSON object")
        if not stored_settings.get("api_key"):
            stored_settings["api_key"] = _unprotect_api_key(
                stored_settings.get(_PROTECTED_API_KEY_FIELD)
            )
        return settings_from_mapping(stored_settings)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        log.error("فشل تحميل الإعدادات — العودة للافتراضيات. السبب: %s", exc)
        return AppSettings()


def save_settings(settings: AppSettings) -> bool:
    """
    تحفظ الإعدادات في CONFIG_FILE بترميز UTF-8.
    تُعيد True عند النجاح، False عند الفشل.
    """
    temporary_file = CONFIG_FILE.with_suffix(".tmp")
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        stored_settings = asdict(settings)
        api_key = stored_settings.pop("api_key")
        stored_settings[_PROTECTED_API_KEY_FIELD] = (
            _protect_api_key(api_key) if api_key else ""
        )
        with temporary_file.open("w", encoding="utf-8") as settings_stream:
            json.dump(
                stored_settings,
                settings_stream,
                ensure_ascii=False,
                indent=4,
            )
        os.replace(temporary_file, CONFIG_FILE)
        return True
    except (OSError, pywintypes.error) as exc:
        log.error("فشل حفظ الإعدادات: %s", exc)
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def setup_runtime() -> None:
    """يهيئ الموارد التشغيلية قبل إنشاء الواجهة."""
    setup_logging()
