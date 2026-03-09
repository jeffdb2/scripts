param(
    [string]$VcHost  = "tpsp1vca3n00001.tpsp1infra.local",
    [string]$VcUser  = "jefferson.rsantos@tpsp1.vsphere",
    [string]$CmkUrl  = "http://10.107.14.66/checkmkpoc",
    [string]$CmkUser = "check_host_vmware"
)

Write-Host "== Configuração de variáveis de ambiente para vCenter e Checkmk =="

# vCenter
if (-not $env:VC_HOST) {
    $env:VC_HOST = $VcHost
}
if (-not $env:VC_USER) {
    $env:VC_USER = $VcUser
}
if (-not $env:VC_PASSWORD) {
    $secureVcPassword = Read-Host "Informe a senha do usuário vCenter ($VcUser)" -AsSecureString
    $plainVcPassword  = [System.Net.NetworkCredential]::new("", $secureVcPassword).Password
    $env:VC_PASSWORD  = $plainVcPassword
}

# Checkmk
if (-not $env:CMK_URL) {
    $env:CMK_URL = $CmkUrl
}
if (-not $env:CMK_USER) {
    $env:CMK_USER = $CmkUser
}
if (-not $env:CMK_SECRET) {
    $secureCmkSecret = Read-Host "Informe o segredo (API secret) do usuário Checkmk ($CmkUser)" -AsSecureString
    $plainCmkSecret  = [System.Net.NetworkCredential]::new("", $secureCmkSecret).Password
    $env:CMK_SECRET  = $plainCmkSecret
}

Write-Host ""
Write-Host "Variáveis de ambiente configuradas para esta sessão."
Write-Host "Executando o script Python de auditoria..." -ForegroundColor Green
Write-Host ""

python ".\verificar_hosts_no_checkmk.py"