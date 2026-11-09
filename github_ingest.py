"""Consultas públicas de Pull Requests do GitHub, com transporte injetável.

Usado pela manutenção diária para reconciliar locks antigos sem exigir um
token. A API pública devolve `state` (`open`/`closed`) e `merged_at`; o fecho
sem merge tem `state: closed` e `merged_at: null`.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"


class ErroGitHub(Exception):
    """Falha ao consultar o GitHub; a manutenção tenta de novo na próxima vez."""


def _abrir(pedido, timeout=10):
    """Abre o pedido HTTP. É o único ponto de rede, trocado nos testes."""
    return urllib.request.urlopen(pedido, timeout=timeout)


def repositorio(valor=None):
    """Resolve `owner/repo` a partir do ambiente ou do `REPOSITORIO_URL`."""
    if valor:
        return valor
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = os.environ.get(
        "REPOSITORIO_URL", "https://github.com/rebecaruivo/caex-llm-wiki/blob/main"
    )
    partes = [parte for parte in urllib.parse.urlparse(url).path.split("/") if parte]
    if len(partes) >= 2:
        return "/".join(partes[:2])
    return "rebecaruivo/caex-llm-wiki"


def _requisitar(url, transporte=None):
    pedido = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with (transporte or _abrir)(pedido) as resposta:
            corpo = resposta.read()
    except urllib.error.HTTPError as erro:
        if erro.code == 404:
            return None
        raise ErroGitHub(f"GitHub respondeu {erro.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise ErroGitHub("sem conexão com o GitHub") from erro
    if isinstance(corpo, (bytes, bytearray)):
        corpo = corpo.decode("utf-8")
    try:
        return json.loads(corpo) if corpo else None
    except json.JSONDecodeError as erro:
        raise ErroGitHub("resposta inválida do GitHub") from erro


def _normalizar(dados):
    if not isinstance(dados, dict):
        return None
    return {
        "numero": dados.get("number"),
        "state": dados.get("state"),
        "merged": bool(dados.get("merged") or dados.get("merged_at")),
        "merged_at": dados.get("merged_at"),
        "url": dados.get("html_url"),
        "branch": (dados.get("head") or {}).get("ref"),
    }


def buscar_pr(numero, *, repositorio_=None, transporte=None):
    """Consulta um PR pelo número. Devolve `None` quando ele não existe."""
    if numero is None:
        return None
    repo = repositorio(repositorio_)
    dados = _requisitar(f"{API}/repos/{repo}/pulls/{int(numero)}", transporte)
    return _normalizar(dados)


def buscar_pr_aberto_por_branch(branch, *, repositorio_=None, transporte=None):
    """Procura um PR aberto cujo head é o branch informado."""
    repo = repositorio(repositorio_)
    dono = repo.split("/")[0]
    alvo = urllib.parse.quote(f"{dono}:{branch}", safe=":/")
    dados = _requisitar(f"{API}/repos/{repo}/pulls?state=open&head={alvo}", transporte)
    if isinstance(dados, list) and dados:
        return _normalizar(dados[0])
    return None
