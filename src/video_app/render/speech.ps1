param([Parameter(Mandatory=$true)][string]$Manifest)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$data = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $voices = @($synth.GetInstalledVoices() | Where-Object {$_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh-*'})
    if ($data.voice) { $synth.SelectVoice([string]$data.voice) }
    elseif ($voices.Count -gt 0) { $synth.SelectVoice($voices[0].VoiceInfo.Name) }
    else { throw 'VOICE_NOT_FOUND: Install a Chinese Windows speech voice before rendering.' }
    $synth.Rate = [int]$data.rate
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    foreach ($segment in $data.segments) {
        $synth.SetOutputToWaveFile([string]$segment.path, $format)
        $synth.Speak([string]$segment.text)
        $synth.SetOutputToNull()
    }
    @{voice=$synth.Voice.Name; segments=$data.segments.Count} | ConvertTo-Json -Compress
} finally { $synth.Dispose() }
