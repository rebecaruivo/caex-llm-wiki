"""API do oráculo do CAEX. Todos os dados são fictícios."""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Body, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
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
