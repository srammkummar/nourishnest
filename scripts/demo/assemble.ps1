$ErrorActionPreference = 'Stop'
$demoOutput = Join-Path $PSScriptRoot '../../artifacts/demo'
$encoder = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $encoder) {
    throw 'FFmpeg is unavailable. No installation attempted. Use the offline presentation and recording instructions.'
}
Push-Location $demoOutput
try {
    & $encoder.Source -n -f concat -safe 0 -i frames.ffconcat -t 360 -vf 'fps=30,format=yuv420p' -c:v libx264 -crf 18 -movflags +faststart -an nourishnest-product-technical-demo.mp4
    if ($LASTEXITCODE -ne 0) { throw 'FFmpeg assembly failed.' }
    $probe = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($probe) {
        & $probe.Source -v error -show_entries 'format=duration,size:stream=codec_name,width,height,r_frame_rate' -of json nourishnest-product-technical-demo.mp4
    }
} finally {
    Pop-Location
}
