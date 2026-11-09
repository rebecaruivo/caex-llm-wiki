"""Guarda e lê o histórico de chats e os jobs de ingestão no Supabase.

O Supabase transforma cada tabela do banco em um endereço HTTP (a API REST):
GET lê, POST cria, PATCH atualiza e DELETE apaga. As funções RPC são chamadas
em `/rest/v1/rpc/<funcao>`. Só este arquivo conhece a chave secreta, e só a API
chama estas funções. O navegador nunca fala direto com o banco.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from uuid import UUID


class ErroBanco(Exception):
    """Falha ao falar com o Supabase."""


class ChatNaoEncontrado(ErroBanco):
    """O chat não existe ou já foi apagado."""


class DuplicataIngest(ErroBanco):
    """O índice único parcial já tem um job bloqueante para este hash."""


class SourceIdEmUso(ErroBanco):
    """O source_id já existe; a API deve tentar um sufixo maior."""


def configurado():
    """Diz se as duas variáveis do Supabase estão no ambiente."""
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY"))


def _id(chat_id):
    """Aceita só um UUID válido, para ninguém injetar filtros na consulta."""
    return str(UUID(str(chat_id)))


def _base_e_cabecalhos():
    base = os.environ.get("SUPABASE_URL", "").rstrip("/").removesuffix("/rest/v1")
    chave = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not base or not chave:
        raise ErroBanco("SUPABASE_URL ou SUPABASE_SECRET_KEY não configurada.")
    cabecalhos = {"apikey": chave, "Content-Type": "application/json"}
    if not chave.startswith("sb_"):
        # A chave antiga (service_role) é um token JWT e vai também neste cabeçalho.
        cabecalhos["Authorization"] = f"Bearer {chave}"
    return base, cabecalhos


def _abrir(pedido, timeout=10):
    """Abre o pedido HTTP. É o único ponto de rede, trocado nos testes."""
    return urllib.request.urlopen(pedido, timeout=timeout)


def _ler_resposta(resposta):
    texto = resposta.read().decode("utf-8")
    return texto, resposta.headers


def _pedir(metodo, tabela, filtros=None, corpo=None, devolver=False, prefer=None, contar=False):
    """Faz uma chamada à API REST do Supabase e devolve o JSON da resposta."""
    base, cabecalhos = _base_e_cabecalhos()
    url = f"{base}/rest/v1/{tabela}"
    if filtros:
        url += "?" + urllib.parse.urlencode(filtros)

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
        with _abrir(pedido) as resposta:
            texto, cabecalhos_resposta = _ler_resposta(resposta)
            if contar:
                # Formato: "0-0/123" (ou "*/0" quando não há linhas).
                return int(cabecalhos_resposta.get("Content-Range", "*/0").split("/")[-1])
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode("utf-8", "replace")[:500]
        if '"23503"' in detalhe:
            # Código do Postgres para "aponta para um chat que não existe".
            raise ChatNaoEncontrado("Chat não encontrado.") from erro
        raise ErroBanco(f"Supabase respondeu {erro.code}: {detalhe}") from erro
    except (urllib.error.URLError, TimeoutError) as erro:
        raise ErroBanco(f"Sem conexão com o Supabase: {erro}") from erro
    return json.loads(texto) if texto else None


def _rpc(funcao, corpo=None):
    """Chama uma função RPC do Postgres via `/rest/v1/rpc/<funcao>`."""
    base, cabecalhos = _base_e_cabecalhos()
    url = f"{base}/rest/v1/rpc/{funcao}"
    dados = json.dumps(corpo or {}, ensure_ascii=False).encode("utf-8")
    pedido = urllib.request.Request(url, data=dados, headers=cabecalhos, method="POST")
    try:
        with _abrir(pedido) as resposta:
            texto, _ = _ler_resposta(resposta)
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode("utf-8", "replace")[:500]
        raise ErroBanco(f"Supabase respondeu {erro.code}: {detalhe}") from erro
    except (urllib.error.URLError, TimeoutError) as erro:
        raise ErroBanco(f"Sem conexão com o Supabase: {erro}") from erro
    return json.loads(texto) if texto else None


def _valor_rpc(resposta):
    """Extrai um escalar das formas que o PostgREST devolve para RPCs."""
    if resposta is None:
        return None
    if isinstance(resposta, (int, float, bool)):
        return resposta
    if isinstance(resposta, list):
        if not resposta:
            return None
        resposta = resposta[0]
    if isinstance(resposta, dict):
        for chave in ("valor", "resultado", "contagem", "consumir_limite", "tomar_lock_fila"):
            if chave in resposta:
                return resposta[chave]
        if len(resposta) == 1:
            return next(iter(resposta.values()))
        return None
    return resposta


def _texto_erro(erro):
    return str(erro).lower()


def _booleano_rpc(resposta):
    valor = _valor_rpc(resposta)
    if isinstance(valor, str):
        return valor.strip().lower() == "true"
    return bool(valor)


# ---------------------------------------------------------------------------
# Histórico de chats (comportamento anterior preservado)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Ingestão: jobs, limites, lock e hashes
# ---------------------------------------------------------------------------

def _hash_valido(hash_):
    if not re.fullmatch(r"[0-9a-f]{64}", str(hash_)):
        raise ValueError("Hash de conteúdo inválido.")
    return str(hash_)


def _filtro_status(statuss):
    return "in.(" + ",".join(statuss) + ")"


def criar_job_ingest(dados):
    """Cria um job em `na_fila` e devolve a linha criada.

    O índice único parcial de `hash_conteudo` resolve a corrida entre dois
    envios: se ele disparar, levantamos `DuplicataIngest`; colisão de
    `source_id` levanta `SourceIdEmUso` para a API tentar um sufixo maior.
    """
    try:
        linhas = _pedir("POST", "ingest_jobs", corpo=dados, devolver=True)
    except ErroBanco as erro:
        texto = _texto_erro(erro)
        if "23505" in texto:
            if "hash_bloqueante" in texto or "hash_conteudo" in texto:
                raise DuplicataIngest("Já existe um envio bloqueante para este conteúdo.") from erro
            if "source_id" in texto:
                raise SourceIdEmUso("source_id já está em uso.") from erro
        raise
    return linhas[0] if linhas else None


def buscar_job_ingest(job_id):
    """Devolve o job completo pelo UUID, ou None."""
    linhas = _pedir("GET", "ingest_jobs", {
        "id": f"eq.{_id(job_id)}",
        "select": "*",
        "limit": "1",
    })
    return linhas[0] if linhas else None


def buscar_job_bloqueante_por_hash(hash_, statuss):
    """Primeiro job bloqueante com este hash, ou None."""
    linhas = _pedir("GET", "ingest_jobs", {
        "hash_conteudo": f"eq.{_hash_valido(hash_)}",
        "status": _filtro_status(statuss),
        "select": "id,source_id,status",
        "order": "criado_em.asc",
        "limit": "1",
    })
    return linhas[0] if linhas else None


def buscar_hash_ingest(hash_, resultados):
    """Resultado terminal guardado para o hash, ou None."""
    linhas = _pedir("GET", "ingest_hashes", {
        "hash_conteudo": f"eq.{_hash_valido(hash_)}",
        "resultado": _filtro_status(resultados),
        "select": "hash_conteudo,source_id,resultado",
        "limit": "1",
    })
    return linhas[0] if linhas else None


def guardar_hash_ingest(hash_, source_id, resultado):
    """Grava o último resultado terminal do hash, sem texto nem nomes."""
    _pedir("POST", "ingest_hashes", {"on_conflict": "hash_conteudo"}, corpo={
        "hash_conteudo": _hash_valido(hash_),
        "source_id": source_id,
        "resultado": resultado,
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, prefer="resolution=merge-duplicates")


def listar_source_ids_ingest():
    """Todos os `source_id` de jobs existentes, para evitar colisão."""
    linhas = _pedir("GET", "ingest_jobs", {"select": "source_id", "limit": "10000"})
    return [linha["source_id"] for linha in linhas or []]


def contar_jobs_na_fila_antes(criado_em):
    """Quantos jobs `na_fila` são mais antigos que o instante informado."""
    return _pedir("GET", "ingest_jobs", {
        "status": "eq.na_fila",
        "criado_em": f"lt.{criado_em}",
        "select": "id",
    }, contar=True)


def contar_jobs_por_status(statuss):
    """Conta jobs nos estados informados. Usado por métricas e manutenção."""
    return _pedir("GET", "ingest_jobs", {
        "status": _filtro_status(statuss),
        "select": "id",
    }, contar=True)


def job_mais_antigo_na_fila():
    """O job `na_fila` mais antigo, ou None."""
    linhas = _pedir("GET", "ingest_jobs", {
        "status": "eq.na_fila",
        "select": "id,source_id,status,falhas_dispatch,criado_em",
        "order": "criado_em.asc",
        "limit": "1",
    })
    return linhas[0] if linhas else None


def atualizar_job_ingest(job_id, campos):
    """Atualiza campos do job e devolve a linha nova."""
    linhas = _pedir("PATCH", "ingest_jobs", {"id": f"eq.{_id(job_id)}"}, corpo=campos, devolver=True)
    return linhas[0] if linhas else None


def registrar_evento_ingest(job_id, evento, detalhe=None):
    """Registra um evento curto, sem texto bruto, nomes ou IP."""
    _pedir("POST", "ingest_eventos", corpo={
        "job_id": _id(job_id),
        "evento": evento,
        "detalhe": detalhe or {},
    })


def tomar_lock_fila(job_id, intervalo_segundos):
    """Tenta o lock da fila; devolve True quando ele passa a ser do job."""
    resposta = _rpc("tomar_lock_fila", {
        "p_job_id": _id(job_id),
        "p_intervalo_segundos": int(intervalo_segundos),
    })
    return _booleano_rpc(resposta)


def liberar_lock_fila(job_id):
    """Libera o lock se ele ainda pertencer ao job."""
    _rpc("liberar_lock_fila", {"p_job_id": _id(job_id)})


def marcar_pr_aberto(job_id, pr_numero, pr_url):
    """Grava o PR aberto no lock compartilhado."""
    _rpc("marcar_pr_aberto", {
        "p_job_id": _id(job_id),
        "p_pr_numero": int(pr_numero),
        "p_pr_url": pr_url,
    })


def consumir_ip(chave, dia, limite, expira_em):
    """Consome uma unidade do limite por IP; None quando o limite acabou."""
    return _valor_rpc(_rpc("consumir_ip", {
        "p_chave": chave,
        "p_dia": dia,
        "p_limite": int(limite),
        "p_expira_em": expira_em,
    }))


def consumir_global(chave, dia, limite, expira_em):
    """Consome uma unidade do limite global; None quando o limite acabou."""
    return _valor_rpc(_rpc("consumir_global", {
        "p_chave": chave,
        "p_dia": dia,
        "p_limite": int(limite),
        "p_expira_em": expira_em,
    }))


def consumir_status(chave, dia, limite, expira_em):
    """Consome uma consulta de andamento (60 por minuto por IP)."""
    return _valor_rpc(_rpc("consumir_limite", {
        "p_chave": chave,
        "p_dia": dia,
        "p_limite": int(limite),
        "p_expira_em": expira_em,
    }))


def buscar_job_por_branch(branch):
    """O job cujo PR usa este branch, ou None."""
    linhas = _pedir("GET", "ingest_jobs", {
        "branch": f"eq.{branch}",
        "select": "*",
        "order": "criado_em.desc",
        "limit": "1",
    })
    return linhas[0] if linhas else None


def estado_fila():
    """A linha única do lock da fila, ou None."""
    linhas = _pedir("GET", "ingest_estado", {"id": "eq.1", "select": "*", "limit": "1"})
    return linhas[0] if linhas else None


def atualizar_estado_fila(campos):
    """Atualiza a linha única do lock e devolve a linha nova."""
    linhas = _pedir("PATCH", "ingest_estado", {"id": "eq.1"}, corpo=campos, devolver=True)
    return linhas[0] if linhas else None


def listar_jobs_expirados(agora_iso):
    """Jobs cujo TTL do original já venceu."""
    return _pedir("GET", "ingest_jobs", {
        "expira_em": f"lt.{agora_iso}",
        "select": "id,source_id,status,hash_conteudo,conteudo_bruto,nomes_detectados,expira_em",
    }) or []


def limpar_conteudo_job(job_id):
    """Apaga o original e os nomes de um job, mantendo a linha."""
    _pedir("PATCH", "ingest_jobs", {"id": f"eq.{_id(job_id)}"}, corpo={
        "conteudo_bruto": None,
        "nomes_detectados": None,
    })


def limpar_limites_expirados(agora_iso):
    """Apaga as linhas de limite cujo prazo já passou."""
    _pedir("DELETE", "limites_ingest", {"expira_em": f"lt.{agora_iso}"})


def listar_jobs_terminais_antigos(limite_iso, statuss):
    """Jobs em estado terminal parados desde antes de `limite_iso`."""
    return _pedir("GET", "ingest_jobs", {
        "status": _filtro_status(statuss),
        "atualizado_em": f"lt.{limite_iso}",
        "select": "id,source_id,status",
    }) or []


def apagar_job_ingest(job_id):
    """Apaga o job; os eventos caem junto por `on delete cascade`."""
    _pedir("DELETE", "ingest_jobs", {"id": f"eq.{_id(job_id)}"})


def listar_processando_parados(limite_iso):
    """Jobs `processando` sem atualização desde antes de `limite_iso`."""
    return _pedir("GET", "ingest_jobs", {
        "status": "eq.processando",
        "atualizado_em": f"lt.{limite_iso}",
        "select": "id,source_id,status,hash_conteudo,atualizado_em",
    }) or []
