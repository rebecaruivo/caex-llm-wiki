"""Relatório semanal do oráculo, para a automação no Make.

Junta, num período, o uso do chat, os envios de atas, as pendências da wiki
e as datas da semana. O código monta os FATOS (listas de títulos, motivos,
pendências e prazos) já em HTML, para não depender de um modelo lembrar de
cada item. A IA recebe só o que precisa interpretar: os números de uso e as
lacunas, para agrupar por tema e sugerir ações.

Uma resposta sem nenhuma fonte é tratada como lacuna: a wiki não tinha a
informação. O relatório leva o texto das perguntas (que o chat avisa para
não conter dados pessoais), nunca respostas, ids de chat ou de envio. Títulos
de envios passam pela mesma regra de nomes do ingest.
"""
import html
import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import ingerir

LIMITE_LACUNAS = 50
LIMITE_CARACTERES = 300
DIAS_DE_PRAZO = 7

MOTIVOS = {
    "classificacao": "falha ao fazer a triagem",
    "compilacao": "a IA não conseguiu propor mudanças válidas",
    "schema": "resposta da IA fora do formato",
    "lint": "a revisão automática recusou as mudanças",
    "pii": "dados pessoais no texto",
    "dispatch": "não foi possível iniciar o processamento",
    "timeout": "o processamento travou",
    "infra": "falha de infraestrutura",
}
MOTIVOS_POR_STATUS = {
    "quarentena": "suspeita de instrução dirigida à IA; aguarda a gestão",
    "expirado": "expirou antes de ser processado",
    "fechado_sem_merge": "a gestão fechou o PR sem aprovar",
}
NAO_CONCLUIDOS = ("falhou", "quarentena", "expirado", "fechado_sem_merge")
DATA = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
CITACAO = re.compile(r"\s*\[fonte: [A-Z0-9-]+\]")


def _texto_da_pergunta(mensagem):
    """A versão completa quando a pergunta dependia das anteriores."""
    texto = mensagem.get("pergunta_interpretada") or mensagem.get("pergunta") or ""
    texto = " ".join(str(texto).split())
    return texto[:LIMITE_CARACTERES]


def _lacunas(mensagens):
    lacunas, vistas = [], set()
    for mensagem in mensagens:
        if mensagem.get("fontes"):
            continue
        texto = _texto_da_pergunta(mensagem)
        chave = texto.casefold().rstrip("?!. ")
        if not texto or chave in vistas:
            continue
        vistas.add(chave)
        lacunas.append({"pergunta": texto, "quando": mensagem.get("criado_em")})
    return lacunas


def _titulo_publico(envio):
    """Título sem os nomes que a triagem detectou, pela regra do ingest."""
    titulo = " ".join(str(envio.get("titulo") or "sem título").split())
    nomes = envio.get("nomes_detectados") or []
    return ingerir.remover_nomes(titulo, nomes if isinstance(nomes, list) else [])


def _dias_desde(instante, hoje):
    try:
        return (hoje - date.fromisoformat(str(instante)[:10])).days
    except ValueError:
        return None


def separar_envios(envios, hoje):
    """Envios do período agrupados pelo que a gestão precisa saber."""
    grupos = {"publicados": [], "aguardando_revisao": [], "sem_mudancas": [], "nao_concluidos": []}
    for envio in envios or []:
        status = envio.get("status")
        item = {"titulo": _titulo_publico(envio)}
        if status == "publicado":
            item["pr_url"] = envio.get("pr_url")
            grupos["publicados"].append(item)
        elif status == "aguardando_revisao":
            item["pr_url"] = envio.get("pr_url")
            item["dias_esperando"] = _dias_desde(envio.get("atualizado_em"), hoje)
            grupos["aguardando_revisao"].append(item)
        elif status == "sem_mudancas":
            grupos["sem_mudancas"].append(item)
        elif status in NAO_CONCLUIDOS:
            item["motivo"] = MOTIVOS_POR_STATUS.get(status) or MOTIVOS.get(
                envio.get("motivo"), "motivo não registrado"
            )
            grupos["nao_concluidos"].append(item)
    return grupos


def ler_pendencias(raiz):
    """Linhas P-xx e documentos faltando de `wiki/pendencias.md`."""
    caminho = Path(raiz) / "wiki/pendencias.md"
    if not caminho.exists():
        return [], []
    pendencias, faltando, secao = [], [], None
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if linha.startswith("## "):
            secao = linha[3:].strip()
            continue
        celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
        if linha.startswith("|") and re.fullmatch(r"P-\d+", celulas[0]):
            pendencias.append({
                "id": celulas[0],
                "problema": CITACAO.sub("", celulas[1]) if len(celulas) > 1 else "",
                "quem_resolve": celulas[3] if len(celulas) > 3 else "",
            })
        elif secao == "Documentos faltando" and linha.startswith("- [ ]"):
            faltando.append(linha[5:].strip())
    return pendencias, faltando


def prazos_da_semana(raiz, hoje, dias=DIAS_DE_PRAZO):
    """Linhas da wiki com uma data entre hoje e os próximos `dias` dias.

    Ignora linhas já marcadas como substituídas e as páginas que o código
    escreve (índice, log, pendências).
    """
    fim = hoje + timedelta(days=dias)
    achados = []
    wiki = Path(raiz) / "wiki"
    for pagina in sorted(wiki.rglob("*.md")):
        relativo = pagina.relative_to(raiz).as_posix()
        if relativo in ingerir.ARQUIVOS_DO_CODIGO:
            continue
        for linha in pagina.read_text(encoding="utf-8").splitlines():
            if not linha.startswith(("- ", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
                continue
            if "(substituída por" in linha:
                continue
            for dia, mes, ano in DATA.findall(linha):
                try:
                    quando = date(int(ano), int(mes), int(dia))
                except ValueError:
                    continue
                if hoje <= quando <= fim:
                    texto = CITACAO.sub("", linha).lstrip("-0123456789. ").strip()
                    achados.append({"data": quando.isoformat(), "texto": texto, "pagina": relativo})
                    break
    return sorted(achados, key=lambda item: item["data"])


def _data_br(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def _lista(itens, vazio):
    if not itens:
        return f"<p>{html.escape(vazio)}</p>"
    return "<ul>" + "".join(f"<li>{item}</li>" for item in itens) + "</ul>"


def _link(texto, url):
    texto = html.escape(texto)
    if url and str(url).startswith("https://"):
        return f'{texto} (<a href="{html.escape(url)}">PR</a>)'
    return texto


def html_fatos(envios, pendencias, faltando, prazos):
    """Seções factuais do e-mail, montadas por código e escapadas."""
    e = html.escape
    partes = [
        "<h2>Atas e documentos da semana</h2>",
        "<h3>Publicados na wiki</h3>",
        _lista([_link(i["titulo"], i.get("pr_url")) for i in envios["publicados"]],
               "Nenhum documento publicado nesta semana."),
        "<h3>Aguardando revisão da gestão</h3>",
        _lista([
            _link(i["titulo"], i.get("pr_url"))
            + (f" — há {i['dias_esperando']} dia(s)" if i.get("dias_esperando") is not None else "")
            for i in envios["aguardando_revisao"]
        ], "Nenhum PR esperando revisão."),
        "<h3>Não geraram mudança</h3>",
        _lista([e(i["titulo"]) for i in envios["sem_mudancas"]],
               "Nenhum envio sem mudanças."),
        "<h3>Não concluídos</h3>",
        _lista([f"{e(i['titulo'])} — {e(i['motivo'])}" for i in envios["nao_concluidos"]],
               "Nenhum envio falhou."),
        "<h2>Pendências e prazos</h2>",
        "<h3>Datas desta semana na wiki</h3>",
        _lista([f"<strong>{e(_data_br(p['data']))}</strong>: {e(p['texto'])}" for p in prazos],
               "Nenhuma data na wiki para os próximos 7 dias."),
        "<h3>Pendências abertas</h3>",
        _lista([
            f"<strong>{e(p['id'])}</strong>: {e(p['problema'])}"
            + (f" (resolve: {e(p['quem_resolve'])})" if p.get("quem_resolve") else "")
            for p in pendencias
        ], "Nenhuma pendência aberta."),
        "<h3>Documentos faltando</h3>",
        _lista([e(d) for d in faltando], "Nenhum documento marcado como faltando."),
    ]
    return "".join(partes)


def montar_relatorio(mensagens, envios, inicio, fim, raiz=None, hoje=None):
    """Monta o relatório a partir das linhas do banco e da wiki."""
    mensagens = list(mensagens or [])
    envios = list(envios or [])
    if hoje is None:
        try:
            hoje = date.fromisoformat(str(fim)[:10])
        except ValueError:
            hoje = date.today()
    lacunas = _lacunas(mensagens)
    sem_fonte = sum(1 for m in mensagens if not m.get("fontes"))
    grupos = separar_envios(envios, hoje)
    pendencias, faltando = ler_pendencias(raiz) if raiz else ([], [])
    prazos = prazos_da_semana(raiz, hoje) if raiz else []

    uso = {
        "perguntas": len(mensagens),
        "reaproveitadas_do_cache": sum(1 for m in mensagens if m.get("do_cache")),
        "respondidas_com_fonte": len(mensagens) - sem_fonte,
        "sem_fonte": sem_fonte,
        "com_citacao_invalida": sum(1 for m in mensagens if m.get("citacoes_invalidas")),
        "lacunas": lacunas[:LIMITE_LACUNAS],
        "lacunas_omitidas": max(0, len(lacunas) - LIMITE_LACUNAS),
        "documentos_faltando": faltando,
    }
    return {
        "periodo": {"inicio": inicio, "fim": fim},
        **{k: v for k, v in uso.items() if k != "documentos_faltando"},
        "envios_de_atas": dict(Counter(str(e.get("status")) for e in envios)),
        "envios": grupos,
        "pendencias": pendencias,
        "documentos_faltando": faltando,
        "prazos_da_semana": prazos,
        # Para a automação: o que a IA interpreta e o que o código já montou.
        "para_ia": json.dumps(uso, ensure_ascii=False),
        "html_fatos": html_fatos(grupos, pendencias, faltando, prazos),
    }
