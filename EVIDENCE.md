# How VMS Pro downloads recordings: evidence

Target: `General_Beta_VMS Pro_Win64` (VMS Pro V3.1.5.4, build 20260920, from `version.txt`).
Analysed on 2026-10-14. Sources:

- **Runtime log** `Log/Client_20261014.log`. VMS Pro wrote it during your session on
  2026-10-14 between 18:20 and 18:35, which included a playback search and three download attempts.
- **Static strings and exports** from `XNetSDK.dll`, the vendor network SDK that does the device I/O.
- **Local state**: `config.ini`, `data/cloudusers/<account>/devices.db`, `data/.../downloads/`.

Notation: **[O]** observed directly · **[I]** inferred · **[?]** unknown.
Line numbers refer to `Client_20261014.log`. Secrets that appear in the log (cloud tokens, device
login tokens) are left out here on purpose.

## 1. Login: two layers

| Step | Evidence |
|---|---|
| Cloud account login by QR code against `api-*.jftechws.com/ams/qr/...` | [O] lines 54–246 (`CCloudLoginDlg::codeLogin`) |
| SDK init with app credentials (`H264_DVR_InitAppInfo ret=1`) | [O] line 248 |
| Device list loaded from `devices.db`: 4 cameras, **Address empty, LoginType 2, CloudID = serial** | [O] SQLite read (password and token columns excluded) |
| P2P state query by serial, then `ConnectDevice` per camera: `LoginSuccess IP <empty> Port 34567 Cloud <serial> link mode 2` | [O] lines 351–566 |
| Device login uses a per-device token (`Login Token ...`), not the typed password | [O] lines 405, 471, 517, 564 |
| The SDK's login packet is built in `CProtocolNetIP::NewLoginPTL` with `EncryptType` `MD5` or `TOKEN`, `LoginType` **`DVRIP-Mobile`**, and `UserName`/`PassWord` | [O] strings in `XNetSDK.dll` near offset 0x15016e8 |

So in your setup VMS Pro never connects to a camera by IP. It reaches each camera through
Xiongmai's cloud P2P relay by serial number, and then speaks the normal device protocol inside that tunnel. [I]

## 2. Search: `OPFileQuery` (message 1440)

[O] lines 5047–5064. The exact request and reply were logged:

```json
In:  {"BeginTime":"2026-10-14 13:30:00","Channel":0,"DriverTypeMask":"0x0000FFFF",
      "EndTime":"2026-10-14 13:59:59","Event":"*","StreamType":"0x0","Type":"h264"}
Out: {"Name":"OPFileQuery","OPFileQuery":[{"BeginTime":"2026-10-14 13:30:00","DiskNo":0,
      "EndTime":"2026-10-14 13:35:00","FileLength":"0x0000ea28",
      "FileName":"/idea0/2026-10-14/001/13.30.00-13.35.00[R][@a36f][0].h264","SerialNo":0}, ...]}
```

- [O] The camera stores 5-minute segments on its SD card (`/idea0/<date>/001/HH.MM.SS-HH.MM.SS[R][@id][0].h264`).
- [I] `FileLength` is in KiB: 0xea28 = 59,944 KiB, about 58.5 MiB per 5 minutes, which fits an 8 MP camera (`XM232_X6S-WEQ-H_8M`, line 5340).
- [O] `CPlaybackSearch::onRecordSearchBasic` runs this per day or hour window (lines 2978, 3651, 5041), and `CPlaybackDisplayWidget::onRecordSearched` draws the results on the timeline.

## 3. Download: `DownLoadByRecordFile` → `OPPlayBack` by name

Call chain recorded at runtime [O], lines 5116–5122:

```
CPlaybackManage::startDownloadRecordFile(ch 0, "/idea0/2026-10-14/001/13.30.00-13.35.00[R][@a36f][0].h264")
 └ CPlaybackSession::StartPlaybackByFile → pInterFace->DownLoadByRecordFile(loginID)
    └ CXNetSdkManager::downloadByFile   File=<name> Start "20261014 133000" End "20261014 133500"
       └ RecordDownloadMessageCallBack  "Start Download Handle ..."
```

- [O] Progress arrives through `CPlaybackSession::DealWithDownPosCallbackV2` (`pos = 1000 Progress 100`, line 5260).
- [O] Each job ends in `CPlaybackDownloadFile::onUpdateDownloadFileState` and `stopMediaPlay(handle)`. Files download **one at a time**: the second started only after the first was stopped (lines 5213–5221).
- [O] The wire message is `OPPlayBack`. `XNetSDK.dll` embeds the template
  `{"Name":"OPPlayBack","OPPlayBack":{"Action":...,"Parameter":{"Channel","FileName","FileOffset","OffWithHead","PlayMode","StreamType","Value","TransMode":"TCP",...},"StartTime","EndTime"}}`
  together with the actions `Claim`, `DownloadStart`, `DownloadStop`, `DownloadPause` and `DownloadContinue` (`CChnRecordStream::ToSendMediaControl`).
- [O] The SDK exports `XSDK_MediaRecordDownloadByName`, `XSDK_MediaRecordDownloadByTime`, `XSDK_DevFindFile` and `XSDK_DevLogin`.
- [O] Output location: `data/cloudusers/<account>/downloads/<serial>_<channel>_<YYYYMMDDhhmmss>.h264`. `config.ini` `[Path] DownloadPath=` is empty, so the default is used. `PlaybackDownFormat=0` means it keeps the native `.h264` and doesn't convert.

### Your download attempts did not succeed

- [O] Attempt 1 (18:32:04) was stopped after 90 s, ending in state `-2` (line 5215).
- [O] Attempt 2 (18:33:34) ended when **the camera dropped the connection**: `DealWithDevDisConnect`, `ESXSDK_MEDIA_ON_PLAY_STATE nParam2 -1007`, state `-2` (lines 5256–5270).
- [O] Attempt 3 (18:35:06) was rejected immediately with state `-4` (line 5328).
- [O] The only file on disk, `5e85…_1_20261014133000.h264`, is **0 bytes**.
- [O] During these attempts the client polled `caps-sa.jftechws.com/api/reqCaps/v2` for `net.dat` (`"net.dat.enable":false`, `"freetime":600`) and `statics.jftechws.com/vms/boid-free-play-duration.json` (`freePlayMinutes`: 10 by default, 3 for some OEM ids).
- [I] Cloud P2P/relay playback appears to be metered: free minutes per session or account, with the paid "net.dat" traffic plan disabled. That would explain transfers that stall or stop. The client code that applies this limit was not traced. [?]

### Later attempts (18:36–18:46), from `Log/log/netsdk.log`

- [O] The download path is cloud relay: `RPSClient` gets an agent server `23.251.39.39:6611`, the handshake reports
  the camera at `181.24.30.173`, and a P2P hole punch is tried (`ClientIO IoRecvData Error -1`).
  The camera's LAN address is `192.168.1.33`; this PC is on `192.168.100.13`, a different site.
- [O] During the transfer the SDK logs repeated `Stream Data Loss` / `CChnRecordStream::OnDPError error: -3`
  (around 18:37:45–18:37:49), then `DealWithDevDisConnect` and `-1007`.
- [O] VMS Pro then logs `Progress 100` and `DownloadByFile_State_Finished` even though the transfer was cut off.
  The UI shows "Descarga completada", which is a **false success**. The file was 0 bytes, and the folder was
  empty after the retry failed at 18:45:55.
- [O] Every camera has `General.General.OverWrite = "OverWrite"`: the SD card loops and deletes the oldest recordings.

## 4. File format

- [I] The `.h264` files are Xiongmai's private container: each frame starts with `00 00 01 FC/FE` (I-frame, 16-byte header holding fps and size), `FD` (P-frame, 8 bytes), `FA` (audio) or `F9` (info). VMS Pro ships `ffmpeg.exe` and FFmpeg DLLs to decode them.
  This layout comes from public documentation of the protocol, not from a sample: no non-empty recording was available to inspect. `videowatcher/xmdemux.py` resyncs on unknown blocks to stay robust.

## 5. What videoWatcher reproduces

| VMS Pro | videoWatcher |
|---|---|
| Cloud login, then P2P device login by serial | **LAN**: direct DVRIP login to `IP:34567` (`EncryptType MD5`, `LoginType DVRIP-Mobile`). P2P backend reserved |
| `OPFileQuery` (1440) | Same message and body, paged by 64 |
| `DownLoadByRecordFile` → `OPPlayBack` `Claim` + `DownloadStart` `ByName` | Same messages over the login socket, data in 1426 packets |
| One download at a time | One at a time per camera |
| Saves native `.h264` | Saves native `.h264`, plus optional MP4 remux with no re-encode |

### Not established by this evidence

- [?] The byte-level P2P tunnel and token login (`EncryptType TOKEN`). That's why P2P isn't implemented.
- [?] The MD5 password digest (`sofia_hash`) comes from public protocol documentation. It is checked against the published empty-password value `tlJwpbo6`, but not yet against your cameras.
- [?] Whether your cameras answer on the LAN with the per-device usernames stored in `devices.db` (`fxcu`, `dmth`, ...), or still accept `admin`.
