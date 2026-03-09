import csv
import os
import sys
from typing import Dict, List, Set

import requests

from verificar_hosts_no_checkmk import CMK_CONFIG, normalize_hostname, get_checkmk_hosts_bulk


def build_cmk_session() -> requests.Session:
    """Cria e retorna uma sessão HTTP para a API REST do Checkmk.

    A sessão reaproveita conexões TCP e aplica, de forma centralizada,
    os cabeçalhos necessários para autenticação via Bearer Token,
    conforme as boas práticas de integração com o Checkmk.

    As credenciais e a URL base são obtidas a partir de ``CMK_CONFIG``,
    que por sua vez lê os valores das variáveis de ambiente
    ``CMK_URL``, ``CMK_USER`` e ``CMK_SECRET``.

    Returns:
        Objeto ``requests.Session`` já configurado com os cabeçalhos
        adequados para chamadas à API REST do Checkmk.

    Raises:
        ValueError: Se qualquer uma das chaves essenciais de
            configuração (URL, usuário ou segredo) não estiver definida.
    """
    base_url = CMK_CONFIG.get("url")
    user = CMK_CONFIG.get("user")
    secret = CMK_CONFIG.get("secret")

    if not base_url or not user or not secret:
        raise ValueError(
            "Configuração do Checkmk incompleta. "
            "Verifique as variáveis de ambiente CMK_URL, CMK_USER e CMK_SECRET."
        )

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {user} {secret}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    return session


def load_hosts_from_csv(csv_path: str) -> Set[str]:
    """Carrega a lista de hosts a partir de um arquivo CSV.

    O arquivo CSV deve conter um hostname por linha, sem cabeçalho,
    exatamente no formato gerado pelo script de verificação
    (``hosts_sem_monitoramento_YYYYMMDD_HHMMSS.csv``). Linhas em branco
    ou compostas apenas por espaços em branco são ignoradas.

    Args:
        csv_path: Caminho para o arquivo CSV contendo os nomes dos
            hosts que devem ser criados no Checkmk.

    Returns:
        Conjunto de nomes de hosts (não normalizados), preservando o
        texto original de cada linha do CSV.

    Raises:
        FileNotFoundError: Se o arquivo CSV informado não existir.
    """
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"Arquivo CSV não encontrado: {csv_path}")

    hosts: Set[str] = set()
    with open(csv_path, mode="r", encoding="utf-8") as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            if not row:
                continue
            raw_name = row[0].strip()
            if raw_name:
                hosts.add(raw_name)

    return hosts


def create_host_payload(host_name: str, folder: str) -> Dict:
    """Monta o payload JSON para criação de um host no Checkmk.

    Este payload segue o modelo esperado pelo endpoint
    ``domain-types/host_config/actions/create/invoke`` da API REST.
    Para hosts VMware, assumimos que regras globais de monitoramento
    (por exemplo, via vSphere special agent) serão aplicadas após a
    criação, de forma que aqui definimos apenas o mínimo necessário.

    Args:
        host_name: Nome do host a ser criado no Checkmk.
        folder: Caminho da pasta lógica no Checkmk onde o host será
            criado. O valor ``"~"`` representa a pasta principal.

    Returns:
        Dicionário Python pronto para ser serializado como JSON no
        corpo da requisição HTTP.
    """
    # Architect's Notes: mantemos o payload minimalista (apenas nome,
    # pasta e atributos vazios) para delegar a configuração detalhada
    # (tags, labels, regras de agente) às políticas do próprio Checkmk.
    return {
        "host_name": host_name,
        "folder": folder,
        "attributes": {},
    }


def create_host_in_checkmk(session: requests.Session, host_name: str, folder: str) -> bool:
    """Cria um host no Checkmk via API REST.

    Esta função executa uma chamada HTTP POST ao endpoint de criação
    de hosts do Checkmk (``domain-types/host_config/collections/all``).
    Em caso de sucesso (códigos 200 ou 201), retorna ``True``. Para
    códigos de erro conhecidos (como 409 para "host já existente") ou
    outros códigos HTTP, registra a mensagem retornada pela API e
    devolve ``False``.

    Args:
        session: Sessão HTTP previamente configurada com autenticação.
        host_name: Nome do host a ser criado.
        folder: Pasta lógica no Checkmk onde o host deve ser criado.

    Returns:
        ``True`` se o host foi criado com sucesso, ``False`` em caso
        de falha ou se o host já existir.
    """
    base_url = CMK_CONFIG["url"].rstrip("/")
    api_base = f"{base_url}/check_mk/api/1.0"
    url = f"{api_base}/domain-types/host_config/collections/all"

    payload = create_host_payload(host_name, folder)

    try:
        response = session.post(url, json=payload, verify=False)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[ERRO CHECKMK] Falha ao criar host '{host_name}': {exc}")
        return False

    if response.status_code in (200, 201):
        print(f"[OK] Host criado com sucesso no Checkmk: {host_name}")
        return True

    # Tratamento específico para host já existente (conflito)
    if response.status_code == 409:
        print(f"[AVISO] Host já existe no Checkmk, ignorando: {host_name}")
        return False

    # Demais erros: exibimos a resposta completa para facilitar o debug.
    try:
        error_body = response.json()
    except ValueError:
        error_body = response.text

    print(
        f"[ERRO CHECKMK] Falha ao criar host '{host_name}'. "
        f"Status: {response.status_code} - Resposta: {error_body}"
    )
    return False


def create_hosts_from_csv(csv_path: str, folder: str = "/") -> None:
    """Orquestra a criação de hosts no Checkmk a partir de um CSV.

    Esta função:

    1. Carrega os hosts do arquivo CSV informado.
    2. Obtém a lista atual de hosts já cadastrados no Checkmk, usando
       a mesma função de inventário em massa utilizada no script de
       verificação.
    3. Calcula quais hosts do CSV ainda não existem no Checkmk,
       considerando a normalização de nomes (FQDN vs hostname curto).
    4. Cria, via API REST, apenas os hosts efetivamente ausentes.

    Args:
        csv_path: Caminho para o arquivo CSV gerado pelo script de
            verificação, contendo um hostname por linha.
        folder: Pasta lógica no Checkmk onde os novos hosts serão
            criados. Por padrão utiliza ``"/"`` (pasta principal).

    Returns:
        None. As informações de progresso e eventuais erros são
        impressas no console.
    """
    print("=" * 70)
    print("CRIACAO AUTOMATIZADA DE HOSTS NO CHECKMK (A PARTIR DE CSV)")
    print("=" * 70)
    print(
        "\n[ATENCAO] Este script realiza operacoes de escrita no Checkmk "
        "(criacao de hosts). Certifique-se de possuir backup/snapshot "
        "recente da configuracao antes de prosseguir."
    )

    hosts_from_csv = load_hosts_from_csv(csv_path)
    if not hosts_from_csv:
        print("\n[NENHUM HOST] O arquivo CSV nao contem hosts para criacao.")
        return

    print(f"\n[1/3] Hosts lidos do CSV: {len(hosts_from_csv)}")

    # Obtemos o inventario atual do Checkmk para evitar chamadas de
    # criacao desnecessarias ou conflitos de "host ja existente".
    existing_hosts_normalized = get_checkmk_hosts_bulk()
    print(f"[2/3] Hosts atualmente cadastrados no Checkmk: {len(existing_hosts_normalized)}")

    # Normalizamos os nomes vindos do CSV utilizando a mesma regra
    # aplicada na comparacao vCenter x Checkmk.
    csv_hosts_normalized_map: Dict[str, str] = {
        normalize_hostname(raw): raw for raw in hosts_from_csv
    }

    missing_in_cmk_normalized = set(csv_hosts_normalized_map.keys()) - existing_hosts_normalized
    hosts_to_create: List[str] = [
        csv_hosts_normalized_map[norm_name] for norm_name in sorted(missing_in_cmk_normalized)
    ]

    print(f"[3/3] Hosts que serao criados no Checkmk: {len(hosts_to_create)}")
    if not hosts_to_create:
        print("\n[NENHUMA ACAO] Todos os hosts do CSV ja existem no Checkmk.")
        return

    session = build_cmk_session()

    created = 0
    for host_name in hosts_to_create:
        if create_host_in_checkmk(session, host_name, folder):
            created += 1

    print("\n" + "=" * 70)
    print(f"Resumo da execucao: {created} hosts criados de {len(hosts_to_create)} candidatos.")
    print("=" * 70)


def main() -> None:
    """Ponto de entrada principal para execucao via linha de comando.

    Uso esperado:

    .. code-block:: bash

        python criar_hosts_no_checkmk.py caminho/do/arquivo.csv [folder]

    Onde:

    - ``caminho/do/arquivo.csv`` aponta para um arquivo gerado pelo
      script de verificacao, com um hostname por linha.
    - ``folder`` (opcional) define a pasta logica no Checkmk onde os
      hosts serao criados; caso omitido, utiliza ``"~"`` (Main).
    """
    if len(sys.argv) < 2:
        print(
            "Uso: python criar_hosts_no_checkmk.py <caminho_csv> [folder]\n"
            "Exemplo: python criar_hosts_no_checkmk.py hosts_sem_monitoramento_20260305_175002.csv /"
        )
        sys.exit(1)

    csv_path = sys.argv[1]
    folder = sys.argv[2] if len(sys.argv) >= 3 else "/"

    create_hosts_from_csv(csv_path, folder)


if __name__ == "__main__":
    main()

