"""
gui.py — الواجهة الرسومية ونقطة الدخول
يضم: SettingsDialog، DictationFrame، مع توافق كامل لقارئات الشاشة.
"""

import threading
import time
import winsound
from typing import Callable

import keyboard
import pyperclip
import wx

from audio_engine import AudioEngine, EngineState
from config import (
    ALLOWED_MODELS,
    DEFAULT_API_URL,
    DEFAULT_MODEL_NAME,
    LANGUAGES,
    AppSettings,
    get_effective_api_key,
    load_settings,
    log,
    save_settings,
    setup_runtime,
)


# ---------------------------------------------------------------------------
# SettingsDialog
# ---------------------------------------------------------------------------
class SettingsDialog(wx.Dialog):
    """نافذة الإعدادات المتقدمة وإدارة مفتاح الـ API."""

    def __init__(
        self, parent: wx.Window, current_settings: AppSettings
    ) -> None:
        super().__init__(
            parent, title="إعدادات الـ API والإملاء", size=(460, 560)
        )
        self.settings = current_settings
        self._init_ui()

    def _init_ui(self) -> None:
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(9, 2, 12, 12)
        grid.AddGrowableCol(1, 1)

        # --- حقول الإدخال ---
        lbl_hotkey = wx.StaticText(panel, label="اختصار الإملاء (Hotkey):")
        self._txt_hotkey = wx.TextCtrl(panel, value=self.settings.hotkey)

        lbl_api_key = wx.StaticText(panel, label="مفتاح الـ API (API Key):")
        self._txt_api_key = wx.TextCtrl(
            panel, value=self.settings.api_key, style=wx.TE_PASSWORD
        )

        lbl_api_url = wx.StaticText(panel, label="رابط الخدمة (API Endpoint):")
        self._txt_api_url = wx.TextCtrl(panel, value=self.settings.api_url)

        lbl_model = wx.StaticText(panel, label="طراز النموذج (Model):")
        self._cmb_model = wx.ComboBox(
            panel,
            choices=list(ALLOWED_MODELS),
            style=wx.CB_READONLY,
            value=(
                self.settings.model_name
                if self.settings.model_name in ALLOWED_MODELS
                else DEFAULT_MODEL_NAME
            ),
        )

        lbl_lang = wx.StaticText(panel, label="اللغة المتوقعة:")
        self._cmb_lang = wx.ComboBox(
            panel,
            choices=LANGUAGES,
            style=wx.CB_READONLY,
            value=self.settings.language,
        )

        self._chk_paste = wx.CheckBox(
            panel, label="لصق تلقائي بعد النسخ (Ctrl+V)"
        )
        self._chk_paste.SetValue(self.settings.auto_paste)

        self._chk_sound = wx.CheckBox(
            panel, label="تفعيل أصوات التنبيه الصوتي"
        )
        self._chk_sound.SetValue(self.settings.sound_enabled)

        self._chk_top = wx.CheckBox(
            panel, label="إبقاء النافذة دائماً في المقدمة"
        )
        self._chk_top.SetValue(self.settings.stay_on_top)

        lbl_prompt = wx.StaticText(panel, label="الموجه الابتدائي (Prompt):")
        self._txt_prompt = wx.TextCtrl(
            panel, value=self.settings.initial_prompt
        )

        self._txt_hotkey.SetName(lbl_hotkey.GetLabel())
        self._txt_api_key.SetName(lbl_api_key.GetLabel())
        self._txt_api_url.SetName(lbl_api_url.GetLabel())
        self._cmb_model.SetName(lbl_model.GetLabel())
        self._cmb_lang.SetName(lbl_lang.GetLabel())
        self._txt_prompt.SetName(lbl_prompt.GetLabel())

        rows = [
            (lbl_hotkey, self._txt_hotkey),
            (lbl_api_key, self._txt_api_key),
            (lbl_api_url, self._txt_api_url),
            (lbl_model, self._cmb_model),
            (lbl_lang, self._cmb_lang),
            (wx.StaticText(panel, label=""), self._chk_paste),
            (wx.StaticText(panel, label=""), self._chk_sound),
            (wx.StaticText(panel, label=""), self._chk_top),
            (lbl_prompt, self._txt_prompt),
        ]

        for label_ctrl, ctrl in rows:
            grid.Add(label_ctrl, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)

        vbox.Add(grid, 1, wx.ALL | wx.EXPAND, 20)

        btn_sizer = wx.StdDialogButtonSizer()
        btn_ok = wx.Button(panel, wx.ID_OK, label="حفظ الإعدادات")
        btn_ok.SetDefault()
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, label="إلغاء")
        btn_sizer.AddButton(btn_ok)
        btn_sizer.AddButton(btn_cancel)
        btn_sizer.Realize()
        vbox.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.BOTTOM, 15)

        panel.SetSizer(vbox)

    def get_settings(self) -> AppSettings:
        """تُعيد الإعدادات المُعدَّلة من حقول الإدخال."""
        selected_model = self._cmb_model.GetValue().strip()
        if not selected_model:
            selected_model = DEFAULT_MODEL_NAME

        return AppSettings(
            hotkey=self._txt_hotkey.GetValue().strip().lower(),
            api_key=self._txt_api_key.GetValue().strip(),
            api_url=self._txt_api_url.GetValue().strip() or DEFAULT_API_URL,
            model_name=selected_model,
            language=self._cmb_lang.GetValue(),
            auto_paste=self._chk_paste.GetValue(),
            sound_enabled=self._chk_sound.GetValue(),
            stay_on_top=self._chk_top.GetValue(),
            initial_prompt=self._txt_prompt.GetValue(),
        )


# ---------------------------------------------------------------------------
# DictationFrame
# ---------------------------------------------------------------------------
_DEBOUNCE_SECS = 0.5


class DictationFrame(wx.Frame):
    """النافذة الرئيسية لتطبيق الإملاء السحابي."""

    def __init__(self) -> None:
        self.settings = load_settings()
        frame_style = wx.DEFAULT_FRAME_STYLE
        if self.settings.stay_on_top:
            frame_style |= wx.STAY_ON_TOP

        super().__init__(
            None,
            title="Groq Dictater Pro",
            size=(500, 650),
            style=frame_style,
        )

        self._engine = AudioEngine(self.settings)
        self._last_toggle: float = 0.0
        self._hotkey_hook = None
        self._closing = False
        self._settings_open = False

        self._init_ui()
        self._replace_hotkey(self.settings.hotkey)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self._check_initial_api_key()

    # ------------------------------------------------------------------
    # بناء الواجهة
    # ------------------------------------------------------------------
    def _init_ui(self) -> None:
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        # شريط الحالة
        self._st_status = wx.StaticText(
            panel, label="جاهز للإملاء (Groq API)", style=wx.ALIGN_CENTER
        )
        self._st_status.SetFont(
            wx.Font(
                14,
                wx.FONTFAMILY_DEFAULT,
                wx.FONTSTYLE_NORMAL,
                wx.FONTWEIGHT_BOLD,
            )
        )
        vbox.Add(self._st_status, flag=wx.ALIGN_CENTER | wx.TOP, border=25)

        # زر التبديل
        hotkey_label = self.settings.hotkey.upper()
        self._btn_toggle = wx.Button(
            panel,
            label=f"بدء الإملاء ({hotkey_label})",
            size=(240, 75),
        )
        self._btn_toggle.SetBackgroundColour(wx.Colour(0, 150, 0))
        self._btn_toggle.SetForegroundColour(wx.WHITE)
        self._btn_toggle.Bind(wx.EVT_BUTTON, lambda _: self._toggle_action())
        vbox.Add(self._btn_toggle, flag=wx.ALIGN_CENTER | wx.TOP, border=20)

        # زر الإعدادات
        self._btn_settings = wx.Button(panel, label="الإعدادات ومفتاح الـ API")
        self._btn_settings.Bind(wx.EVT_BUTTON, self._on_open_settings)
        vbox.Add(
            self._btn_settings,
            flag=wx.ALIGN_CENTER | wx.TOP,
            border=10,
        )

        # سجل النصوص
        lbl_history = wx.StaticText(panel, label="سجل النصوص المنسوخة:")
        vbox.Add(
            lbl_history,
            flag=wx.LEFT | wx.TOP,
            border=20,
        )
        self._text_log = wx.TextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2
        )
        self._text_log.SetDefaultStyle(
            wx.TextAttr(wx.NullColour, alignment=wx.TEXT_ALIGNMENT_RIGHT)
        )
        vbox.Add(
            self._text_log, proportion=1, flag=wx.EXPAND | wx.ALL, border=20
        )

        panel.SetSizer(vbox)
        self.Centre()

    def _check_initial_api_key(self) -> None:
        """تتحقق من توفر مفتاح الـ API وتُنبّه إن كان مفقوداً."""
        effective_key = get_effective_api_key(self.settings)
        if not effective_key:
            self._set_status(
                "لم يُضبط مفتاح API. افتح الإعدادات لإدخاله.",
                colour=wx.Colour(200, 100, 0),
            )

    # ------------------------------------------------------------------
    # التبديل (بدء / إيقاف)
    # ------------------------------------------------------------------
    def _toggle_action(self) -> None:
        """يُطبِّق debounce ثم يُبدِّل حالة التسجيل."""
        if self._closing or self._settings_open:
            return
        now = time.monotonic()
        if now - self._last_toggle < _DEBOUNCE_SECS:
            return
        self._last_toggle = now

        if self._engine.state is EngineState.RECORDING:
            self._stop_recording()
        elif self._engine.state is EngineState.IDLE:
            self._start_recording()

    def _start_recording(self) -> None:
        effective_key = get_effective_api_key(self.settings)
        if not effective_key:
            self._set_status("يرجى ضبط مفتاح API في الإعدادات أولاً")
            self._beep_error()
            return
        try:
            started = self._engine.start_recording(
                on_capture_error=lambda exc: self._post_to_gui(
                    self._on_capture_error, exc
                ),
                on_recording_limit=lambda: self._post_to_gui(
                    self._on_recording_limit
                ),
            )
        except OSError:
            self._set_status("خطأ في جهاز الصوت")
            self._btn_toggle.SetBackgroundColour(wx.Colour(0, 150, 0))
            return
        if not started:
            return
        self._beep(1000)
        self._set_status("جارٍ التسجيل...", colour=wx.RED)
        self._btn_toggle.SetBackgroundColour(wx.Colour(200, 0, 0))
        self._btn_settings.Disable()

    def _stop_recording(self) -> bool:
        stopped = self._engine.stop_recording(
            on_result=lambda text: self._post_to_gui(
                self._on_transcription_result, text
            ),
            on_empty=lambda: self._post_to_gui(
                self._on_transcription_finish, "لم يُرصد صوت"
            ),
            on_error=lambda exc: self._post_to_gui(
                self._on_transcription_error, exc
            ),
        )
        if not stopped:
            return False
        self._beep(600)
        self._set_status("جارٍ الإرسال والنسخ عبر Groq API...")
        self._btn_toggle.SetBackgroundColour(wx.Colour(0, 150, 0))
        self._btn_toggle.Disable()
        self._btn_settings.Disable()
        return True

    def _on_recording_limit(self) -> None:
        if self._stop_recording():
            self._set_status("بلغ التسجيل الحد الآمن؛ جارٍ نسخه...")

    def _on_capture_error(self, _exc: Exception) -> None:
        self._set_status("انقطع التقاط الصوت", colour=wx.RED)
        self._btn_toggle.SetBackgroundColour(wx.Colour(0, 150, 0))
        self._btn_toggle.Enable()
        self._btn_settings.Enable()

    def _on_transcription_error(self, exc: Exception) -> None:
        self._beep_error()
        error_msg = str(exc)
        if "401" in error_msg or "Unauthorized" in error_msg:
            status_text = "مفتاح API غير صحيح (401)"
        elif "429" in error_msg:
            status_text = "تجاوز حد الاستخدام (429 Rate Limit)"
        elif "model_not_found" in error_msg or "404" in error_msg:
            status_text = "اسم النموذج غير مدعوم في Groq API (404)"
        else:
            status_text = "خطأ في الاتصال بـ Groq API"
        self._append_log(f"خطأ: {error_msg}")
        self._on_transcription_finish(status_text, colour=wx.RED)

    # ------------------------------------------------------------------
    # معالجة نتيجة النسخ
    # ------------------------------------------------------------------
    def _on_transcription_result(self, text: str) -> None:
        copied_to_clipboard = True
        try:
            pyperclip.copy(text)
        except pyperclip.PyperclipException as exc:
            copied_to_clipboard = False
            log.warning("تعذّر نسخ النص إلى الحافظة: %s", exc)
        if self.settings.auto_paste and copied_to_clipboard:
            threading.Thread(
                target=self._paste_clipboard_text, daemon=True
            ).start()
        self._append_log(text)
        self._beep(1500)
        if copied_to_clipboard:
            self._on_transcription_finish(
                "نُسخ النص إلى الحافظة", colour=wx.Colour(0, 150, 0)
            )
        else:
            self._on_transcription_finish("اكتمل النسخ وتعذر وضعه في الحافظة")

    def _on_transcription_finish(
        self,
        status_text: str,
        colour: wx.Colour = wx.NullColour,
    ) -> None:
        """تُعيد تفعيل الواجهة بعد انتهاء المعالجة."""
        self._set_status(status_text, colour=colour)
        self._btn_toggle.Enable()
        self._btn_settings.Enable()

    def _paste_clipboard_text(self) -> None:
        """يُنفِّذ Ctrl+V في خيط مستقل بعد تأخير كافٍ لضمان التركيز."""
        time.sleep(0.25)
        try:
            keyboard.press_and_release("ctrl+v")
        except (KeyError, ValueError, TypeError, OSError) as exc:
            log.warning("فشل اللصق التلقائي: %s", exc)

    # ------------------------------------------------------------------
    # أدوات مساعدة
    # ------------------------------------------------------------------
    def _post_to_gui(
        self,
        callback: Callable[..., None],
        *callback_arguments: object,
    ) -> None:
        """يرسل callback إلى wx ما دامت النافذة حية."""
        if self._closing:
            return

        def invoke_if_open() -> None:
            if not self._closing:
                callback(*callback_arguments)

        wx.CallAfter(invoke_if_open)

    def _set_status(
        self, label: str, colour: wx.Colour = wx.NullColour
    ) -> None:
        """تحدّث الحالة ولونها؛ ولا تُستدعى إلا في خيط GUI."""
        self._st_status.SetLabel(label)
        if colour != wx.NullColour:
            self._st_status.SetForegroundColour(colour)
        else:
            self._st_status.SetForegroundColour(
                wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT)
            )

    def _beep(self, freq: int) -> None:
        """يُصدر صوت تنبيه إن كان مُفعَّلاً في الإعدادات."""
        if self.settings.sound_enabled:
            winsound.Beep(freq, 150)

    def _beep_error(self) -> None:
        """يُصدر نغمة خطأ تنبيهية."""
        if self.settings.sound_enabled:
            winsound.Beep(400, 300)

    def _append_log(self, text: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self._text_log.AppendText(f"[{timestamp}] {text}\n\n")

    # ------------------------------------------------------------------
    # الـ Hotkey
    # ------------------------------------------------------------------
    def _replace_hotkey(self, hotkey: str) -> bool:
        """يتحقق من الاختصار الجديد قبل نزع الاختصار العامل."""
        if not hotkey:
            self._show_hotkey_error("لا يجوز أن يكون الاختصار فارغًا")
            return False
        try:
            new_hotkey_hook = keyboard.add_hotkey(
                hotkey, lambda: self._post_to_gui(self._toggle_action)
            )
        except (KeyError, ValueError, TypeError, OSError) as exc:
            self._show_hotkey_error(str(exc))
            return False

        self._remove_hotkey()
        self._hotkey_hook = new_hotkey_hook
        self._btn_toggle.SetLabel(f"بدء/إيقاف الإملاء ({hotkey.upper()})")
        return True

    def _remove_hotkey(self) -> None:
        if self._hotkey_hook is None:
            return
        try:
            keyboard.remove_hotkey(self._hotkey_hook)
        except (KeyError, ValueError):
            pass
        self._hotkey_hook = None

    def _show_hotkey_error(self, error_detail: str) -> None:
        wx.MessageBox(
            f"تعذّر اعتماد الاختصار:\n{error_detail}",
            "اختصار غير صالح",
            wx.OK | wx.ICON_ERROR,
            self,
        )

    # ------------------------------------------------------------------
    # الإعدادات
    # ------------------------------------------------------------------
    def _on_open_settings(self, _event: wx.Event) -> None:
        dlg = SettingsDialog(self, self.settings)
        self._settings_open = True
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            new_settings = dlg.get_settings()
            if (
                new_settings.hotkey != self.settings.hotkey
                and not self._replace_hotkey(new_settings.hotkey)
            ):
                return
            self._apply_settings(new_settings)
        finally:
            self._settings_open = False
            dlg.Destroy()

    def _apply_settings(self, new_settings: AppSettings) -> None:
        old_settings = self.settings
        self.settings = new_settings
        self._engine.update_settings(new_settings)
        if old_settings.stay_on_top != new_settings.stay_on_top:
            self._apply_topmost_style(new_settings.stay_on_top)
        if not save_settings(new_settings):
            wx.MessageBox(
                "تعذَّر حفظ الإعدادات — راجع الصلاحيات.",
                "تحذير",
                wx.OK | wx.ICON_WARNING,
                self,
            )
        effective_key = get_effective_api_key(new_settings)
        if effective_key:
            self._set_status(
                "جاهز للإملاء (Groq API)", colour=wx.Colour(0, 150, 0)
            )
        else:
            self._set_status(
                "لم يُضبط مفتاح API.", colour=wx.Colour(200, 100, 0)
            )

    def _apply_topmost_style(self, stay_on_top: bool) -> None:
        frame_style = self.GetWindowStyle()
        if stay_on_top:
            self.SetWindowStyle(frame_style | wx.STAY_ON_TOP)
        else:
            self.SetWindowStyle(frame_style & ~wx.STAY_ON_TOP)

    # ------------------------------------------------------------------
    # الإغلاق
    # ------------------------------------------------------------------
    def _on_close(self, event: wx.CloseEvent) -> None:
        self._closing = True
        self._remove_hotkey()
        self._engine.shutdown()
        event.Skip()


# ---------------------------------------------------------------------------
# نقطة الدخول
# ---------------------------------------------------------------------------
def main() -> None:
    setup_runtime()
    app = wx.App()
    frame = DictationFrame()
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
