# 香港六合彩預測系統

這個資料夾就是手機獨立雲端版。發布後手機直接開雲端網址使用，不需要同 Wi-Fi，不需要連回本機電腦，電腦關閉也能看。

## 手機獨立運作

- 雲端網址由 GitHub Pages 提供。
- 雲端排程由 GitHub Actions 執行。
- 開獎日雲端會自動抓官方最新開獎。
- 抓到新開獎後自動重新運算最新預測。
- 自動更新手機首頁、完整戰報、最新預測、歷史資料。

## 發布

先登入 GitHub：

```powershell
gh auth login -h github.com
```

再執行：

```powershell
.\publish_to_github.ps1
```

完成後會輸出手機獨立雲端網址：

```text
https://你的帳號.github.io/香港六合彩預測系統/
```

手機打開這個網址即可獨立使用。

## 雲端排程

開獎日自動檢查：

```text
每週二、四、六 22:45 / 23:45 / 00:45 香港時間附近多次檢查
```

也可以在 GitHub Actions 手動執行 `香港六合彩預測系統手機雲端`。

## 主要入口

- `site/index.html`：手機雲端首頁
- `site/mobile.html`：手機首頁原檔
- `site/latest_battle_report.html`：完整戰報
- `site/latest_prediction.html`：最新預測
- `site/mobile_status.json`：手機狀態資料
- `.github/workflows/mobile-cloud.yml`：雲端自動更新與部署
