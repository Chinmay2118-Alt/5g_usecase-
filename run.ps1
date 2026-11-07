# Start the 5G Remote Hands simulation server.
#   .\run.ps1                       rescue rover, starts in the flood (switch disasters live on the dashboard)
#   .\run.ps1 -Scenario wildfire    start in another disaster: flood, earthquake, wildfire, landslide
#   .\run.ps1 -Scenario arm         tabletop arm
#   .\run.ps1 -NewCert        regenerate the certificate (e.g. after the laptop's IP changed)
param([ValidateSet('flood', 'earthquake', 'wildfire', 'landslide', 'rescue', 'arm')][string]$Scenario = 'flood', [switch]$NewCert)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    python -m venv .venv
    .\.venv\Scripts\python -m pip install -r requirements.txt
}
if ($NewCert -or -not (Test-Path certs\cert.pem)) {
    .\.venv\Scripts\python -m server.certs
}
.\.venv\Scripts\python -m server.main --scenario $Scenario
