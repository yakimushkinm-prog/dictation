# Dictation

Push a hotkey on your Mac, speak, push it again — the text appears at the cursor in whatever
app is in front. Recognition runs on your own GPU machine, not in anyone's cloud.

Round trip is **0.2–0.5 s** for a normal sentence on an RTX 5070. Audio never leaves your
network and is never stored.

[Русская версия](README.ru.md)

```
[Mac] hotkey → record → SSH tunnel :9876 → [GPU box] FastAPI → faster-whisper → text → paste at cursor
```

## Why it exists

Dictation built into the OS is either cloud-based or mediocre, and the good cloud services want
your voice on their servers. This puts a real Whisper model on hardware you own, reachable only
over a private tunnel, and makes it feel like a native OS feature: one key, no window, no
copy-paste dance.

The design constraint that shaped everything: **it has to be fast enough to use mid-sentence.**
Anything above roughly a second and you stop reaching for it.

## Parts

| | |
|---|---|
| `mac/client.py` | Menu-bar app: global hotkey, microphone capture, upload, paste |
| `server/server.py` | FastAPI service with `/health` and `/transcribe` |
| `server/transcriber.py` | faster-whisper wrapper — lazy load, GPU with CPU fallback |
| `mac/launchd/` | launchd agents: keep the client and the SSH tunnel alive |
| `server/dictation.service` | systemd user unit for the server |

## Setup

### 1. Server (the GPU machine)

Linux or WSL2 with an NVIDIA card. Copy `server/` to `~/dictation/server`:

```bash
cd ~/dictation/server
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

Install as a user service so it survives logout:

```bash
sudo loginctl enable-linger "$USER"
mkdir -p ~/.config/systemd/user
cp dictation.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now dictation
curl -s localhost:9876/health     # {"status":"ok","device":"cuda"}
```

The service binds `127.0.0.1` only. Nothing is exposed to the network — reaching it is the
tunnel's job.

Environment overrides: `DICT_MODEL` (default `medium`), `DICT_DEVICE` (`auto`), `DICT_LANG`
(default `ru`; set empty for auto-detect).

### 2. Tunnel (on the Mac)

```bash
cp mac/launchd/com.leshwas.dictation-tunnel.plist.example \
   ~/Library/LaunchAgents/com.leshwas.dictation-tunnel.plist
```

Edit the copy and replace `__USER__`, `__HOST__`, `__PORT__` and `__SSH_KEY__`, then:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.leshwas.dictation-tunnel.plist
curl -s localhost:9876/health     # same answer as on the server — tunnel is up
```

### 3. Client (on the Mac)

```bash
cd mac
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
python client.py
```

A 🎙 icon appears in the menu bar. Press the hotkey (the backtick key `` ` `` by default), speak, press again.
Use **Test: record 4 s** from the menu to check the microphone without a hotkey.

macOS will ask for **Microphone**, **Accessibility** and **Input Monitoring** permission.
All three are required: the first to record, the other two to send the paste keystroke.

To run it at login, use `com.leshwas.dictation.plist.example` the same way as the tunnel agent.
To get a proper app bundle with its own identity (which makes the macOS permission prompts
behave), build with py2app:

```bash
python setup.py py2app -A
```

## Configuration

`mac/config.toml`:

| Key | Default | |
|---|---|---|
| `server_url` | `http://127.0.0.1:9876` | local end of the tunnel |
| `hotkey` | `` ` `` | pynput syntax; a plain key like `` ` `` still types its character, so use a combination such as `<ctrl>+<alt>+d` if that gets in the way |
| `sound` | `true` | click on start and stop |
| `restore_clipboard` | `false` | put the previous clipboard back after pasting |

## Design notes

The parts that took more than one attempt, kept here because they are the whole difficulty:

**Recording goes through AVFoundation, not PortAudio.** The obvious choice — `sounddevice` —
records silence on modern macOS unless the process has properly requested microphone access, and
it gives you no way to trigger that prompt. `AVAudioRecorder` asks the system the way the system
expects, so the user sees the permission dialog and the file actually contains audio. The client
checks the recorded file size and says "microphone recorded nothing (permission?)" rather than
sending an empty file and reporting a mysterious empty result.

**Pasting uses a Quartz `CGEvent`, not synthetic pynput keystrokes.** Emulated Cmd+V is dropped
by a good number of apps. Posting a real keyboard event to the HID event tap works everywhere,
including Electron apps and terminals.

**The model is loaded during startup, not on first request.** Without a warm-up the first
dictation of the day takes several seconds while weights move onto the card — exactly the moment
a new user decides the tool is slow. FastAPI's `lifespan` hook loads it before the service reports
ready.

**GPU failure degrades instead of breaking.** The transcriber tries CUDA, falls back to CPU when
the card is unavailable, and — because ctranslate2 can construct a CUDA model successfully and
only then fail at inference — retries once on CPU if inference throws. Any recognition failure
returns an empty string, so the client shows "nothing recognized" rather than an error.

**cuBLAS and cuDNN are preloaded by hand on WSL.** ctranslate2 finds them via `dlopen`, and under
WSL they are not on the system search path — but they are inside the `nvidia-*` pip packages. The
server loads those `.so` files with `RTLD_GLOBAL` at import time, which avoids editing
`LD_LIBRARY_PATH` in the unit file. Best-effort: if the packages are absent it stays quiet and
runs on CPU.

**`compute_type` is `int8_float16`.** About 1.5 GB of VRAM, accuracy close to `float16` — chosen
so the model fits alongside a language model on the same 12 GB card.

## Security

Audio goes over an SSH tunnel on a private network. The server listens on loopback only, the
temporary WAV is deleted after transcription, and nothing is written to disk on either side
beyond that. There is no authentication in the service itself — reaching the port at all requires
your SSH key, which is the intended threat model. Do not bind it to `0.0.0.0`.

## Notes

Source comments are in Russian; the default recognition language is Russian and configurable via
`DICT_LANG`. Built and used daily on macOS 26 with a Windows/WSL2 GPU host.

## License

MIT — see [LICENSE](LICENSE).
