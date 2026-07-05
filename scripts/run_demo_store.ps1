param(
    [string]$PlatformUrl = "http://127.0.0.1:8000",
    [string]$PlatformApiKey,
    [string]$CredentialsPath = "D:\cursor-hackaton\temp\demo_seed_credentials.json",
    [int]$Port = 8100
)

$env:UCP_PLATFORM_URL = $PlatformUrl
if (-not $PlatformApiKey -and (Test-Path $CredentialsPath)) {
    $creds = Get-Content -Path $CredentialsPath -Raw | ConvertFrom-Json
    if ($creds.sdk_api_key) {
        $PlatformApiKey = $creds.sdk_api_key
    }
}
if ($PlatformApiKey) {
    $env:UCP_PLATFORM_API_KEY = $PlatformApiKey
} else {
    Remove-Item Env:UCP_PLATFORM_API_KEY -ErrorAction SilentlyContinue
}
$env:UCP_DEMO_ORDER_CAPABILITY = "1"
$env:UCP_DEMO_BASE_URL = "http://127.0.0.1:$Port"

Set-Location "D:\cursor-hackaton\python-sdk"
python -m uvicorn examples.demo_store:app --reload --port $Port
