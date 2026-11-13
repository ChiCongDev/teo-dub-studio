$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$basePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$gpuPython = Join-Path $projectRoot '.venv-inpaint\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $basePython)) { throw 'Hay cai Teo Dub Studio truoc.' }
if (-not (Test-Path -LiteralPath $gpuPython)) {
    & $basePython -m venv (Join-Path $projectRoot '.venv-inpaint')
    if ($LASTEXITCODE -ne 0) { throw 'Khong tao duoc moi truong GPU.' }
}
& $gpuPython -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128 --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Cai PyTorch CUDA that bai.' }
& $gpuPython -m pip install numpy==2.2.6 opencv-python-headless==4.12.0.88 av==18.1.0 --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Cai thu vien video that bai.' }
$modelDirectory = Join-Path $projectRoot 'models\inpaint'
New-Item -ItemType Directory -Force -Path $modelDirectory | Out-Null
$modelPath = Join-Path $modelDirectory 'sttn.pth'
$expectedHash = '25b0c2c30042d82efd1893bd42ec726764262d94115393a1718f8d65d2a7817b'
if (-not (Test-Path -LiteralPath $modelPath) -or (Get-FileHash -LiteralPath $modelPath -Algorithm SHA256).Hash -ne $expectedHash) {
    $partialPath = Join-Path $modelDirectory ('sttn-' + [guid]::NewGuid().ToString('N') + '.download')
    Invoke-WebRequest 'https://raw.githubusercontent.com/YaoFANGUK/video-subtitle-remover/e109b9ddc1d0e8f153199dfa05c1d767546906d8/backend/models/sttn-det/sttn.pth' -OutFile $partialPath
    if ((Get-FileHash -LiteralPath $partialPath -Algorithm SHA256).Hash -ne $expectedHash) { throw 'SHA256 model khong khop. Khong su dung tep vua tai.' }
    Move-Item -LiteralPath $partialPath -Destination $modelPath -Force
}
# Bo giong noi goc (13/11/2026): Demucs htdemucs chay tren CPU, cung moi truong nay. Chi cai ma Python thuan (khong keo them torch).
& $gpuPython -m pip install demucs==4.0.1 einops==0.8.2 julius==0.2.8 openunmix==1.3.0 pyyaml==6.0.3 tqdm==4.70.1 omegaconf==2.3.0 antlr4-python3-runtime==4.9.3 --no-deps --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Cai thu vien tach giong that bai.' }
$voiceDirectory = Join-Path $projectRoot 'models\demucs'
New-Item -ItemType Directory -Force -Path $voiceDirectory | Out-Null
$voicePath = Join-Path $voiceDirectory 'htdemucs-955717e8.th'
$voiceHash = '8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4'
if (-not (Test-Path -LiteralPath $voicePath) -or (Get-FileHash -LiteralPath $voicePath -Algorithm SHA256).Hash -ne $voiceHash) {
    $voicePartial = Join-Path $voiceDirectory ('htdemucs-' + [guid]::NewGuid().ToString('N') + '.download')
    Invoke-WebRequest 'https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th' -OutFile $voicePartial
    if ((Get-FileHash -LiteralPath $voicePartial -Algorithm SHA256).Hash -ne $voiceHash) { throw 'SHA256 mo hinh tach giong khong khop. Khong su dung tep vua tai.' }
    Move-Item -LiteralPath $voicePartial -Destination $voicePath -Force
}
& $gpuPython -X utf8 -B -m teodub.voice_removal_worker --check
if ($LASTEXITCODE -ne 0) { throw 'Chua chay duoc bo tach giong nguoi noi.' }
Write-Host 'Da san sang bo giong noi goc (chay bang CPU).'
& $gpuPython -X utf8 -B -m teodub.inpaint_worker --check
if ($LASTEXITCODE -ne 0) { throw 'Chua su dung duoc GPU NVIDIA; khong chuyen sang CPU.' }
Write-Host 'Da san sang xoa chu bang GPU.'
