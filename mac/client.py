"""Диктовка — клиент на маке.

Хоткей-переключатель: нажал → запись с микрофона (AVFoundation); нажал ещё раз →
стоп, аудио уходит по SSH-туннелю на домашний ПК (GPU whisper), текст вставляется
под курсор. Запись через AVFoundation (а не PortAudio) — чтобы macOS корректно
показал запрос доступа к микрофону и отдавал реальный звук.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import tomllib

import httpx
import rumps
from pynput import keyboard

import AVFoundation as AV
import Quartz
from Foundation import NSURL

def _load_cfg() -> dict:
    # в .app __file__ внутри бандла — поэтому ищем конфиг и в ~/dictation-mac
    for p in (os.path.expanduser("~/dictation-mac/config.toml"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "config.toml")):
        try:
            with open(p, "rb") as f:
                return tomllib.load(f)
        except OSError:
            continue
    return {}


CFG = _load_cfg()

SERVER_URL = CFG.get("server_url", "http://127.0.0.1:9876")
HOTKEY = CFG.get("hotkey", "`")
SOUND = bool(CFG.get("sound", True))
RESTORE_CLIPBOARD = bool(CFG.get("restore_clipboard", True))

REC_PATH = "/tmp/dictation_rec.wav"
K_LINEAR_PCM = 1819304813  # kAudioFormatLinearPCM ('lpcm')

ICON_IDLE = "🎙"
ICON_REC = "🔴"
ICON_WORK = "⏳"
ICON_ERR = "⚠️"


def log(msg: str) -> None:
    line = f"[dictation] {msg}"
    print(line, flush=True)
    try:  # в .app stdout уходит в никуда — дублируем в файл; encoding обязателен
        with open("/tmp/dictation-client.out", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _play(name: str) -> None:
    if not SOUND:
        return
    subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _request_mic() -> None:
    """Явно запросить доступ к микрофону — AVFoundation покажет системное окно."""
    def handler(granted: bool) -> None:
        log(f"доступ к микрофону: {'дан' if granted else 'отказано'}")
    AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AV.AVMediaTypeAudio, handler)


class AVRecorder:
    """Запись с микрофона через AVAudioRecorder в WAV 16кГц/моно/16-бит."""

    def __init__(self) -> None:
        self._rec = None

    def start(self) -> None:
        try:
            os.remove(REC_PATH)
        except OSError:
            pass
        url = NSURL.fileURLWithPath_(REC_PATH)
        settings = {
            AV.AVFormatIDKey: K_LINEAR_PCM,
            AV.AVSampleRateKey: 16000.0,
            AV.AVNumberOfChannelsKey: 1,
            AV.AVLinearPCMBitDepthKey: 16,
            AV.AVLinearPCMIsFloatKey: False,
            AV.AVLinearPCMIsBigEndianKey: False,
        }
        rec, err = AV.AVAudioRecorder.alloc().initWithURL_settings_error_(
            url, settings, None)
        if rec is None:
            raise RuntimeError(f"AVAudioRecorder init: {err}")
        rec.record()
        self._rec = rec

    def stop(self) -> str:
        if self._rec is not None:
            self._rec.stop()
            self._rec = None
        return REC_PATH


def _paste(text: str) -> None:
    old = b""
    if RESTORE_CLIPBOARD:
        old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))
    time.sleep(0.05)
    # нативный Cmd+V через Quartz CGEvent (надёжнее pynput-эмуляции)
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for is_down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(src, 9, is_down)  # 9 = клавиша 'v'
        Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    if RESTORE_CLIPBOARD:
        time.sleep(0.4)
        subprocess.run(["pbcopy"], input=old)


class Dictation(rumps.App):
    def __init__(self) -> None:
        super().__init__(ICON_IDLE, quit_button=None)
        self.recording = False
        self.rec = AVRecorder()
        self.menu = [
            rumps.MenuItem("Старт / Стоп", callback=lambda _: self.toggle()),
            rumps.MenuItem("🎤 Тест: записать 4 сек",
                           callback=lambda _: self.test_record()),
            None,
            rumps.MenuItem("Выход", callback=lambda _: rumps.quit_application()),
        ]
        try:
            self._hotkeys = keyboard.GlobalHotKeys({HOTKEY: self.toggle})
            self._hotkeys.start()
            log(f"запущен; хоткей={HOTKEY}, сервер={SERVER_URL}")
        except Exception as exc:  # noqa: BLE001
            log(f"хоткей не запустился: {exc}")
        threading.Timer(1.0, _request_mic).start()  # запрос доступа при старте

    # --- запись ---
    def toggle(self) -> None:
        if not self.recording:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        try:
            self.rec.start()
        except Exception as exc:  # noqa: BLE001
            log(f"запись не началась: {exc}")
            self._notify("Не удалось начать запись", str(exc)[:120])
            self._set_icon(ICON_ERR, reset=True)
            return
        self.recording = True
        self._set_icon(ICON_REC)
        _play("Tink")
        log("запись началась")

    def _stop(self) -> None:
        self.recording = False
        path = self.rec.stop()
        self._set_icon(ICON_WORK)
        _play("Pop")
        threading.Thread(target=self._process, args=(path,), daemon=True).start()

    def test_record(self) -> None:
        threading.Thread(target=self._test, daemon=True).start()

    def _test(self) -> None:
        log("ТЕСТ: запись 4 сек…")
        self._set_icon(ICON_REC)
        try:
            self.rec.start()
        except Exception as exc:  # noqa: BLE001
            log(f"ТЕСТ запись ошибка: {exc}")
            self._set_icon(ICON_ERR, reset=True)
            return
        time.sleep(4)
        path = self.rec.stop()
        self._set_icon(ICON_WORK)
        self._process(path)

    # --- отправка и вставка ---
    def _process(self, path: str) -> None:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        log(f"отправка: файл {size} байт")
        if size < 1000:
            log("файл пустой/крошечный — микрофон молчит или нет доступа")
            self._notify("Пусто", "Микрофон не записал звук (доступ?)")
            self._set_icon(ICON_IDLE)
            return
        try:
            with open(path, "rb") as f:
                resp = httpx.post(
                    f"{SERVER_URL}/transcribe",
                    files={"file": ("dictation.wav", f, "audio/wav")},
                    timeout=60.0)
            resp.raise_for_status()
            text = (resp.json().get("text") or "").strip()
        except Exception as exc:  # noqa: BLE001
            log(f"сервер ошибка: {exc}")
            self._notify("Сервер недоступен", str(exc)[:120])
            self._set_icon(ICON_ERR, reset=True)
            return
        log(f"распознано: {text!r}")
        if text:
            try:
                _paste(text)
                log("вставка: Cmd+V отправлен")
            except Exception as exc:  # noqa: BLE001
                log(f"вставка ошибка: {exc}")
                self._notify("Не удалось вставить", str(exc)[:120])
        else:
            log("пусто — нечего вставлять")
        self._set_icon(ICON_IDLE)

    # --- UI ---
    def _set_icon(self, icon: str, reset: bool = False) -> None:
        self.title = icon
        if reset:
            threading.Timer(2.0, lambda: setattr(self, "title", ICON_IDLE)).start()

    def _notify(self, title: str, msg: str) -> None:
        try:
            rumps.notification("Диктовка", title, msg)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    Dictation().run()
