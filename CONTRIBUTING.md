# Contributing

感謝你願意改善這個專案。

## 開發流程

1. 先搜尋既有Issue，避免重複。
2. Fork倉庫並由 `main` 建立功能分支。
3. 使用 Python 3.12 建立虛擬環境並安裝 `requirements-dev.txt`。
4. 讓修改保持單一目的，不要混入無關格式化。
5. 執行基本檢查：

```powershell
python -m py_compile ptz-server.py multi-ptz-launcher.py director-console.py ndi_preview.py
ruff check --select E9,F63,F7,F82 *.py
```

6. 若修改相機、UVC、NDI或OBS功能，請在PR中列出：Windows版本、相機型號與數量、USB拓撲、擷取模式、OBS版本、重現步驟與日誌。

## Pull Request要求

- 說明問題、解法與風險。
- 不提交EXE、ZIP、錄影、設定檔、IP位址、個人路徑或金鑰。
- UI變更請附截圖。
- 新功能應更新README／使用說明與CHANGELOG。
- 保持對不安裝NDI的Direct UVC模式相容。

## Commit訊息

建議使用清楚的動詞開頭，例如：

- `fix: release UVC when OBS mode is enabled`
- `feat: add per-camera capture fallback`
- `docs: document multi-node OBS URLs`
