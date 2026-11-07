"""Núcleo do oráculo do CAEX.

A avaliação (tools/run_evals.py) e a API (app.py) usam este mesmo código.
Assim, o comportamento medido nos testes é o mesmo que vai para o ar.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
MODELO_PADRAO = "gemini-3.5-flash-lite"
CITACAO = re.compile(r"\[fonte: ([A-Z0-9-]+)\]")
CITACAO_QUALQUER = re.compile(r"\[fonte:\s*([^\]]*)\]")


class ErroApi(Exception):
    """Falha ao falar com o Gemini."""


def carregar_env():
    """Lê o arquivo .env local, se existir. Na Vercel, as variáveis vêm do painel."""
    arquivo = ROOT / ".env"
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip().strip("\"'"))


def chamar_gemini(prompt, modelo, chave, tentativas=5):
    """Envia um prompt ao Gemini e devolve o texto da resposta."""
    url = f"{BASE_URL}/models/{modelo}:generateContent"
    corpo = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    cabecalhos = {"Content-Type": "application/json", "x-goog-api-key": chave}
    for tentativa in range(1, tentativas + 1):
        pedido = urllib.request.Request(url, data=corpo, headers=cabecalhos, method="POST")
        try:
            with urllib.request.urlopen(pedido, timeout=120) as resposta:
                dados = json.load(resposta)
            partes = dados["candidates"][0]["content"]["parts"]
            texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
            if not texto.strip():
                raise ErroApi("resposta vazia")
            return texto.strip()
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", "replace")[:300]
            if erro.code not in (429, 500, 502, 503, 504) or tentativa == tentativas:
                raise ErroApi(f"HTTP {erro.code}: {detalhe}") from erro
            espera = int(erro.headers.get("Retry-After") or 0) or 15 * tentativa
            print(f"  limite ou instabilidade (HTTP {erro.code}); aguardando {espera}s", flush=True)
            time.sleep(espera)
        except (urllib.error.URLError, TimeoutError) as erro:
            if tentativa == tentativas:
                raise ErroApi(f"falha de conexão: {erro}") from erro
            time.sleep(10 * tentativa)
        except (KeyError, IndexError, json.JSONDecodeError) as erro:
            raise ErroApi(f"formato inesperado da resposta: {erro}") from erro
    raise ErroApi("tentativas esgotadas")


def montar_wiki():
    """Junta as páginas da wiki, sem o campo id, que confundia o modelo."""
    paginas = sorted(p for p in (ROOT / "wiki").rglob("*.md") if p.name != "log.md")
    blocos = []
    for pagina in paginas:
        nome = pagina.relative_to(ROOT).as_posix()
        texto = pagina.read_text(encoding="utf-8")
        texto = re.sub(r"^id: .*\n", "", texto, count=1, flags=re.MULTILINE)
        blocos.append(f'<pagina caminho="{nome}">\n{texto}\n</pagina>')
    return "\n\n".join(blocos)


def carregar_prompt():
    return (ROOT / "prompts/oraculo.md").read_text(encoding="utf-8")


def ids_validos():
    """IDs que o oráculo pode citar: fontes do manifesto que não estão em quarentena."""
    manifesto = json.loads((ROOT / "sources/manifest.json").read_text(encoding="utf-8"))
    return {f["id"] for f in manifesto["fontes"] if f["uso"] == "wiki"}


def citacoes_invalidas(resposta, validos):
    """Lista citações que não são exatamente um ID válido."""
    return [c for c in CITACAO_QUALQUER.findall(resposta) if c.strip() not in validos]


def montar_prompt(pergunta, modelo_prompt, wiki):
    return modelo_prompt.replace("{{paginas}}", wiki).replace("{{pergunta}}", pergunta)


def versao_contexto(modelo_prompt, wiki):
    """Código curto que identifica a versão do prompt e da wiki."""
    return hashlib.sha256((modelo_prompt + wiki).encode("utf-8")).hexdigest()[:12]


def responder(pergunta, chave, modelo, modelo_prompt=None, wiki=None, validos=None):
    """Responde uma pergunta usando só a wiki e devolve a resposta com as fontes."""
    modelo_prompt = modelo_prompt if modelo_prompt is not None else carregar_prompt()
    wiki = wiki if wiki is not None else montar_wiki()
    validos = validos if validos is not None else ids_validos()
    texto = chamar_gemini(montar_prompt(pergunta, modelo_prompt, wiki), modelo, chave)
    return {
        "resposta": texto,
        "fontes": sorted(set(CITACAO.findall(texto)) & validos),
        "citacoes_invalidas": citacoes_invalidas(texto, validos),
    }
