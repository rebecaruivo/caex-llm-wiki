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


def responder(pergunta, chave, modelo, modelo_prompt=None, wiki=None, validos=None, tentativas=5):
    """Responde uma pergunta usando só a wiki e devolve a resposta com as fontes."""
    modelo_prompt = modelo_prompt if modelo_prompt is not None else carregar_prompt()
    wiki = wiki if wiki is not None else montar_wiki()
    validos = validos if validos is not None else ids_validos()
    texto = chamar_gemini(montar_prompt(pergunta, modelo_prompt, wiki), modelo, chave, tentativas)
    return {
        "resposta": texto,
        "fontes": sorted(set(CITACAO.findall(texto)) & validos),
        "citacoes_invalidas": citacoes_invalidas(texto, validos),
    }


def ler_frontmatter(texto):
    """Lê o bloco entre --- no topo de uma página e devolve um dicionário."""
    if not texto.startswith("---"):
        return {}
    bloco = texto.split("---", 2)[1]
    dados = {}
    for linha in bloco.strip().splitlines():
        if ":" not in linha:
            continue
        chave, valor = (parte.strip() for parte in linha.split(":", 1))
        if valor.startswith("[") and valor.endswith("]"):
            valor = [item.strip() for item in valor[1:-1].split(",") if item.strip()]
        elif valor == "null":
            valor = None
        dados[chave] = valor
    return dados


def resumo_wiki():
    """Descreve o que a wiki cobre, o que falta e as fontes, a partir dos arquivos."""
    paginas = []
    for pasta in ("processos", "decisoes"):
        for arquivo in sorted((ROOT / "wiki" / pasta).glob("*.md")):
            meta = ler_frontmatter(arquivo.read_text(encoding="utf-8"))
            paginas.append({
                "titulo": meta.get("titulo"),
                "tipo": meta.get("tipo"),
                "status": meta.get("status"),
                "atualizado_em": meta.get("atualizado_em"),
                "validado_em": meta.get("validado_em"),
                "fontes": meta.get("fontes", []),
            })

    pendencias = (ROOT / "wiki/pendencias.md").read_text(encoding="utf-8")
    faltando = []
    if "## Documentos faltando" in pendencias:
        trecho = pendencias.split("## Documentos faltando", 1)[1].split("\n## ", 1)[0]
        faltando = [linha.split("]", 1)[1].strip() for linha in trecho.splitlines() if linha.startswith("- [ ]")]

    manifesto = json.loads((ROOT / "sources/manifest.json").read_text(encoding="utf-8"))
    fontes = [
        {"id": f["id"], "titulo": f["titulo"], "tipo": f["tipo"], "data": f["data"], "arquivo": f["arquivo"]}
        for f in manifesto["fontes"]
        if f["uso"] == "wiki"
    ]
    datas = [p["atualizado_em"] for p in paginas if p["atualizado_em"]]
    return {
        "atualizado_em": max(datas) if datas else None,
        "paginas": paginas,
        "ainda_nao_cobre": faltando,
        "fontes": fontes,
    }


# ===== Reescrita de perguntas de continuação =====
# Passo 1 de 2: transforma "E quem organiza?" em "Quem organiza a Semana de Integração?".
# O passo 2 é o responder() acima, sem mudança. Assim os evals do oráculo continuam valendo.

HISTORICO_MAXIMO = 3


def carregar_prompt_reescrita():
    return (ROOT / "prompts/reescrita.md").read_text(encoding="utf-8")


def montar_prompt_reescrita(pergunta, anteriores, modelo_prompt):
    valores = {
        "historico": "\n".join(f"- {h}" for h in anteriores),
        "pergunta": pergunta,
    }
    # Uma só passada: um texto do usuário com "{{pergunta}}" não é substituído de novo.
    return re.sub(r"\{\{(historico|pergunta)\}\}", lambda m: valores[m.group(1)], modelo_prompt)


def validar_reescrita(texto, original):
    """Usa a pergunta original quando a reescrita sai do formato esperado."""
    linha = texto.strip().strip('"“”').strip()
    if not linha or "\n" in linha or len(linha) > 500 or "[fonte:" in linha:
        return original
    return linha


def reescrever(pergunta, historico, chave, modelo, modelo_prompt=None, tentativas=5):
    """Devolve a pergunta completa, usando as perguntas anteriores do chat.

    historico: perguntas anteriores, da mais antiga para a mais recente.
    Sem histórico, devolve a pergunta como veio, sem chamar o modelo.
    """
    anteriores = [h.strip() for h in historico if h and h.strip()][-HISTORICO_MAXIMO:]
    if not anteriores:
        return pergunta
    modelo_prompt = modelo_prompt if modelo_prompt is not None else carregar_prompt_reescrita()
    texto = chamar_gemini(montar_prompt_reescrita(pergunta, anteriores, modelo_prompt), modelo, chave, tentativas)
    return validar_reescrita(texto, pergunta)
