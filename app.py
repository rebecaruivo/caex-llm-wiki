"""API do oráculo do CAEX. Todos os dados são fictícios."""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import db
from oraculo import MODELO_PADRAO, ErroApi, carregar_env, responder, resumo_wiki

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

    # Exemplo da página /docs: sem chat_id, para a primeira pergunta criar um chat.
    model_config = {"json_schema_extra": {"examples": [{"pergunta": "Quando será a Semana de Integração?"}]}}


class Registro(BaseModel):
    tipo: Literal["lacuna", "erro"]
    pergunta: str = Field(min_length=3, max_length=500)
    resposta: str = Field(default="", max_length=4000)


class Resposta(BaseModel):
    resposta: str
    fontes: list[str]
    citacoes_invalidas: list[str]
    chat_id: str | None = None
    salvo: bool = False


NAO_ENCONTRADO = "Este chat não existe mais. Alguém do CA pode ter apagado."


def exigir_banco():
    """Interrompe a rota com 503 se o Supabase não estiver configurado."""
    if not db.configurado():
        raise HTTPException(status_code=503, detail="O histórico ainda não está configurado.")


@app.get("/", include_in_schema=False)
def interface():
    """Entrega a página do oráculo, no mesmo domínio da API."""
    return FileResponse(RAIZ / "static" / "index.html", headers={"Cache-Control": "no-cache"})


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


@app.post("/api/perguntar", response_model=Resposta)
def perguntar(dados: Pergunta):
    """Recebe uma pergunta e devolve a resposta do oráculo com as fontes citadas."""
    pergunta = dados.pergunta.strip()
    if len(pergunta) < 3:
        raise HTTPException(status_code=422, detail="Escreva uma pergunta com pelo menos 3 caracteres.")

    chave = os.environ.get("GEMINI_API_KEY", "")
    if not chave or chave == "cole_sua_chave_aqui":
        raise HTTPException(status_code=503, detail="Oráculo indisponível: a chave do modelo não está configurada.")

    chat_id = str(dados.chat_id) if dados.chat_id else None
    if chat_id and db.configurado():
        # Confere antes de gastar uma chamada ao modelo.
        try:
            db.abrir_chat(chat_id)
        except db.ChatNaoEncontrado as erro:
            raise HTTPException(status_code=404, detail=NAO_ENCONTRADO) from erro
        except db.ErroBanco as erro:
            print(f"Falha ao abrir chat: {erro}", flush=True)

    modelo = os.environ.get("GEMINI_MODEL", MODELO_PADRAO)
    try:
        resultado = responder(pergunta, chave, modelo, tentativas=2)
    except ErroApi as erro:
        print(f"Falha ao consultar o modelo: {erro}", flush=True)
        raise HTTPException(status_code=502, detail="Não foi possível consultar o modelo agora. Tente novamente.") from erro

    # Salvar é um extra: se o banco falhar, a pessoa recebe a resposta mesmo assim.
    resultado["salvo"] = False
    if db.configurado():
        try:
            if not chat_id:
                chat_id = db.criar_chat(pergunta)["id"]
            db.salvar_mensagem(chat_id, pergunta, resultado)
            resultado["salvo"] = True
        except db.ErroBanco as erro:
            print(f"Falha ao salvar no histórico: {erro}", flush=True)
    resultado["chat_id"] = chat_id
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
