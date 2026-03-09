import os
import ssl
from typing import Set
import csv
from datetime import datetime

import requests
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
from urllib3.exceptions import InsecureRequestWarning

# Desabilita avisos de certificados em ambientes que ainda utilizam self-signed
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# --- CONFIGURAÇÕES ---
VC_CONFIG = {
    # Em produção, o ideal é definir estas variáveis no ambiente
    # (ex.: export VC_HOST=..., VC_USER=..., VC_PASSWORD=...).
    'host': os.getenv('VC_HOST', 'tpsp1vca3n00001.tpsp1infra.local'),
    'user': os.getenv('VC_USER', 'jefferson.rsantos@tpsp1.vsphere'),
    'pwd':  os.getenv('VC_PASSWORD'),
}

CMK_CONFIG = {
    # Em produção, o ideal é definir estas variáveis no ambiente
    # (ex.: export CMK_URL=..., CMK_USER=..., CMK_SECRET=...).
    'url':    os.getenv('CMK_URL', 'https://10.107.14.66/checkmkpoc'),
    'user':   os.getenv('CMK_USER', 'check_host_vmware'),
    'secret': os.getenv('CMK_SECRET'),
}


def normalize_hostname(name: str) -> str:
    """Normaliza o nome do host para comparação entre vCenter e Checkmk.

    Converte o nome para minúsculas, remove espaços em branco nas
    extremidades e, se houver domínio (FQDN), retorna apenas a
    parte antes do primeiro ponto. Dessa forma, nomes como
    "tpsp1esx3n00040" e "tpsp1esx3n00040.tpsp1infra.local" são
    tratados como equivalentes.

    Args:
        name: Nome do host conforme retornado pela API do vCenter
            ou do Checkmk.

    Returns:
        Nome do host normalizado em minúsculas e sem sufixo de
        domínio, quando houver.
    """
    normalized = name.strip().lower()
    if not normalized:
        return normalized

    # Architect's Notes: utilizamos apenas o rótulo antes do
    # primeiro ponto para alinhar FQDN do vCenter com hostnames
    # curtos usados no Checkmk, garantindo comparação consistente.
    return normalized.split('.', 1)[0]


def get_vcenter_hosts_bulk() -> Set[str]:
    """
    Extrai todos os hosts do vCenter de uma só vez.

    Usa pyVmomi com CreateContainerView para obter a lista de
    objetos vim.HostSystem de forma performática e converte os
    nomes para um conjunto de strings normalizadas.

    Returns:
        Conjunto de nomes de hosts normalizados presentes no
        inventário do vCenter.
    """
    hosts_found = set()
    context = ssl._create_unverified_context()
    
    try:
        si = SmartConnect(host=VC_CONFIG['host'], user=VC_CONFIG['user'], 
                          pwd=VC_CONFIG['pwd'], sslContext=context)
        content = si.RetrieveContent()
        
        # ContainerView é a forma mais performática de listar objetos no vSphere
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.HostSystem], True
        )
        
        for host in container.view:
            hosts_found.add(normalize_hostname(host.name))
            
        container.Destroy()
        Disconnect(si)
        return hosts_found
    except Exception as e:
        print(f"[ERRO vCENTER] Falha na extração: {e}")
        return set()

def get_checkmk_hosts_bulk() -> Set[str]:
    """
    Consulta a coleção completa de hosts no Checkmk via REST API.

    Utiliza o endpoint de coleção (Bulk) para reduzir múltiplas
    chamadas HTTP a uma única requisição e converte os IDs dos
    hosts em um conjunto de nomes normalizados.

    Returns:
        Conjunto de nomes de hosts normalizados presentes na
        configuração do Checkmk.
    """
    # Endpoint de coleção (Bulk)
    base_url = CMK_CONFIG['url'].rstrip('/')
    api_url = f"{base_url}/check_mk/api/1.0/domain-types/host_config/collections/all"
    headers = {
        "Authorization": f"Bearer {CMK_CONFIG['user']} {CMK_CONFIG['secret']}",
        "Accept": "application/json"
    }

    try:
        response = requests.get(api_url, headers=headers, verify=False)
        if response.status_code == 200:
            data = response.json()
            # Extraímos apenas o ID de cada host na lista retornada
            # O Checkmk retorna os hosts dentro de ['value']
            cmk_hosts = {
                normalize_hostname(item['id'])
                for item in data.get('value', [])
                if 'id' in item
            }
            return cmk_hosts
        else:
            print(f"[ERRO CHECKMK] Status: {response.status_code} - {response.text}")
            return set()
    except Exception as e:
        print(f"[ERRO CHECKMK] Falha na conexão: {e}")
        return set()


def export_missing_hosts_to_csv(missing_hosts: Set[str]) -> str:
    """Gera um arquivo CSV com os hosts sem monitoramento.

    Cria um arquivo CSV no diretório atual contendo apenas a lista de
    nomes de hosts que estão presentes no vCenter, mas não estão
    configurados no Checkmk. Cada linha do arquivo contém unicamente
    o nome do host, sem cabeçalho ou colunas adicionais, para facilitar
    o consumo por outras automações ou ferramentas.

    A data/hora de execução é utilizada somente na composição do nome
    do arquivo, evitando sobrescrita entre execuções diferentes.

    Args:
        missing_hosts: Conjunto de nomes de hosts normalizados que
            foram identificados como presentes no vCenter e ausentes
            no Checkmk.

    Returns:
        Caminho relativo do arquivo CSV gerado.
    """
    if not missing_hosts:
        return ""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"hosts_sem_monitoramento_{timestamp}.csv"

    # Architect's Notes: cada linha contém apenas o hostname, o que
    # simplifica integrações futuras (ex.: leitura linha a linha para
    # criação automática de hosts no Checkmk).
    with open(filename, mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        for host in sorted(missing_hosts):
            writer.writerow([host])

    return filename

def main():
    print("="*70)
    print("ANALISE DE CONFORMIDADE DE HOSTS ENTRE vCENTER E CHECKMK".center(70))
    print("="*70)

    # 1. Coleta em massa
    print("[1/2] Consultando inventário vCenter (pyVmomi)...")
    vc_hosts_set = get_vcenter_hosts_bulk()
    
    print("[2/2] Consultando inventário Checkmk (REST API Bulk)...")
    cmk_hosts_set = get_checkmk_hosts_bulk()

    if not vc_hosts_set or not cmk_hosts_set:
        print("\n[ERRO] Não foi possível obter as listas para comparação.")
        return

    # 2. Comparação em Memória (Operação de Conjuntos)
    # Hosts que estão no vCenter mas NÃO estão no Checkmk
    missing_in_cmk = vc_hosts_set - cmk_hosts_set
    
    # Hosts que estão no Checkmk mas NÃO estão no vCenter (Hosts órfãos/antigos)
    orphans_in_cmk = cmk_hosts_set - vc_hosts_set

    # Restrição para órfãos: apenas hosts VMware (nomes contendo "esx")
    vmware_orphans_in_cmk = {host for host in orphans_in_cmk if "esx" in host}

    # 2.1. Exporta CSV com hosts sem monitoramento, se houver
    if missing_in_cmk:
        csv_file = export_missing_hosts_to_csv(missing_in_cmk)
        if csv_file:
            print(f"\n[INFO] CSV gerado com hosts sem monitoramento: {csv_file}")

    # 3. Output Executivo
    print("\n" + "-"*70)
    print(f"{'RESUMO DE CONFORMIDADE':^70}")
    print("-"*70)
    print(f"Hosts Totais no vCenter:          {len(vc_hosts_set)}")
    print(f"Hosts Totais no Checkmk:          {len(cmk_hosts_set)}")
    print(f"Hosts em Conformidade:            {len(vc_hosts_set & cmk_hosts_set)}")
    print("-"*70)

    if missing_in_cmk:
        print(f"\n⚠️  ALERTA: {len(missing_in_cmk)} Hosts SEM monitoramento:")
        for host in sorted(missing_in_cmk):
            print(f"  - {host}")
    else:
        print("\n✅ Sucesso: Todos os hosts do vCenter estão sendo monitorados.")

    if vmware_orphans_in_cmk:
        print(f"\nℹ️  INFO: {len(vmware_orphans_in_cmk)} Hosts órfãos no Checkmk (não existem no vCenter, apenas VMware/esx):")
        for host in sorted(vmware_orphans_in_cmk):
            print(f"  - {host}")

    print("\n" + "="*70)

if __name__ == "__main__":
    main()