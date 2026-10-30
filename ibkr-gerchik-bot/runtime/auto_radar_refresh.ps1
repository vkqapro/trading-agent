$json = Invoke-RestMethod -Uri "http://127.0.0.1:8550/api/radar/refresh" -Method Post -ContentType "application/json" -Body "{}" -TimeoutSec 1800
$json | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 "C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot\runtime\auto_radar_refresh_result.json"
