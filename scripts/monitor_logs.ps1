$LogFile = "D:\APMD_eOffice_Bot\logs\bot_activity.log"

if (-not (Test-Path $LogFile)) {
    Write-Host "Waiting for log file to be created..." -ForegroundColor Yellow
}

Write-Host "Starting log monitor on $LogFile..." -ForegroundColor Green

Get-Content $LogFile -Wait -Tail 10 | ForEach-Object {
    if ($_ -match "ERROR" -or $_ -match "CRITICAL") {
        Write-Host "[ALERT] $_" -ForegroundColor Red
    }
    elseif ($_ -match "WARNING") {
        Write-Host "[WARN] $_" -ForegroundColor Yellow
    }
    else {
        Write-Host $_
    }
}
