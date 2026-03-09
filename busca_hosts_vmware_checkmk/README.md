## Auditoria e Provisionamento de Hosts VMware no Checkmk

Este projeto implementa um fluxo completo para:

1. **Auditar** quais hosts ESXi existem no vCenter e se estão monitorados no Checkmk.
2. **Gerar CSV** com os hosts do vCenter que ainda não estão no Checkmk.
3. **Criar automaticamente** esses hosts no Checkmk via REST API.

Os scripts principais são:

- `set_env_vcenter_checkmk.ps1`
- `verificar_hosts_no_checkmk.py`
- `criar_hosts_no_checkmk.py`
- `teste_user.ps1` (apoio para teste de autenticação)

---

## Pré‑requisitos

- **Python** 3.x com os pacotes:
  - `requests`
  - `pyVmomi`
  - `pyVim`
- **PowerShell** (Windows)
- Usuário **vCenter** com permissão de leitura de inventário.
- Usuário **Checkmk** do tipo *Automation user* com:
  - `CMK_USER` = nome do usuário (ex.: `check_host_vmware`)
  - `CMK_SECRET` = *automation secret* configurado no Checkmk.
- URL do site Checkmk (exemplo):  
  `http://10.107.14.66/checkmkpoc`

---

## Variáveis de ambiente utilizadas

As credenciais **não** ficam hardcoded em Python. São lidas via variáveis de ambiente:

- **vCenter**
  - `VC_HOST` – FQDN ou IP do vCenter
  - `VC_USER` – usuário (ex.: `usuario@dominio.vsphere`)
  - `VC_PASSWORD` – senha do usuário

- **Checkmk**
  - `CMK_URL` – base do site (ex.: `http://10.107.14.66/checkmkpoc`)
  - `CMK_USER` – usuário de automação
  - `CMK_SECRET` – automation secret (senha do usuário de automação)

O script PowerShell `set_env_vcenter_checkmk.ps1` facilita a configuração dessas variáveis na sessão atual.

---

## Script `set_env_vcenter_checkmk.ps1`

### Função

- Configura as variáveis de ambiente para vCenter e Checkmk na **sessão atual do PowerShell**.
- Solicita interativamente:
  - Senha do vCenter (`VC_PASSWORD`)
  - Secret do Checkmk (`CMK_SECRET`)
- Ao final, executa o script Python de auditoria `verificar_hosts_no_checkmk.py`.

### Uso

No diretório do projeto:

```powershell
.\set_env_vcenter_checkmk.ps1
```

Fluxo:

1. Exibe a mensagem de configuração.
2. Caso as variáveis ainda não existam, pergunta:
   - Senha do vCenter.
   - Segredo (API secret) do usuário Checkmk.
3. Exporta:
   - `VC_HOST`, `VC_USER`, `VC_PASSWORD`
   - `CMK_URL`, `CMK_USER`, `CMK_SECRET`
4. Chama:

```powershell
python ".\verificar_hosts_no_checkmk.py"
```

---

## Script `verificar_hosts_no_checkmk.py`

### Objetivo

- Conectar no **vCenter** (via `pyVmomi`) e obter todos os hosts ESXi.
- Conectar no **Checkmk** (via REST API) e obter todos os hosts configurados.
- **Normalizar nomes** (FQDN → hostname curto, tudo em lowercase).
- Calcular:
  - Hosts **no vCenter e não no Checkmk** (sem monitoramento).
  - Hosts **no Checkmk e não no vCenter**, filtrando apenas os que contêm `"esx"` (órfãos VMware).
- **Gerar CSV** apenas com os hosts sem monitoramento.

### Principais funções

- **`normalize_hostname(name: str) -> str`**  
  Remove domínio e converte para minúsculas, para tratar:

  - `tpsp1esx3n00040.tpsp1infra.local` → `tpsp1esx3n00040`

- **`get_vcenter_hosts_bulk() -> Set[str]`**  
  - Conecta ao vCenter usando `SmartConnect`.
  - Usa `CreateContainerView` com `vim.HostSystem` para listar hosts.
  - Retorna um `set` com nomes normalizados.

- **`get_checkmk_hosts_bulk() -> Set[str]`**  
  - Monta a URL:  
    `f"{CMK_URL.rstrip('/')}/check_mk/api/1.0/domain-types/host_config/collections/all"`
  - Usa `requests` com header `Authorization: Bearer <user> <secret>`.
  - Extrai `item["id"]` de `data["value"]`, normalizando via `normalize_hostname`.

- **`export_missing_hosts_to_csv(missing_hosts: Set[str]) -> str`**  
  - Gera arquivo:  
    `hosts_sem_monitoramento_YYYYMMDD_HHMMSS.csv`
  - Cada linha contém **somente o hostname** (sem cabeçalho).

- **`main()`**  
  - Coleta inventário vCenter e Checkmk.
  - Calcula:
    - `missing_in_cmk = vc_hosts_set - cmk_hosts_set`
    - `orphans_in_cmk = cmk_hosts_set - vc_hosts_set`
    - `vmware_orphans_in_cmk = {h for h in orphans_in_cmk if "esx" in h}`
  - Gera CSV se houver `missing_in_cmk`.
  - Exibe resumo executivo no console:
    - Totais vCenter / Checkmk.
    - Hosts em conformidade.
    - Lista de hosts sem monitoramento.
    - Lista de hosts órfãos (apenas VMware / `"esx"`).

### Execução direta (sem PowerShell)

Se as variáveis de ambiente já estiverem definidas:

```bash
python verificar_hosts_no_checkmk.py
```

O CSV gerado (quando houver faltantes) será algo como:

- `hosts_sem_monitoramento_20260305_175002.csv`

---

## Script `criar_hosts_no_checkmk.py`

### Objetivo

- Ler o CSV gerado pelo script de auditoria.
- Verificar novamente os hosts já existentes no Checkmk.
- Criar **apenas** os hosts que ainda não existem, via REST API.

### Principais funções

- **`build_cmk_session() -> requests.Session`**  
  - Lê `CMK_URL`, `CMK_USER`, `CMK_SECRET` de `CMK_CONFIG` (mesma config do outro script).
  - Cria uma `Session` com headers padrão:
    - `Authorization: Bearer <user> <secret>`
    - `Accept: application/json`
    - `Content-Type: application/json`

- **`load_hosts_from_csv(csv_path: str) -> Set[str]`**  
  - Lê um hostname por linha (formato gerado pelo `verificar_hosts_no_checkmk.py`).
  - Ignora linhas vazias.

- **`create_host_payload(host_name: str, folder: str) -> Dict`**  
  - Monta o JSON mínimo para criação:
    - `{"host_name": host_name, "folder": folder, "attributes": {}}`
  - A pasta default é `"/"` (Main folder).

- **`create_host_in_checkmk(session, host_name, folder) -> bool`**  
  - Monta a URL base:  
    `API_URL = f"{CMK_URL.rstrip('/')}/check_mk/api/1.0"`
  - Chama:
    - `POST {API_URL}/domain-types/host_config/collections/all`
  - Trata:
    - `200/201` → sucesso.
    - `409` → host já existe.
    - Outros códigos → imprime corpo do erro.

- **`create_hosts_from_csv(csv_path: str, folder: str = "/") -> None`**  
  Fluxo:
  1. Lê os hosts do CSV.
  2. Obtém inventário atual do Checkmk via `get_checkmk_hosts_bulk()`.
  3. Normaliza os nomes do CSV com `normalize_hostname`.
  4. Calcula **apenas os que ainda não existem** no Checkmk.
  5. Para cada host faltante, chama `create_host_in_checkmk`.
  6. Exibe resumo final: quantos foram criados.

- **`main()`**  
  - Uso esperado:

    ```bash
    python criar_hosts_no_checkmk.py caminho/do/arquivo.csv [folder]
    ```

  - Exemplo:

    ```bash
    python criar_hosts_no_checkmk.py hosts_sem_monitoramento_20260305_175002.csv /
    ```

---

## Script `teste_user.ps1`

### Objetivo

- Testar **isoladamente** a autenticação na REST API do Checkmk com o usuário de automação.
- Útil para depurar erros HTTP `401` (não autenticado).

### Comportamento típico

- Monta a URL da API, por exemplo:

  ```powershell
  $apiUrl = "http://10.107.14.66/checkmkpoc/check_mk/api/1.0/domain-types/host_config/collections/all"
  $cmkUser = "check_host_vmware"
  $cmkSecret = "<automation_secret>"
  ```

- Define headers:

  ```powershell
  $headers = @{
    "Authorization" = "Bearer $cmkUser $cmkSecret"
    "Accept"        = "application/json"
  }
  ```

- Executa:

  ```powershell
  Invoke-WebRequest -Uri $apiUrl -Headers $headers -Method GET
  ```

- Se as credenciais estiverem corretas, retorna JSON com a coleção de hosts.
- Se houver erro, exibe resposta no console (por exemplo, `401` com mensagem de falta de autenticação).

---

## Fluxo completo recomendado

1. **Configurar variáveis de ambiente e rodar auditoria**

   ```powershell
   .\set_env_vcenter_checkmk.ps1
   ```

   - Coleta inventário do vCenter e do Checkmk.
   - Exibe hosts:
     - Sem monitoramento.
     - Órfãos (apenas VMware / `"esx"`).
   - Gera um CSV com hosts sem monitoramento:
     - `hosts_sem_monitoramento_YYYYMMDD_HHMMSS.csv`

2. **(Opcional) Validar autenticação na API (debug)**

   ```powershell
   .\teste_user.ps1
   ```

3. **Criar automaticamente os hosts faltantes no Checkmk**

   ```powershell
   python criar_hosts_no_checkmk.py hosts_sem_monitoramento_YYYYMMDD_HHMMSS.csv /
   ```

   - Cria apenas os hosts que:
     - Estão no CSV.
     - Ainda não existem no Checkmk (considerando normalização FQDN/hostname).

4. **No Checkmk (GUI)**

   - Realizar **Service Discovery** nos novos hosts.
   - **Ativar as mudanças** (`Activate changes`).

> **Observação de segurança:**  
> Os scripts de criação de host fazem **operações de escrita** no Checkmk.  
> Antes de rodar em produção, sempre garanta que há backup/snapshot recente da configuração do site.

