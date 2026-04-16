$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path "$PSScriptRoot/..").Path
python -m grpc_tools.protoc `
  -I "$RootDir/proto" `
  --python_out "$RootDir/generated" `
  --grpc_python_out "$RootDir/generated" `
  "$RootDir/proto/memory_game.proto"

Write-Host "Stubs cliente generados en $RootDir/generated"
