# Pocket Camera PTZ LAN Controller

在 Windows 區域網路中集中控制 DJI Osmo Pocket 3／相容 UVC PTZ 裝置，並以實驗性 Wi-Fi 後端支援 Feiyu Pocket 3。兩種相機共用同一套導播控制台與 HTTP API。

> 非 DJI、Feiyu、NDI 或 OBS 官方專案。所有名稱及商標均屬其各自權利人。

## 功能

- 自動偵測最多五台 DJI／Osmo／Pocket UVC 裝置。
- 每台相機使用獨立節點與連接埠 `8723–8727`。
- Blackmagic 風格三機導播介面。
- 搖桿、鍵盤、變焦、Home、速度控制。
- 每台最多 20 組可命名 Preset，支援完成秒數與動態曲線。
- 三機場景巨集。
- Direct UVC LAN、NDI 預覽與軟體追蹤。
- OBS 共用 MJPEG 輸出：相機只開啟一次 UVC。
- 黑畫面偵測與 UVC 格式自動降級。
- 單一 Master Console 管理最多五個隱藏節點。
- PyInstaller 一鍵建立 Windows EXE。
- Feiyu Pocket 3 實驗性 PTP/IP＋UDP 搖桿控制後端。

## 系統需求

- Windows 10／11 64-bit
- Python 3.12（從原始碼執行或建置 EXE 時需要）
- DJI Osmo Pocket 3 或具 DirectShow PTZ 控制介面的相容裝置
- Feiyu 模式另需電腦連上相機 Wi-Fi；畫面使用外部 UVC／NDI 來源
- 有線 Gigabit LAN 建議用於多機 720p 串流
- NDI 功能需要相容的 NDI Runtime／Tools；Direct UVC LAN 不需要 NDI

## 快速開始

### 建立 EXE

1. 下載或 clone 整個倉庫。
2. 雙擊 `build-windows-exe.bat`。
3. 完成後執行 `manage-firewall.bat`，選擇開放LAN連線。
4. 相機端執行 `start-cameras.bat`，選擇 DJI 一般／NDI-only，或 Feiyu 實驗模式。
5. 導播端執行 `EXE/Pocket3-Director.exe`。

### 從原始碼執行

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python multi-ptz-launcher.py --max-cameras 5 --start-port 8723
```

另一台導播電腦執行：

```powershell
python director-console.py
```

## 連接埠

| 相機 | 控制與預覽 |
|---|---:|
| Camera 1 | 8723 |
| Camera 2 | 8724 |
| Camera 3 | 8725 |
| Camera 4 | 8726 |
| Camera 5 | 8727 |

導播台輸入節點網址，例如 `http://192.168.1.100:8723`。

## OBS輸出

OBS 建議使用「媒體來源」，取消勾選本機檔案，輸入：

```text
http://CAMERA_PC_IP:8723/obs.mjpg
```

Camera 2～5 將連接埠依序改成 `8724–8727`。若使用瀏覽器來源，也可以輸入 `/obs`，但 MJPEG 媒體來源通常較順。

不要讓 OBS 的「視訊擷取裝置」與本程式同時直接開啟同一台 Pocket 3，否則 Windows UVC 驅動可能發生黑畫面或離線。

## API摘要

| 路徑 | 說明 |
|---|---|
| `/status` | 節點、相機、FPS與預覽狀態 JSON |
| `/preview.jpg` | 控制台低負載 JPEG |
| `/preview.mjpg` | 控制台 MJPEG |
| `/obs.mjpg` | OBS 720p MJPEG |
| `/obs` | OBS瀏覽器包裝頁 |
| `/vel?pan=&tilt=&zoom=` | PTZ速度控制 |
| `/stop` | 停止運鏡 |
| `/preset/*` | 儲存、叫出、命名與刪除 Preset |

完整設定請參閱 [Feiyu設定](docs/FEIYU_SETUP.md)、[OBS設定](docs/OBS_SETUP.md)、[架構](ARCHITECTURE.md)與[故障排除](docs/TROUBLESHOOTING.md)。

## Windows管理工具

| 檔案 | 用途 |
|---|---|
| `start-cameras.bat` | 啟動 DJI 多機節點或 Feiyu 實驗節點 |
| `manage-firewall.bat` | 新增或移除LAN防火牆規則 |
| `manage-autostart.bat` | 管理Node與Director登入自動啟動 |
| `build-windows-exe.bat` | 建立 DJI、Feiyu 與 Director Windows EXE |

## 已知限制

- UVC格式支援取決於相機韌體、USB控制器與DirectShow驅動。
- MJPEG適合校內區網與低複雜度部署；對極低延遲正式製播，NDI或硬體影像介面仍較理想。
- DJI內部ActiveTrack演算法未提供公開UVC控制介面；目前自動追蹤為本專案的軟體追蹤。
- 導播介面目前顯示三個控制盤面，節點與OBS輸出最多五台。
- Feiyu 後端目前沒有絕對位置、變焦、Home、Preset 與節點內建預覽，且尚待目標韌體實機驗證。

## 貢獻與安全

提交程式前請閱讀 [CONTRIBUTING.md](CONTRIBUTING.md)。安全漏洞請依 [SECURITY.md](SECURITY.md) 私下回報，不要建立公開Issue。

## 授權

本專案使用 [MIT License](LICENSE)。

---

English: A Windows LAN controller with a shared Director/API for DJI-compatible UVC/PTZ cameras and an experimental Feiyu Pocket 3 Wi-Fi control backend.
