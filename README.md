# 香港六合彩手機獨立雲端系統

這個資料夾是可發布到 GitHub Pages 的手機雲端版。

手機使用後不需要連回本機電腦：

- GitHub Pages 提供公開手機網址。
- GitHub Actions 在開獎日自動抓 HKJC 最新資料。
- 雲端重新產生預測、戰報、手機首頁。
- `site/index.html` 是手機版首頁。

## 發布

先登入 GitHub：

```powershell
gh auth login -h github.com
```

再執行：

```powershell
.\publish_to_github.ps1
```

完成後手機開啟輸出的 `https://使用者.github.io/hk-marksix-mobile-cloud/`。

## 雲端排程

預設排程：

```text
每週二、四、六 22:30 香港/台灣時間
```

也可以在 GitHub Actions 手動執行 `Mark Six Mobile Cloud`。

## 檔案

- `site/index.html`：手機首頁
- `site/mobile.html`：手機首頁原檔
- `site/mobile_status.json`：手機狀態資料
- `site/latest_battle_report.html`：完整戰報
- `site/latest_prediction.html`：最新預測
- `.github/workflows/mobile-cloud.yml`：雲端自動更新與部署
