$apiUrl = "http://10.107.14.66/checkmkpoc/check_mk/api/1.0/domain-types/host_config/collections/all"
$cmkUser = "check_host_vmware"
$cmkSecret = "XUASTBRAHWQYE@YUPTB@"

$headers = @{
  "Authorization" = "Bearer $cmkUser $cmkSecret"
  "Accept"        = "application/json"
}

Invoke-WebRequest -Uri $apiUrl -Headers $headers -Method GET