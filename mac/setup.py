"""py2app-сборка приложения «Диктовка».

Alias-режим (для личного использования):
    python setup.py py2app -A
Бандл получит собственную идентичность com.leshwas.dictation — тогда macOS
корректно выдаёт доступ к микрофону / мониторингу ввода / Accessibility.
"""
from setuptools import setup

APP = ["client.py"]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "Диктовка",
        "CFBundleDisplayName": "Диктовка",
        "CFBundleIdentifier": "com.leshwas.dictation",
        "CFBundleVersion": "1.0",
        "CFBundleShortVersionString": "1.0",
        "LSUIElement": True,
        "NSMicrophoneUsageDescription":
            "Диктовка записывает речь, чтобы перевести её в текст.",
        # без UTF-8 бандл стартует в ASCII-локали и падает на кириллице
        "LSEnvironment": {"PYTHONUTF8": "1", "LANG": "en_US.UTF-8",
                          "LC_ALL": "en_US.UTF-8"},
    },
    "packages": ["rumps", "pynput", "httpx", "AVFoundation", "Foundation", "objc"],
}

setup(
    name="Диктовка",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
