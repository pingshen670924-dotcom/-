param(
    [string]$RepoName = "香港六合彩預測系統",
    [switch]$Private
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$RepoPath = (Resolve-Path -LiteralPath $PSScriptRoot).Path.Replace("\", "/")
$env:GIT_CONFIG_GLOBAL = Join-Path $PSScriptRoot ".gitconfig_publish"
@(
    "[safe]",
    "	directory = $RepoPath"
) | Set-Content -LiteralPath $env:GIT_CONFIG_GLOBAL -Encoding UTF8

gh auth status | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "GitHub 尚未登入或 token 已失效，請先執行 gh auth login -h github.com"
}

$Owner = gh api user --jq ".login"
if (-not $Owner) {
    throw "無法取得 GitHub 使用者。"
}

if (-not (Test-Path -LiteralPath ".git")) {
    git init
    git branch -M main
}

git add .
git commit -m "香港六合彩預測系統手機雲端更新" 2>$null
if ($LASTEXITCODE -ne 0) {
    git status --short | Out-Host
}

$Visibility = if ($Private) { "--private" } else { "--public" }
$RepoExists = $false
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    gh repo view "$Owner/$RepoName" *> $null
    if ($LASTEXITCODE -eq 0) {
        $RepoExists = $true
    }
} finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}

if (-not $RepoExists) {
    gh repo create "$Owner/$RepoName" $Visibility --source . --remote origin --push
} else {
    git remote remove origin 2>$null
    git remote add origin "https://github.com/$Owner/$RepoName.git"
    git push -u origin main
}

$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    gh api --method POST "repos/$Owner/$RepoName/pages" -f build_type=workflow *> $null
    if ($LASTEXITCODE -ne 0) {
        gh api --method PATCH "repos/$Owner/$RepoName/pages" -f build_type=workflow *> $null
    }
} finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}

gh workflow run "香港六合彩預測系統手機雲端" --repo "$Owner/$RepoName"

$Url = "https://$Owner.github.io/$RepoName/"
Write-Host ""
Write-Host "香港六合彩預測系統手機獨立雲端網址:"
Write-Host $Url
Write-Host ""
Write-Host "第一次部署通常需要 1-3 分鐘。GitHub Actions 完成後手機即可直接開，電腦關掉也能使用。"



