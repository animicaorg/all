param(
    [string]$DeviceId = ""
)

# Simple helper: fetch deps and run the Flutter wallet on a specified device
Push-Location -Path (Join-Path $PSScriptRoot "..")
if(Test-Path -Path "./wallet") { Set-Location -Path ./wallet }

Write-Host "Running flutter pub get..."
flutter pub get
if($LASTEXITCODE -ne 0) { Write-Host "flutter pub get failed"; exit $LASTEXITCODE }

# If .env is missing, create it from .env.example to simplify dev flow
if(-not (Test-Path -Path "./.env")){
    if(Test-Path -Path "./.env.example"){
        Write-Host "Creating .env from .env.example"
        Copy-Item -Path ./.env.example -Destination ./.env
    }
}

if([string]::IsNullOrEmpty($DeviceId)){
    Write-Host "Running on default device (use -DeviceId to target an emulator/device)"
    flutter run
}else{
    Write-Host "Running on device: $DeviceId"
    flutter run -d $DeviceId
}

Pop-Location
