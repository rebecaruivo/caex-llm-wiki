"""API do oráculo do CAEX. Todos os dados são fictícios."""
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config_ingestao
import db
import ingerir
from tools.lint_wiki import revisar
from oraculo import (
    MODELO_PADRAO, ErroApi, carregar_env, carregar_prompt, chave_cache, montar_wiki,
    reescrever, responder, resumo_wiki, versao_contexto,
)

carregar_env()

RAIZ = Path(__file__).resolve().parent
REPOSITORIO = os.environ.get("REPOSITORIO_URL", "https://github.com/rebecaruivo/caex-llm-wiki/blob/main")

app = FastAPI(
    title="Oráculo do CAEX",
    description="Responde perguntas usando só a wiki. Demo com dados fictícios.",
    version="0.1.0",
)


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=3, max_length=500, examples=["Quando será a Semana de Integração?"])
    chat_id: UUID | None = Field(default=None, description="Vazio cria um chat novo.")


# Exemplos que aparecem na página /docs, num menu para escolher.
EXEMPLOS_PERGUNTA = {
    "nova": {
        "summary": "Nova conversa (sem chat_id)",
        "value": {"pergunta": "Quando será a Semana de Integração?"},
    },
    "continuar": {
        "summary": "Continuar um chat (cole o chat_id)",
        "value": {"pergunta": "E quem organiza?", "chat_id": "cole-aqui-o-chat_id"},
    },
}


class Registro(BaseModel):
    tipo: Literal["lacuna", "erro"]
    pergunta: str = Field(min_length=3, max_length=500)
    resposta: str = Field(default="", max_length=4000)
    pergunta_interpretada: str | None = Field(default=None, max_length=500)


class Resposta(BaseModel):
    resposta: str
    fontes: list[str]
    citacoes_invalidas: list[str]
    chat_id: str | None = None
    salvo: bool = False
    pergunta_interpretada: str | None = None
    do_cache: bool = False


NAO_ENCONTRADO = "Este chat não existe mais. Alguém do CA pode ter apagado."


def exigir_banco():
    """Interrompe a rota com 503 se o Supabase não estiver configurado."""
    if not db.configurado():
        raise HTTPException(status_code=503, detail="O histórico ainda não está configurado.")


app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")


@app.get("/", include_in_schema=False)
def interface():
    """Entrega a página do oráculo, no mesmo domínio da API."""
    return FileResponse(RAIZ / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/revisar", include_in_schema=False)
def pagina_revisao():
    """Entrega a página de revisão da wiki (lint)."""
    return FileResponse(RAIZ / "static" / "revisar.html", headers={"Cache-Control": "no-cache"})


@app.get("/enviar", include_in_schema=False)
def pagina_enviar():
    """Entrega o formulário público de envio de fontes."""
    return FileResponse(RAIZ / "static" / "enviar.html", headers={"Cache-Control": "no-cache"})


@app.get("/enviar/{job_id}", include_in_schema=False)
def pagina_andamento(job_id: str):
    """Entrega a página de andamento; o UUID é lido do caminho pelo navegador."""
    return FileResponse(RAIZ / "static" / "enviar.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/saude")
def saude():
    """Confirma que a API está no ar, sem chamar o Gemini."""
    return {"status": "ok"}


@app.get("/api/wiki")
def wiki():
    """Descreve as páginas, o que falta e as fontes, lendo os arquivos da wiki."""
    dados = resumo_wiki()
    for fonte in dados["fontes"]:
        fonte["url"] = f"{REPOSITORIO}/{fonte['arquivo']}"
    return dados


def publico(problema):
    """Prepara um problema do lint para a página pública, sem expor e-mails."""
    detalhe = problema["detalhe"]
    if problema["problema"].startswith("E-mail"):
        detalhe = "Endereço oculto nesta página. Veja no terminal com tools/lint_wiki.py."
    return {chave: problema[chave] for chave in ("tipo", "pagina", "problema", "acao")} | {"detalhe": detalhe}


@app.get("/api/metricas")
def ver_metricas():
    """Quantas perguntas o CA fez e quantas foram respondidas sem chamar o modelo."""
    exigir_banco()
    try:
        return db.metricas()
    except db.ErroBanco as erro:
        print(f"Falha ao ler métricas: {erro}", flush=True)
        raise HTTPException(status_code=503, detail="Não foi possível ler as métricas agora.") from erro


@app.get("/api/lint")
def lint():
    """Roda a revisão automática da wiki. Não usa IA e não altera nada."""
    resultado = revisar(RAIZ)
    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "paginas": resultado["paginas"],
        "testes": resultado["testes"],
        "erros": [publico(p) for p in resultado["erros"]],
        "avisos": [publico(p) for p in resultado["avisos"]],
    }


@app.post("/api/perguntar", response_model=Resposta)
def perguntar(dados: Annotated[Pergunta, Body(openapi_examples=EXEMPLOS_PERGUNTA)]):
    """Recebe uma pergunta e devolve a resposta do oráculo com as fontes citadas."""
    pergunta = dados.pergunta.strip()
    if len(pergunta) < 3:
        raise HTTPException(status_code=422, detail="Escreva uma pergunta com pelo menos 3 caracteres.")

    chave = os.environ.get("GEMINI_API_KEY", "")
    if not chave or chave == "cole_sua_chave_aqui":
        raise HTTPException(status_code=503, detail="Oráculo indisponível: a chave do modelo não está configurada.")

    chat_id = str(dados.chat_id) if dados.chat_id else None
    anteriores = []
    if chat_id and db.configurado():
        # Confere antes de gastar uma chamada ao modelo e pega as perguntas anteriores.
        try:
            chat = db.abrir_chat(chat_id)
            anteriores = [m.get("pergunta_interpretada") or m["pergunta"] for m in chat.get("mensagens", [])]
        except db.ChatNaoEncontrado as erro:
            raise HTTPException(status_code=404, detail=NAO_ENCONTRADO) from erro
        except db.ErroBanco as erro:
            print(f"Falha ao abrir chat: {erro}", flush=True)

    modelo = os.environ.get("GEMINI_MODEL", MODELO_PADRAO)

    # Passo 1: completa perguntas que dependem das anteriores. Se falhar, segue com a original.
    interpretada = pergunta
    if anteriores:
        try:
            interpretada = reescrever(pergunta, anteriores, chave, modelo, tentativas=2)
        except ErroApi as erro:
            print(f"Falha ao reescrever a pergunta: {erro}", flush=True)

    # Cache: mesma pergunta completa + mesma wiki + mesmo modelo = mesma resposta.
    versao = versao_contexto(carregar_prompt(), montar_wiki())
    chave_resposta = chave_cache(interpretada, versao, modelo)
    guardada = None
    if db.configurado():
        try:
            guardada = db.buscar_cache(chave_resposta)
        except db.ErroBanco as erro:
            print(f"Falha ao ler o cache: {erro}", flush=True)

    if guardada:
        resultado = {"resposta": guardada["resposta"], "fontes": guardada["fontes"], "citacoes_invalidas": []}
    else:
        # Passo 2: o oráculo de sempre, avaliado pelos evals, responde a pergunta completa.
        try:
            resultado = responder(interpretada, chave, modelo, tentativas=2)
        except ErroApi as erro:
            print(f"Falha ao consultar o modelo: {erro}", flush=True)
            raise HTTPException(status_code=502, detail="Não foi possível consultar o modelo agora. Tente novamente.") from erro
        # Só entra no cache resposta sem citação inválida.
        if db.configurado() and not resultado["citacoes_invalidas"]:
            try:
                db.guardar_cache(chave_resposta, interpretada, versao, modelo, resultado)
            except db.ErroBanco as erro:
                print(f"Falha ao guardar no cache: {erro}", flush=True)
    resultado["do_cache"] = bool(guardada)

    # Salvar é um extra: se o banco falhar, a pessoa recebe a resposta mesmo assim.
    resultado["salvo"] = False
    if db.configurado():
        try:
            if not chat_id:
                chat_id = db.criar_chat(pergunta)["id"]
            db.salvar_mensagem(chat_id, pergunta, resultado, interpretada if interpretada != pergunta else None, bool(guardada))
            resultado["salvo"] = True
        except db.ErroBanco as erro:
            print(f"Falha ao salvar no histórico: {erro}", flush=True)
    resultado["chat_id"] = chat_id
    resultado["pergunta_interpretada"] = interpretada if interpretada != pergunta else None
    return resultado


@app.get("/api/chats")
def listar_chats():
    """Lista o histórico compartilhado do CA, do chat mais recente ao mais antigo."""
    exigir_banco()
    try:
        return db.listar_chats()
    except db.ErroBanco as erro:
        print(f"Falha ao listar chats: {erro}", flush=True)
        raise HTTPException(status_code=503, detail="Não foi possível carregar o histórico agora.") from erro


@app.get("/api/chats/{chat_id}")
def abrir_chat(chat_id: UUID):
    """Devolve um chat com todas as perguntas e respostas."""
    exigir_banco()
    try:
        return db.abrir_chat(chat_id)
    except db.ChatNaoEncontrado as erro:
        raise HTTPException(status_code=404, detail=NAO_ENCONTRADO) from erro
    except db.ErroBanco as erro:
        print(f"Falha ao abrir chat: {erro}", flush=True)
        raise HTTPException(status_code=503, detail="Não foi possível abrir o chat agora.") from erro


@app.delete("/api/chats/{chat_id}", status_code=204)
def apagar_chat(chat_id: UUID):
    """Apaga um chat para todo o CA. Não dá para desfazer."""
    exigir_banco()
    try:
        db.apagar_chat(chat_id)
    except db.ChatNaoEncontrado as erro:
        raise HTTPException(status_code=404, detail=NAO_ENCONTRADO) from erro
    except db.ErroBanco as erro:
        print(f"Falha ao apagar chat: {erro}", flush=True)
        raise HTTPException(status_code=503, detail="Não foi possível apagar o chat agora.") from erro
    return Response(status_code=204)


@app.post("/api/registros", status_code=201)
def registrar(dados: Registro):
    """Registra uma lacuna ou um erro para a gestão revisar."""
    if dados.tipo == "erro" and db.configurado():
        # Uma resposta com erro informado não deve ser reaproveitada.
        modelo = os.environ.get("GEMINI_MODEL", MODELO_PADRAO)
        versao = versao_contexto(carregar_prompt(), montar_wiki())
        try:
            db.apagar_cache(chave_cache(dados.pergunta_interpretada or dados.pergunta, versao, modelo))
        except db.ErroBanco as erro:
            print(f"Falha ao tirar do cache: {erro}", flush=True)
    item = {
        "tipo": dados.tipo,
        "pergunta": dados.pergunta.strip(),
        "resposta": dados.resposta.strip(),
        "registrado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    webhook = os.environ.get("REGISTROS_WEBHOOK_URL", "")
    arquivo = os.environ.get("REGISTROS_ARQUIVO", "")

    if webhook:
        pedido = urllib.request.Request(
            webhook,
            data=json.dumps(item, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(pedido, timeout=10):
                pass
        except (urllib.error.URLError, TimeoutError) as erro:
            print(f"Falha ao enviar registro: {erro}", flush=True)
            raise HTTPException(status_code=502, detail="Não foi possível registrar agora.") from erro
    elif arquivo:
        destino = (RAIZ / arquivo).resolve()
        if RAIZ not in destino.parents:
            raise HTTPException(status_code=500, detail="Destino de registro inválido.")
        with destino.open("a", encoding="utf-8") as saida:
            saida.write(json.dumps(item, ensure_ascii=False) + "\n")
    else:
        raise HTTPException(status_code=503, detail="O registro ainda não está configurado.")

    return {"status": "registrado"}


# ---------------------------------------------------------------------------
# Ingestão: configuração, envio, andamento e avanço da fila
# ---------------------------------------------------------------------------

ROTULOS_TIPO = {"ata": "Ata", "guia": "Guia", "outro": "Relato"}
MSG_ENVIO_FECHADO = "O envio está fechado para testes."
MSG_ENVIO_NAO_CONFIGURADO = "O envio ainda não está configurado."
MSG_NAO_DISPONIVEL = "Este envio não está mais disponível."
MSG_PII = "Remova dados pessoais (CPF, RA, telefone ou e-mail pessoal) e envie de novo."
MSG_TURNSTILE = "Não foi possível verificar o navegador. Tente novamente."
MSG_LIMITE_STATUS = "Muitas consultas. Tente novamente em um minuto."
MENSAGENS_ESTADO = {
    "aguardando_revisao": "Proposta enviada para revisão da gestão.",
    "publicado": "Publicado após revisão da gestão.",
    "fechado_sem_merge": "A gestão encerrou a revisão sem publicar.",
    "sem_mudancas": "Nenhum fato novo encontrado. Nada foi publicado.",
    "quarentena": "A fonte foi enviada para revisão da gestão. Nenhuma página foi alterada.",
    "falhou": "Não foi possível concluir. A gestão foi avisada.",
    "falhou_pii": "Remova nomes de pessoas e reenvie.",
    "expirado": "Este envio expirou. Envie a fonte novamente.",
}
ETAPAS = {
    "triagem": "triagem",
    "compilando": "compilando a proposta",
    "validando": "validando a proposta",
    "abrindo_pr": "abrindo o Pull Request",
}


class ErroDispatch(Exception):
    """Falha curta ao disparar o worker; nunca guarda o token."""


def _agora():
    """Relógio UTC injetável nos testes."""
    return datetime.now(timezone.utc)


def _iso(momento):
    return momento.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dia_fuso(momento):
    return momento.astimezone(ZoneInfo(config_ingestao.FUSO)).date()


def _expira(momento, dias=0, minutos=0):
    return _iso(momento + timedelta(days=dias, minutes=minutos))


def _hmac_ip(ip):
    salt = os.environ.get("INGEST_SALT", "")
    return hmac.new(salt.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()


def _ip_da_requisicao(request):
    """Primeiro valor de x-forwarded-for; na Vercel ele é obrigatório."""
    cabecalho = request.headers.get("x-forwarded-for") if request is not None else None
    if cabecalho:
        return cabecalho.split(",")[0].strip()
    if os.environ.get("VERCEL"):
        raise HTTPException(status_code=400, detail="Requisição inválida.")
    if request is not None and getattr(request, "client", None):
        return request.client.host
    return "127.0.0.1"


def _chave_ip(ip, dia):
    return f"ip:{_hmac_ip(ip)}:{dia.isoformat()}"


def _chave_global(dia):
    return f"global:{dia.isoformat()}"


def _chave_status(ip, momento):
    local = momento.astimezone(ZoneInfo(config_ingestao.FUSO))
    return f"status:{_hmac_ip(ip)}:{local.strftime('%Y-%m-%d-%H-%M')}"


def verificar_turnstile(token, ip, secret=None):
    """Verifica o token do Turnstile. Pode ser substituída nos testes."""
    secret = secret or os.environ.get("TURNSTILE_SECRET_KEY", "")
    if not token or not secret:
        return False
    corpo = urllib.parse.urlencode({
        "secret": secret,
        "response": token,
        "remoteip": ip,
    }).encode("utf-8")
    pedido = urllib.request.Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=corpo,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=10) as resposta:
            return bool(json.loads(resposta.read().decode("utf-8")).get("success"))
    except Exception:
        # Nunca expõe token, segredo ou detalhe da Cloudflare ao cliente.
        return False


def _repositorio_github():
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = os.environ.get("REPOSITORIO_URL", "https://github.com/rebecaruivo/caex-llm-wiki/blob/main")
    partes = [parte for parte in urllib.parse.urlparse(url).path.split("/") if parte]
    if len(partes) >= 2:
        return "/".join(partes[:2])
    return "rebecaruivo/caex-llm-wiki"


def disparar_worker(job_id):
    """Dispara `ingerir.yml` via GitHub Actions. Sem token, falha curta."""
    token = os.environ.get("GITHUB_DISPATCH_TOKEN", "")
    if not token:
        raise ErroDispatch("token de dispatch ausente")
    corpo = json.dumps({"ref": "main", "inputs": {"job_id": str(job_id)}}).encode("utf-8")
    pedido = urllib.request.Request(
        f"https://api.github.com/repos/{_repositorio_github()}/actions/workflows/ingerir.yml/dispatches",
        data=corpo,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=10):
            return True
    except urllib.error.HTTPError as erro:
        raise ErroDispatch(f"GitHub respondeu {erro.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise ErroDispatch("sem conexão com o GitHub") from None


def _duplicata_manifesto(hash_):
    """`source_id` de uma fonte de ingestão já publicada, se houver."""
    try:
        manifesto = ingerir.carregar_manifesto(RAIZ)
    except (OSError, ValueError, KeyError):
        return None
    for fonte in manifesto.get("fontes", []):
        if fonte.get("origem") == "envio" and fonte.get("hash") == hash_:
            return fonte.get("id")
    return None


def _duplicata(hash_):
    """`source_id` do primeiro registro bloqueante para o hash, ou None."""
    statuss = config_ingestao.STATUS_BLOQUEANTES
    try:
        job = db.buscar_job_bloqueante_por_hash(hash_, statuss)
        if job:
            return job.get("source_id")
        guardado = db.buscar_hash_ingest(hash_, statuss)
        if guardado:
            return guardado.get("source_id")
    except db.ErroBanco as erro:
        print(f"Falha ao conferir duplicata: {erro}", flush=True)
        raise
    return _duplicata_manifesto(hash_)


def _posicao_fila(job):
    if job.get("status") != "na_fila":
        return None
    try:
        return db.contar_jobs_na_fila_antes(job["criado_em"]) + 1
    except (db.ErroBanco, KeyError):
        return 1


def _mensagem_estado(job, posicao):
    status = job.get("status")
    if status == "na_fila":
        return f"Na fila. Posição {posicao}. A espera depende da revisão da gestão."
    if status == "processando":
        etapa = ETAPAS.get(job.get("etapa"), job.get("etapa") or "processando")
        return f"Processando: {etapa}."
    if status == "falhou" and job.get("motivo") == "pii":
        return MENSAGENS_ESTADO["falhou_pii"]
    return MENSAGENS_ESTADO.get(status, "Acompanhe a revisão da gestão.")


def _job_publico(job, posicao):
    """Allowlist explícita: nunca devolve texto bruto, hash, nome ou título."""
    return {
        "id": str(job["id"]),
        "source_id": job.get("source_id"),
        "status": job.get("status"),
        "motivo": job.get("motivo"),
        "etapa": job.get("etapa"),
        "posicao_fila": posicao,
        "pr_url": job.get("pr_url"),
        "mensagem": _mensagem_estado(job, posicao),
        "criado_em": job.get("criado_em"),
        "atualizado_em": job.get("atualizado_em"),
    }


def _fonte_completa(ids_manifesto):
    """IDs existentes no manifesto, sem quebrar quando ele não está disponível."""
    return set(ids_manifesto or [])


def _criar_job_na_fila(tipo, titulo, data_documento, texto, hash_):
    """Gera `source_id`, cria o job e resolve colisão/ corrida de hash."""
    existentes = _fonte_completa(_ids_manifesto_seguro())
    try:
        existentes.update(db.listar_source_ids_ingest())
    except db.ErroBanco as erro:
        print(f"Falha ao listar source_ids: {erro}", flush=True)
        raise
    momento = _agora()
    expira_em = _iso(momento + timedelta(days=config_ingestao.RETENCAO_BRUTO_DIAS))
    for _ in range(4):
        source_id = ingerir.gerar_source_id(tipo, data_documento, existentes, texto, sufixo_sempre=True)
        corpo = {
            "source_id": source_id,
            "status": "na_fila",
            "tipo": tipo,
            "origem": "web",
            "titulo": titulo,
            "data_documento": data_documento,
            "conteudo_bruto": texto,
            "hash_conteudo": hash_,
            "expira_em": expira_em,
        }
        try:
            return db.criar_job_ingest(corpo)
        except db.SourceIdEmUso:
            existentes.add(source_id)
            continue
        except db.DuplicataIngest as erro:
            duplicado = None
            try:
                duplicado = _duplicata(hash_)
            except db.ErroBanco:
                pass
            raise HTTPException(
                status_code=409,
                detail=f"Esta fonte já foi registrada como {duplicado or 'fonte existente'}.",
            ) from erro
    raise HTTPException(status_code=409, detail="Não foi possível gerar um identificador único.")


def _ids_manifesto_seguro():
    try:
        return ingerir.ids_manifesto(RAIZ).keys()
    except (OSError, ValueError, KeyError):
        return ()


def _validar_envio(dados):
    """Valida tamanho, tipo, título, data e texto, nesta ordem."""
    tipo = dados.get("tipo") if isinstance(dados.get("tipo"), str) else ""
    titulo = dados.get("titulo").strip() if isinstance(dados.get("titulo"), str) else ""
    data_documento = dados.get("data_documento").strip() if isinstance(dados.get("data_documento"), str) else ""
    texto = dados.get("texto") if isinstance(dados.get("texto"), str) else ""

    if len(texto.encode("utf-8")) > config_ingestao.MAX_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"O texto passa do limite de {config_ingestao.MAX_BYTES} bytes.",
        )
    if tipo not in config_ingestao.TIPOS:
        raise HTTPException(status_code=422, detail="Tipo de documento inválido.")
    if not (3 <= len(titulo) <= 200):
        raise HTTPException(status_code=422, detail="O título precisa ter entre 3 e 200 caracteres.")
    try:
        date.fromisoformat(data_documento)
    except ValueError as erro:
        raise HTTPException(
            status_code=422, detail="Data do documento inválida. Use AAAA-MM-DD."
        ) from erro
    if not texto.strip():
        raise HTTPException(status_code=422, detail="Envie o texto da fonte.")
    return tipo, titulo, data_documento, texto


def _processar_envio(dados, request):
    """Executa a ordem obrigatória do POST /api/enviar."""
    dados = dados if isinstance(dados, dict) else {}

    # 1. Abertura e chaves.
    site_key = os.environ.get("TURNSTILE_SITE_KEY", "")
    secret_key = os.environ.get("TURNSTILE_SECRET_KEY", "")
    if not config_ingestao.INGEST_ABERTO or not site_key or not secret_key:
        raise HTTPException(status_code=503, detail=MSG_ENVIO_FECHADO)

    # 2. Tamanho, tipo, título, data e texto.
    tipo, titulo, data_documento, texto = _validar_envio(dados)

    if not db.configurado():
        raise HTTPException(status_code=503, detail=MSG_ENVIO_NAO_CONFIGURADO)

    ip = _ip_da_requisicao(request)
    momento = _agora()
    dia = _dia_fuso(momento)

    # 3. Limite por IP (consumido mesmo que o resto rejeite).
    try:
        restante_ip = db.consumir_ip(
            _chave_ip(ip, dia), dia, config_ingestao.INGEST_MAX_POR_IP, _expira(momento, dias=1)
        )
    except db.ErroBanco as erro:
        print(f"Falha ao consumir limite de IP: {erro}", flush=True)
        raise HTTPException(status_code=503, detail="Não foi possível registrar o envio agora.") from erro
    if restante_ip is None:
        raise HTTPException(
            status_code=429,
            detail=f"Limite de {config_ingestao.INGEST_MAX_POR_IP} envios por dia. Tente novamente amanhã.",
        )

    # 4. Turnstile.
    token = dados.get("turnstile_token") if isinstance(dados.get("turnstile_token"), str) else ""
    if not verificar_turnstile(token, ip):
        raise HTTPException(status_code=403, detail=MSG_TURNSTILE)

    # 5. PII óbvia no título e no texto.
    if ingerir.detectar_pii(f"{titulo}\n{texto}"):
        raise HTTPException(status_code=422, detail=MSG_PII)

    hash_ = ingerir.hash_conteudo(texto)

    # 6. Duplicata (jobs, hashes e manifesto).
    try:
        duplicado = _duplicata(hash_)
    except db.ErroBanco as erro:
        print(f"Falha ao conferir duplicata: {erro}", flush=True)
        raise HTTPException(status_code=503, detail="Não foi possível conferir o envio agora.") from erro
    if duplicado:
        raise HTTPException(
            status_code=409, detail=f"Esta fonte já foi registrada como {duplicado}."
        )

    # 7. Limite global (só chega aqui quem passou por todas as rejeições).
    try:
        restante_global = db.consumir_global(
            _chave_global(dia), dia, config_ingestao.INGEST_MAX_DIA, _expira(momento, dias=1)
        )
    except db.ErroBanco as erro:
        print(f"Falha ao consumir limite global: {erro}", flush=True)
        raise HTTPException(status_code=503, detail="Não foi possível registrar o envio agora.") from erro
    if restante_global is None:
        raise HTTPException(status_code=429, detail="A fila de envios está cheia hoje. Tente novamente amanhã.")

    # 8. Criação do job em na_fila.
    try:
        job = _criar_job_na_fila(tipo, titulo, data_documento, texto, hash_)
    except db.ErroBanco as erro:
        print(f"Falha ao criar job de ingestão: {erro}", flush=True)
        raise HTTPException(status_code=503, detail="Não foi possível registrar o envio agora.") from erro
    if not job:
        raise HTTPException(status_code=503, detail="Não foi possível registrar o envio agora.")

    posicao = _posicao_fila(job)

    # 9. Avanço idempotente da fila; falha de dispatch não desfaz o envio.
    try:
        talvez_avancar_fila()
    except Exception as erro:  # noqa: BLE001 - nunca derruba um envio aceito
        print(f"Falha ao avançar a fila: {type(erro).__name__}", flush=True)

    return {
        "id": str(job["id"]),
        "source_id": job["source_id"],
        "status_url": f"/enviar/{job['id']}",
        "posicao_fila": posicao,
    }


@app.get("/api/enviar/config")
def config_enviar():
    """Estado público do formulário de envio."""
    return {
        "aberto": bool(config_ingestao.INGEST_ABERTO),
        "turnstile_site_key": os.environ.get("TURNSTILE_SITE_KEY", ""),
        "max_bytes": config_ingestao.MAX_BYTES,
        "tipos": [
            {"valor": valor, "rotulo": ROTULOS_TIPO[valor]}
            for valor in config_ingestao.TIPOS
        ],
    }


@app.post("/api/enviar", status_code=201)
def enviar(request: Request, dados: dict = Body(default={})):
    """Recebe uma fonte e cria o job em `na_fila`, na ordem da especificação."""
    return _processar_envio(dados, request)


@app.get("/api/ingest/{job_id}")
def andamento(job_id: str, request: Request):
    """Andamento público do envio, com allowlist de campos e limite por IP."""
    try:
        UUID(str(job_id))
    except (ValueError, TypeError, AttributeError) as erro:
        raise HTTPException(status_code=404, detail=MSG_NAO_DISPONIVEL) from erro

    if not db.configurado():
        raise HTTPException(status_code=503, detail=MSG_ENVIO_NAO_CONFIGURADO)

    ip = _ip_da_requisicao(request)
    momento = _agora()
    dia = _dia_fuso(momento)
    try:
        permitido = db.consumir_status(
            _chave_status(ip, momento),
            dia,
            config_ingestao.STATUS_RATE_LIMIT_POR_MINUTO,
            _expira(momento, minutos=1),
        )
    except db.ErroBanco as erro:
        print(f"Falha ao consumir limite de status: {erro}", flush=True)
        raise HTTPException(status_code=503, detail="Não foi possível consultar o envio agora.") from erro
    if permitido is None:
        raise HTTPException(status_code=429, detail=MSG_LIMITE_STATUS)

    try:
        job = db.buscar_job_ingest(job_id)
    except db.ErroBanco as erro:
        print(f"Falha ao buscar job de ingestão: {erro}", flush=True)
        raise HTTPException(status_code=503, detail="Não foi possível consultar o envio agora.") from erro
    if not job:
        raise HTTPException(status_code=404, detail=MSG_NAO_DISPONIVEL)

    posicao = _posicao_fila(job)
    resposta = _job_publico(job, posicao)

    try:
        talvez_avancar_fila()
    except Exception as erro:  # noqa: BLE001 - consulta não falha por dispatch
        print(f"Falha ao avançar a fila: {type(erro).__name__}", flush=True)

    return resposta


def talvez_avancar_fila(*, disparar=None, agora=None):
    """Toma o lock e dispara o worker; após o limite, marca `falhou`/`dispatch`."""
    if not db.configurado():
        return {"disparado": False, "motivo": "banco_nao_configurado"}
    try:
        job = db.job_mais_antigo_na_fila()
    except db.ErroBanco as erro:
        print(f"Falha ao ler a fila: {erro}", flush=True)
        return {"disparado": False, "motivo": "erro_banco"}
    if not job:
        return {"disparado": False, "motivo": "fila_vazia"}

    try:
        tomou = db.tomar_lock_fila(job["id"], config_ingestao.INTERVALO_DISPATCH_SEGUNDOS)
    except db.ErroBanco as erro:
        print(f"Falha ao tomar o lock: {erro}", flush=True)
        return {"disparado": False, "motivo": "erro_banco"}
    if not tomou:
        return {"disparado": False, "motivo": "lock"}

    momento = agora() if callable(agora) else (agora or _agora())
    try:
        (disparar or disparar_worker)(job["id"])
    except Exception:  # noqa: BLE001 - qualquer falha conta para o limite
        falhas = int(job.get("falhas_dispatch") or 0) + 1
        campos = {
            "falhas_dispatch": falhas,
            "ultima_tentativa": _iso(momento),
            "atualizado_em": _iso(momento),
        }
        if falhas >= config_ingestao.MAX_FALHAS_DISPATCH:
            campos.update({
                "status": "falhou",
                "motivo": "dispatch",
                "etapa": None,
                "fechado_em": _iso(momento),
            })
        try:
            db.atualizar_job_ingest(job["id"], campos)
            db.registrar_evento_ingest(job["id"], "dispatch_falhou", {"falhas": falhas})
            if falhas >= config_ingestao.MAX_FALHAS_DISPATCH:
                db.liberar_lock_fila(job["id"])
        except db.ErroBanco as erro:
            print(f"Falha ao registrar dispatch: {erro}", flush=True)
        print(f"Falha ao disparar worker para {job.get('source_id') or 'job'}", flush=True)
        return {"disparado": False, "motivo": "dispatch", "falhas": falhas}

    try:
        db.registrar_evento_ingest(job["id"], "dispatch", {})
    except db.ErroBanco as erro:
        print(f"Falha ao registrar dispatch: {erro}", flush=True)
    return {"disparado": True}
