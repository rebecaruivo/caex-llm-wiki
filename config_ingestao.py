"""Constantes e switches operacionais da ingestão de fontes.

Os valores vêm da especificação (`docs/especificacao-ingest.md`, §11.3) e são
lidos uma vez na importação. Segredos nunca ficam aqui: só nomes de variáveis.
"""
import os

# Limites de entrada.
MAX_BYTES = 51200
STATUS_RATE_LIMIT_POR_MINUTO = 60
INTERVALO_DISPATCH_SEGUNDOS = 30
LOCK_DIAS = 7
RETENCAO_BRUTO_DIAS = 30
RETENCAO_JOB_DIAS = 90
MAX_FALHAS_DISPATCH = 5
MAX_TENTATIVAS = 3
TIMEOUT_PROCESSANDO_MINUTOS = 15

# Modelo e fuso usados pelo worker.
MODELO_INGEST = "gemini-3.5-flash-lite"
FUSO = "America/Sao_Paulo"

# Tipos aceitos no formulário e seu mapeamento para o manifesto.
TIPOS = ("ata", "guia", "outro")
AUTORIDADE_POR_TIPO = {
    "ata": "ca-deliberativa",
    "guia": "ca-operacional",
    "outro": "relato",
}
PASTA_POR_TIPO = {
    "ata": "atas",
    "guia": "guias",
    "outro": "relatos",
}
PREFIXO_POR_TIPO = {
    "ata": "ATA",
    "guia": "GUIA",
    "outro": "REL",
}

# Status que bloqueiam um reenvio por duplicata.
STATUS_BLOQUEANTES = (
    "na_fila",
    "processando",
    "aguardando_revisao",
    "publicado",
    "sem_mudancas",
    "quarentena",
)

# Resultados terminais que permitem reenvio.
STATUS_REENVIAVEL = ("falhou", "expirado", "fechado_sem_merge")

# Domínio institucional aceito em texto público.
DOMINIO_INSTITUCIONAL = "@example.edu"

# Regexes de PII óbvia. Nomes são tratados depois, no worker.
PII_REGEX = {
    "cpf": r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
    "ra": r"\bRA\b[\s:.-]*\d{4,}\b",
    "telefone": r"(?:\+?55[\s.-]*)?(?:\(?\d{2}\)?[\s.-]*)?\d{4,5}[\s.-]?\d{4}\b",
    "email": r"[\w.+-]+@[\w.-]+\.\w+",
}

# Pré-filtro de injeção óbvia (lista de regexes), conforme spec §7.2.
# Os padrões só casam frases dirigidas à IA; palavras isoladas como
# `ignorar`, `regra` ou `instrução` não disparam quarentena.
PREFILTRO = (
    r"ignore\s+(?:(?:all|any|the)\s+)?(?:previous|prior|above)\s+(?:instructions|rules|prompts)",
    r"ignore\s+(?:as|todas\s+as)\s+(?:instru[cç][oõ]es|regras|ordens)\s+(?:anteriores|acima)",
    r"desconsidere\s+(?:as|todas\s+as)\s+(?:instru[cç][oõ]es|regras)",
    r"voc[êe]\s+[ée]\s+um(?:a)?\s+(?:assistente|modelo|ia)\b",
    r"you\s+are\s+(?:now\s+)?an?\s+(?:ai|assistant|model)\b",
    r"(?:system|developer)\s+prompt",
    r"prompt\s+do\s+sistema",
    r"(?:reveal|show|print|repeat)\s+(?:your|the)\s+(?:system\s+prompt|instructions)",
    r"(?:mostre|revele|imprima|repita)\s+(?:o|as|suas)\s+(?:prompt|instru[cç][oõ]es)",
    r"jailbreak|DAN\s+mode",
)


def _bool_env(nome, padrao=False):
    """Lê um switch booleano do ambiente; só 'true' liga."""
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() == "true"


def _int_env(nome, padrao):
    """Lê um switch inteiro do ambiente, caindo no padrão se inválido."""
    valor = os.environ.get(nome)
    if valor is None or not valor.strip():
        return padrao
    try:
        return int(valor)
    except ValueError:
        return padrao


# Três switches vêm do ambiente. Padrões documentados na especificação §11.1.
INGEST_ABERTO = _bool_env("INGEST_ABERTO", False)
INGEST_MAX_DIA = _int_env("INGEST_MAX_DIA", 20)
INGEST_MAX_POR_IP = _int_env("INGEST_MAX_POR_IP", 3)
