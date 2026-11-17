"""Painel de uso do oráculo: agrega o histórico por semana.

Função pura, testável sem banco. Conta perguntas, respostas com e sem fonte,
cache, avaliações e envios de atas por semana (segunda a domingo, no horário
de Brasília), e lista as lacunas mais perguntadas e as respostas marcadas com
erro. Os números saem prontos: a página só desenha.
"""
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
LIMITE_LISTAS = 8
STATUS_PUBLICADO = "publicado"
STATUS_EM_ANDAMENTO = ("na_fila", "processando", "aguardando_revisao")


def _dia_local(instante):
    """Data em Brasília de um timestamp ISO do banco, ou None."""
    try:
        valor = datetime.fromisoformat(str(instante).replace("Z", "+00:00"))
    except ValueError:
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(FUSO).date()


def inicio_da_semana(dia):
    return dia - timedelta(days=dia.weekday())


def semanas_do_periodo(hoje, quantidade):
    """Segundas-feiras das últimas `quantidade` semanas, da mais antiga à atual."""
    atual = inicio_da_semana(hoje)
    return [atual - timedelta(weeks=n) for n in range(quantidade - 1, -1, -1)]


def _pergunta(mensagem):
    texto = mensagem.get("pergunta_interpretada") or mensagem.get("pergunta") or ""
    return " ".join(str(texto).split())[:200]


def _chave(texto):
    return re.sub(r"[?!.\s]+$", "", texto.casefold())


def _mais_frequentes(textos):
    """Agrupa perguntas iguais (sem caixa e pontuação final), mais pedidas primeiro."""
    contagem, exemplo = Counter(), {}
    for texto in textos:
        chave = _chave(texto)
        if not chave:
            continue
        contagem[chave] += 1
        exemplo.setdefault(chave, texto)
    return [
        {"pergunta": exemplo[chave], "vezes": vezes}
        for chave, vezes in sorted(contagem.items(), key=lambda par: (-par[1], par[0]))
    ][:LIMITE_LISTAS]


def montar_painel(mensagens, envios, hoje, semanas=8, wiki=None):
    """Monta os números do painel a partir das linhas do banco."""
    inicios = semanas_do_periodo(hoje, semanas)
    primeira = inicios[0]
    linhas = {
        inicio: {
            "inicio": inicio.isoformat(),
            "perguntas": 0, "com_fonte": 0, "sem_fonte": 0, "do_cache": 0,
            "uteis": 0, "erros": 0, "publicadas": 0, "outros_envios": 0,
        }
        for inicio in inicios
    }
    lacunas, erros = [], []
    for mensagem in mensagens or []:
        dia = _dia_local(mensagem.get("criado_em"))
        if dia is None or dia < primeira:
            continue
        linha = linhas.get(inicio_da_semana(dia))
        if linha is None:
            continue
        linha["perguntas"] += 1
        if mensagem.get("fontes"):
            linha["com_fonte"] += 1
        else:
            linha["sem_fonte"] += 1
            lacunas.append(_pergunta(mensagem))
        if mensagem.get("do_cache"):
            linha["do_cache"] += 1
        if mensagem.get("avaliacao") == "positiva":
            linha["uteis"] += 1
        elif mensagem.get("avaliacao") == "erro":
            linha["erros"] += 1
            erros.append(_pergunta(mensagem))

    envios_por_status = Counter()
    for envio in envios or []:
        dia = _dia_local(envio.get("criado_em"))
        if dia is None or dia < primeira:
            continue
        linha = linhas.get(inicio_da_semana(dia))
        if linha is None:
            continue
        status = str(envio.get("status"))
        envios_por_status[status] += 1
        if status == STATUS_PUBLICADO:
            linha["publicadas"] += 1
        else:
            linha["outros_envios"] += 1

    serie = [linhas[inicio] for inicio in inicios]
    total = lambda campo: sum(linha[campo] for linha in serie)
    perguntas = total("perguntas")
    return {
        "periodo": {"inicio": primeira.isoformat(), "fim": hoje.isoformat(), "semanas": semanas},
        "totais": {
            "perguntas": perguntas,
            "com_fonte": total("com_fonte"),
            "sem_fonte": total("sem_fonte"),
            "do_cache": total("do_cache"),
            "uteis": total("uteis"),
            "erros": total("erros"),
            "publicadas": total("publicadas"),
            "envios": sum(envios_por_status.values()),
            "pct_com_fonte": round(100 * total("com_fonte") / perguntas) if perguntas else None,
            "pct_do_cache": round(100 * total("do_cache") / perguntas) if perguntas else None,
        },
        "semanas": serie,
        "envios_por_status": dict(envios_por_status),
        "lacunas_frequentes": _mais_frequentes(lacunas),
        "respostas_com_erro": _mais_frequentes(erros),
        "wiki": wiki or {},
    }
