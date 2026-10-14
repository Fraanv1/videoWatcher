# videoWatcher

> **Español:** ver [LEEME.md](LEEME.md), que tiene los pasos urgentes para conservar los videos.

Search and download SD-card recordings from iCSee / XMEye (Xiongmai) cameras, with a login-protected
web UI. It is modelled on the download feature of VMS Pro; see [EVIDENCE.md](EVIDENCE.md) for how that works.

## Requirements

- Python 3.11+ with `pip install -r requirements.txt`
- Network access from this PC to each camera's IP on TCP **34567** (same LAN, VPN, or port forward)
- Optional: ffmpeg for MP4 output. Either put it on PATH or set
  `VIDEOWATCHER_FFMPEG`, for example to the one bundled with VMS Pro:
  `$env:VIDEOWATCHER_FFMPEG = "C:\...\General_Beta_VMS Pro_Win64\ffmpeg.exe"`

## Run

```powershell
cd C:\Users\Fraan\Downloads\Icsee\videoWatcher
pip install -r requirements.txt
python -m videowatcher adduser fran        # app login, min 8 characters
python -m videowatcher serve               # http://127.0.0.1:8765
```

Then sign in, choose **+ Add** and enter the camera's IP, user and password. Click **Test connection**,
pick a date and **Search**, tick recordings, then **Download selected**.

Files go to `downloads/<camera>/<date>/`. The original `.h264` is always kept. The `.mp4` is a
remux of the same video with no re-encoding, so it is fast and lossless. Audio is not included in the MP4.

## Converting files from the camera's microSD card

```powershell
python -m videowatcher convert E:\ C:\Evidence\cam1_mp4
```

This converts every `.h264` under the folder to MP4 and writes `hashes_sha256.csv` with the
SHA-256 of each original. Originals are only read, never changed.

## Finding the camera IP and credentials

Your cameras are currently added in VMS Pro / iCSee by **cloud serial number only**, so VMS Pro has no
IP for them. To use them here:

1. Find each camera's LAN IP in your router's DHCP client list. In iCSee, *Device settings → About* may also show it.
2. Credentials: iCSee may have replaced `admin` with a random per-device user. VMS Pro's database lists
   users such as `fxcu` and `dmth`. Try `admin` with the password you set in iCSee first. If you can't
   get in, reset the camera's device password from the iCSee app.

## Security notes

- The UI listens on `127.0.0.1` only by default. If you expose it with `--host 0.0.0.0`, put it
  behind HTTPS and pass `--secure-cookie`.
- App passwords are stored as scrypt hashes in `data/users.json`. Sessions are HMAC-signed cookies
  (HttpOnly, SameSite=Strict, 12 h), and an IP is locked out for 5 minutes after 5 failed logins.
- Camera passwords are stored **in plain text** in `data/cameras.json`, because the camera protocol
  needs them. The API never returns them. Keep `data/` private.
- DVRIP itself is unencrypted. Use it only on a network you trust, or over a VPN.

## Layout

```
videowatcher/
  dvrip.py      DVRIP client: login, OPFileQuery search, OPPlayBack download
  backends.py   transport selection: "lan" (implemented), "p2p" (placeholder)
  xmdemux.py    Xiongmai .h264 container to elementary stream, then MP4 via ffmpeg
  jobs.py       background download queue (one at a time per camera)
  auth.py       app users, sessions, login throttle
  store.py      JSON persistence (data/)
  app.py        FastAPI routes + static UI
tests/          unit tests + fake DVRIP camera; run: python -m unittest -v
```

## Adding cloud (P2P) access later

Cameras with `transport: "p2p"` route to `P2PBackend` in `backends.py`. Implementing it means either
wrapping VMS Pro's `XNetSDK.dll` (`XSDK_DevLogin`, `XSDK_DevFindFile`, `XSDK_MediaRecordDownloadByName`),
whose signatures are undocumented and would need reverse-engineering, or reimplementing the Xiongmai
P2P layer. Note that VMS Pro's own cloud downloads in this session were cut off; see EVIDENCE.md §3.
