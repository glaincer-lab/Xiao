# 小二 · 打包前置脚本：组装「内置 Python 运行时」（desktop/runtime/python）
#
# 给谁用：发布者/打包机（使用者不需要跑这个，安装包里已经带好了）。
# 做什么：
#   1. 下载 Python 官方 embeddable 包（免安装、可随安装包分发）到 desktop/runtime/python
#   2. 打开 site 支持 + 装入 pip
#   3. 按仓库根的 requirements.txt 装好后端依赖（纯云链路，不含本地 ASR/TTS 大件）
#   4. （可选，默认执行）补齐 models/ 运行资源：Silero VAD、sherpa 唤醒模型、Piper 中文声库
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\prepare-runtime.ps1
#   可选参数：-PythonVersion 3.12.10   -Force（强制重建）   -SkipModels（跳过模型下载）
param(
  [string]$PythonVersion = "3.12.10",
  [switch]$Force,
  [switch]$SkipModels
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$desktop = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $desktop
$pyDir = Join-Path $desktop "runtime\python"
$cacheDir = Join-Path $desktop "runtime\_cache"
$pyExe = Join-Path $pyDir "python.exe"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Download-File($url, $dest) {
  Write-Host "    下载 $url"
  Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

Write-Host "小二 · 内置 Python 运行时组装（Python $PythonVersion）"
Write-Host "仓库根：$repoRoot"

# ---- 1. Python embeddable ----
if (Test-Path $pyExe) {
  if (-not $Force) {
    Write-Step "运行时已存在（$pyExe），跳过下载/装依赖（-Force 可重建）"
  } else {
    Remove-Item -Recurse -Force (Join-Path $desktop "runtime")
    New-Item -ItemType Directory -Force -Path $pyDir, $cacheDir | Out-Null
  }
}
if (-not (Test-Path $pyExe)) {
  New-Item -ItemType Directory -Force -Path $pyDir, $cacheDir | Out-Null
  $baseVer = ($PythonVersion -replace '\.\d+$', '')   # 3.12.10 -> 3.12
  $zipName = "python-$PythonVersion-embed-amd64.zip"
  $zipPath = Join-Path $cacheDir $zipName
  Write-Step "下载 Python embeddable $PythonVersion"
  if (-not (Test-Path $zipPath)) {
    Download-File "https://www.python.org/ftp/python/$PythonVersion/$zipName" $zipPath
  }
  Write-Step "解压到 runtime\python"
  Expand-Archive -Path $zipPath -DestinationPath $pyDir -Force

  Write-Step "启用 site-packages（修补 _pth）"
  $pth = Get-ChildItem $pyDir -Filter "python*._pth" | Select-Object -First 1
  if (-not $pth) { throw "未找到 ._pth 文件，embeddable 包不完整" }
  $lines = Get-Content $pth.FullName
  $lines = $lines -replace '^#import site$', 'import site'
  if (-not ($lines -contains 'Lib\site-packages')) { $lines += 'Lib\site-packages' }
  Set-Content -Path $pth.FullName -Value $lines -Encoding ASCII

  Write-Step "安装 pip"
  $getPip = Join-Path $cacheDir "get-pip.py"
  if (-not (Test-Path $getPip)) {
    Download-File "https://bootstrap.pypa.io/get-pip.py" $getPip
  }
  & $pyExe $getPip --no-warn-script-location
  if ($LASTEXITCODE -ne 0) { throw "pip 安装失败（退出码 $LASTEXITCODE）" }

  Write-Step "安装后端依赖（requirements.txt，纯云链路）"
  & $pyExe -m pip install --no-warn-script-location -r (Join-Path $repoRoot "requirements.txt")
  if ($LASTEXITCODE -ne 0) { throw "依赖安装失败（退出码 $LASTEXITCODE）" }
}

# ---- 2. models/ 运行资源（仓库不入库，打包机需要齐）----
if (-not $SkipModels) {
  $models = Join-Path $repoRoot "models"
  New-Item -ItemType Directory -Force -Path $models | Out-Null

  # Silero VAD（语音断句必需）
  $vad = Join-Path $models "silero_vad.onnx"
  if (-not (Test-Path $vad)) {
    Write-Step "下载 Silero VAD 模型"
    Download-File "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx" $vad
  }

  # sherpa-onnx 唤醒词模型（「小二」）
  $kwsDirName = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
  $kwsDir = Join-Path $models $kwsDirName
  if (-not (Test-Path (Join-Path $kwsDir "tokens.txt"))) {
    Write-Step "下载 sherpa 唤醒词模型（约几 MB）"
    $kwsArchive = Join-Path $cacheDir "$kwsDirName.tar.bz2"
    if (-not (Test-Path $kwsArchive)) {
      Download-File "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$kwsDirName.tar.bz2" $kwsArchive
    }
    tar -xjf $kwsArchive -C $models
    if ($LASTEXITCODE -ne 0) { throw "唤醒模型解压失败；可手动下载 $kwsDirName 放到 models\" }
  }

  # Piper 中文声库（离线保底播报）
  $piperOnnx = Join-Path $models "zh_CN-huayan-medium.onnx"
  if (-not (Test-Path $piperOnnx)) {
    Write-Step "下载 Piper 中文声库 huayan-medium（约 60MB）"
    $voiceBase = "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium"
    Download-File "$voiceBase/zh_CN-huayan-medium.onnx" $piperOnnx
    Download-File "$voiceBase/zh_CN-huayan-medium.onnx.json" "$piperOnnx.json"
  }

  # bge 语义消歧模型（出网网关合规底线，随包）
  # tokenizer 配置可从 HF 自动下载；ONNX 官方仓库默认不含（需导出/预置），见 scripts/install_gateway_model.py
  $bgeDir = Join-Path $models "gateway-semantic\bge-small-zh-v1.5"
  New-Item -ItemType Directory -Force -Path $bgeDir | Out-Null
  foreach ($f in @("config.json", "tokenizer.json", "tokenizer_config.json")) {
    $bgeDst = Join-Path $bgeDir $f
    if (-not (Test-Path $bgeDst)) {
      Write-Step "下载 bge 配置文件 $f"
      Download-File "https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/main/$f" $bgeDst
    }
  }
  if (-not (Test-Path (Join-Path $bgeDir "model_quantized.onnx")) -and -not (Test-Path (Join-Path $bgeDir "model.onnx"))) {
    Write-Host "     [提示] bge 语义模型 ONNX（model_quantized.onnx / model.onnx）官方仓库不含，需手动预置到 $bgeDir（或用 optimum 导出）" -ForegroundColor Yellow
  }
}

# ---- 3. 自检：关键依赖可导入 ----
Write-Step "自检：内置解释器导入核心依赖"
& $pyExe -c "import fastapi, uvicorn, yaml, dotenv, numpy, sounddevice, sherpa_onnx, onnxruntime, openai, dashscope, edge_tts, pygame, requests, bs4, websockets; print('runtime OK')"
if ($LASTEXITCODE -ne 0) { throw "内置运行时自检失败：有依赖缺 wheel 或安装异常" }

$size = [math]::Round((Get-ChildItem (Join-Path $desktop "runtime") -Recurse | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "`n完成：runtime\python 就绪（约 $size MB），可以跑 desktop 下 npm run dist 打包了。" -ForegroundColor Green
