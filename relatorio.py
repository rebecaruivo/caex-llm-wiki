"""Relatório semanal de uso do oráculo, para a automação no Make.

Junta, num período, as perguntas feitas no chat e os envios de atas. Uma
resposta sem nenhuma fonte é tratada como lacuna: a wiki não tinha a
informação. O relatório é lido pela automação e resumido por IA para a
gestão; por isso ele leva só o texto das perguntas (que o chat avisa para
não conter dados pessoais), nunca respostas, ids de chat ou de envio.
"""
from collections import Counter

LIMITE_LACUNAS = 50
LIMITE_CARACTERES = 300


def _texto_da_pergunta(mensagem):
    """A versão completa quando a pergunta dependia das anteriores."""
    texto = mensagem.get("pergunta_interpretada") or mensagem.get("pergunta") or ""
    texto = " ".join(str(texto).split())
    return texto[:LIMITE_CARACTERES]


def montar_relatorio(mensagens, envios, inicio, fim):
    """Monta o relatório a partir das linhas do banco. Função pura."""
    mensagens = list(mensagens or [])
    lacunas = []
    vistas = set()
    for mensagem in mensagens:
        if mensagem.get("fontes"):
            continue
        texto = _texto_da_pergunta(mensagem)
        chave = texto.casefold().rstrip("?!. ")
        if not texto or chave in vistas:
            continue
        vistas.add(chave)
        lacunas.append({"pergunta": texto, "quando": mensagem.get("criado_em")})

    sem_fonte = sum(1 for mensagem in mensagens if not mensagem.get("fontes"))
    return {
        "periodo": {"inicio": inicio, "fim": fim},
        "perguntas": len(mensagens),
        "reaproveitadas_do_cache": sum(1 for m in mensagens if m.get("do_cache")),
        "respondidas_com_fonte": len(mensagens) - sem_fonte,
        "sem_fonte": sem_fonte,
        "com_citacao_invalida": sum(1 for m in mensagens if m.get("citacoes_invalidas")),
        "lacunas": lacunas[:LIMITE_LACUNAS],
        "lacunas_omitidas": max(0, len(lacunas) - LIMITE_LACUNAS),
        "envios_de_atas": dict(Counter(str(e.get("status")) for e in envios or [])),
    }
