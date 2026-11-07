"""API do oráculo do CAEX. Todos os dados são fictícios."""
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from oraculo import MODELO_PADRAO, ErroApi, carregar_env, responder

carregar_env()

app = FastAPI(
    title="Oráculo do CAEX",
    description="Responde perguntas usando só a wiki. Demo com dados fictícios.",
    version="0.1.0",
)


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=3, max_length=500, examples=["Quando será a Semana de Integração?"])


class Resposta(BaseModel):
    resposta: str
    fontes: list[str]
    citacoes_invalidas: list[str]


@app.get("/api/saude")
def saude():
    """Confirma que a API está no ar, sem chamar o Gemini."""
    return {"status": "ok"}


@app.post("/api/perguntar", response_model=Resposta)
def perguntar(dados: Pergunta):
    """Recebe uma pergunta e devolve a resposta do oráculo com as fontes citadas."""
    pergunta = dados.pergunta.strip()
    if len(pergunta) < 3:
        raise HTTPException(status_code=422, detail="Escreva uma pergunta com pelo menos 3 caracteres.")

    chave = os.environ.get("GEMINI_API_KEY", "")
    if not chave or chave == "cole_sua_chave_aqui":
        raise HTTPException(status_code=503, detail="Oráculo indisponível: a chave do modelo não está configurada.")

    modelo = os.environ.get("GEMINI_MODEL", MODELO_PADRAO)
    try:
        return responder(pergunta, chave, modelo, tentativas=2)
    except ErroApi as erro:
        print(f"Falha ao consultar o modelo: {erro}", flush=True)
        raise HTTPException(status_code=502, detail="Não foi possível consultar o modelo agora. Tente novamente.") from erro
