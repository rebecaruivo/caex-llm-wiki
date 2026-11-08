"""Guarda e lê o histórico de chats no Supabase.

O Supabase transforma cada tabela do banco em um endereço HTTP (a API REST):
GET lê, POST cria e DELETE apaga. Só este arquivo conhece a chave secreta,
e só a API chama estas funções. O navegador nunca fala direto com o banco.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID


class ErroBanco(Exception):
    """Falha ao falar com o Supabase."""


class ChatNaoEncontrado(ErroBanco):
    """O chat não existe ou já foi apagado."""


def configurado():
    """Diz se as duas variáveis do Supabase estão no ambiente."""
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY"))


def _id(chat_id):
    """Aceita só um UUID válido, para ninguém injetar filtros na consulta."""
    return str(UUID(str(chat_id)))


def _pedir(metodo, tabela, filtros=None, corpo=None, devolver=False, prefer=None, contar=False):
    """Faz uma chamada à API REST do Supabase e devolve o JSON da resposta."""
    base = os.environ.get("SUPABASE_URL", "").rstrip("/").removesuffix("/rest/v1")
    chave = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not base or not chave:
        raise ErroBanco("SUPABASE_URL ou SUPABASE_SECRET_KEY não configurada.")

    url = f"{base}/rest/v1/{tabela}"
    if filtros:
        url += "?" + urllib.parse.urlencode(filtros)

    cabecalhos = {"apikey": chave, "Content-Type": "application/json"}
    if not chave.startswith("sb_"):
        # A chave antiga (service_role) é um token JWT e vai também neste cabeçalho.
        cabecalhos["Authorization"] = f"Bearer {chave}"
    preferencias = []
    if devolver:
        # Pede ao Supabase para devolver as linhas criadas ou apagadas.
        preferencias.append("return=representation")
    if prefer:
        preferencias.append(prefer)
    if contar:
        # Pede o total de linhas no cabeçalho Content-Range, sem trazer os dados.
        preferencias.append("count=exact")
        cabecalhos["Range"] = "0-0"
    if preferencias:
        cabecalhos["Prefer"] = ",".join(preferencias)

    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8") if corpo is not None else None
    pedido = urllib.request.Request(url, data=dados, headers=cabecalhos, method=metodo)
    try:
        with urllib.request.urlopen(pedido, timeout=10) as resposta:
            texto = resposta.read().decode("utf-8")
            if contar:
                # Formato: "0-0/123" (ou "*/0" quando não há linhas).
                return int(resposta.headers.get("Content-Range", "*/0").split("/")[-1])
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode("utf-8", "replace")[:300]
        if '"23503"' in detalhe:
            # Código do Postgres para "aponta para um chat que não existe".
            raise ChatNaoEncontrado("Chat não encontrado.") from erro
        raise ErroBanco(f"Supabase respondeu {erro.code}: {detalhe}") from erro
    except (urllib.error.URLError, TimeoutError) as erro:
        raise ErroBanco(f"Sem conexão com o Supabase: {erro}") from erro
    return json.loads(texto) if texto else None


def listar_chats(limite=50):
    """Lista os chats do CA, do mais recente para o mais antigo."""
    linhas = _pedir("GET", "chats", {
        "select": "id,titulo,criado_em,atualizado_em,mensagens(count)",
        "order": "atualizado_em.desc",
        "limit": str(limite),
    })
    return [
        {
            "id": linha["id"],
            "titulo": linha["titulo"],
            "criado_em": linha["criado_em"],
            "atualizado_em": linha["atualizado_em"],
            "perguntas": linha["mensagens"][0]["count"] if linha["mensagens"] else 0,
        }
        for linha in linhas
    ]


def criar_chat(titulo):
    """Cria um chat novo. O título é a primeira pergunta."""
    return _pedir("POST", "chats", corpo={"titulo": titulo[:500]}, devolver=True)[0]


def abrir_chat(chat_id):
    """Devolve um chat com todas as mensagens, em ordem."""
    linhas = _pedir("GET", "chats", {
        "id": f"eq.{_id(chat_id)}",
        "select": "id,titulo,criado_em,atualizado_em,mensagens(id,pergunta,pergunta_interpretada,resposta,fontes,citacoes_invalidas,do_cache,criado_em)",
        "mensagens.order": "criado_em.asc",
    })
    if not linhas:
        raise ChatNaoEncontrado("Chat não encontrado.")
    return linhas[0]


def salvar_mensagem(chat_id, pergunta, resultado, pergunta_interpretada=None, do_cache=False):
    """Guarda uma pergunta e a resposta do oráculo dentro de um chat.

    pergunta_interpretada: a versão completa que o oráculo respondeu, quando
    a pergunta digitada dependia das anteriores ("E quem organiza?").
    """
    return _pedir("POST", "mensagens", corpo={
        "chat_id": _id(chat_id),
        "pergunta": pergunta,
        "pergunta_interpretada": pergunta_interpretada,
        "resposta": resultado["resposta"],
        "fontes": resultado["fontes"],
        "citacoes_invalidas": resultado["citacoes_invalidas"],
        "do_cache": do_cache,
    }, devolver=True)[0]


def apagar_chat(chat_id):
    """Apaga um chat. As mensagens dele somem junto (on delete cascade)."""
    apagados = _pedir("DELETE", "chats", {"id": f"eq.{_id(chat_id)}"}, devolver=True)
    if not apagados:
        raise ChatNaoEncontrado("Chat não encontrado.")


def _chave_valida(chave):
    """A chave do cache é um hash SHA-256: 64 caracteres hexadecimais."""
    if not re.fullmatch(r"[0-9a-f]{64}", chave):
        raise ValueError("Chave de cache inválida.")
    return chave


def buscar_cache(chave):
    """Devolve a resposta guardada para esta chave, ou None."""
    linhas = _pedir("GET", "respostas_cache", {"chave": f"eq.{_chave_valida(chave)}", "select": "resposta,fontes", "limit": "1"})
    return linhas[0] if linhas else None


def guardar_cache(chave, pergunta, versao, modelo, resultado):
    """Guarda uma resposta. Se duas pessoas guardarem ao mesmo tempo, a primeira fica."""
    _pedir("POST", "respostas_cache", {"on_conflict": "chave"}, corpo={
        "chave": _chave_valida(chave),
        "pergunta": pergunta,
        "versao": versao,
        "modelo": modelo,
        "resposta": resultado["resposta"],
        "fontes": resultado["fontes"],
    }, prefer="resolution=ignore-duplicates")


def apagar_cache(chave):
    """Tira uma resposta do cache, por exemplo depois de "Informar erro"."""
    _pedir("DELETE", "respostas_cache", {"chave": f"eq.{_chave_valida(chave)}"})


def metricas():
    """Quantas perguntas foram feitas e quantas vieram do cache."""
    total = _pedir("GET", "mensagens", {"select": "id"}, contar=True)
    reaproveitadas = _pedir("GET", "mensagens", {"select": "id", "do_cache": "eq.true"}, contar=True)
    return {"perguntas": total, "reaproveitadas": reaproveitadas}
