# Speak each narration line with the Windows voice into its own WAV (44.1 kHz, 16-bit, mono).
# Usage: powershell -NoProfile -File tts.ps1 <lines.json> <out folder> <rate -10..10>
param([string]$Lines, [string]$Out, [int]$Rate = 0)
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$items = Get-Content -LiteralPath $Lines -Raw -Encoding UTF8 | ConvertFrom-Json
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($it in $items) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice("Microsoft Zira Desktop")
  $s.Rate = $Rate
  $s.SetOutputToWaveFile((Join-Path $Out ($it.id + ".wav")), $fmt)
  $s.Speak([string]$it.say)
  $s.Dispose()
}
"done " + $items.Count
