#!/usr/bin/env python3
"""Limpeza e reconciliação quando um PR de ingestão é fechado.

O workflow só chama este script para PRs do próprio repositório e de branches
`ingest/...`, mas o script valida de novo o repositório, o branch e o número do
PR. Com merge, o job vira `publicado` com `publicado_em`; sem merge, vira
`fechado_sem_merge`. Nos dois casos o original e os nomes são apagados, o hash
é atualizado e o lock é liberado. Só depois de liberar o lock o script chama a
mesma `talvez_avancar_fila()` do app, que toma o lock e então dispara o worker.

Exemplo (o GitHub Actions fornece `GITHUB_EVENT_PATH`):
    python3 tools/ingest_pr_fechado.py
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import db  # noqa: E402
import reconciliacao  # noqa: E402
from app import talvez_avancar_fila  # noqa: E402


class ErroEvento(ValueError):
    """O evento do PR não é um fechamento válido de ingestão."""


def _agora():
    return datetime.now(timezone.utc)


def _iso(momento):
    return momento.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def carregar_evento(caminho=None):
    """Lê o JSON do evento do GitHub."""
    caminho = caminho or os.environ.get("GITHUB_EVENT_PATH")
    if not caminho:
        raise ErroEvento("GITHUB_EVENT_PATH não definido")
    try:
        return json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erro:
        raise ErroEvento(f"evento do GitHub ilegível: {erro}") from erro


def validar_evento(evento, repositorio=None):
    """Revalida repositório, branch `ingest/...` e número do PR."""
    pr = (evento or {}).get("pull_request") or {}
    head = pr.get("head") or {}
    repo = (head.get("repo") or {}).get("full_name")
    branch = head.get("ref") or ""
    numero = pr.get("number") or (evento or {}).get("number")

    esperado = repositorio or os.environ.get("GITHUB_REPOSITORY", "")
    if not esperado:
        raise ErroEvento("GITHUB_REPOSITORY não definido")
    if repo != esperado:
        raise ErroEvento("PR não pertence a este repositório")
    if not isinstance(branch, str) or not branch.startswith("ingest/"):
        raise ErroEvento("PR não veio de um branch ingest/")
    if not isinstance(numero, int):
        raise ErroEvento("número do PR inválido")
    return {
        "branch": branch,
        "numero": numero,
        "mesclado": bool(pr.get("merged")),
    }


def executar(evento, *, repositorio=None, db_mod=None, avancar=None, agora=None):
    """Reconcilia o job do PR fechado e, depois de liberar o lock, avança a fila."""
    dados = validar_evento(evento, repositorio)
    db_mod = db_mod or db
    momento = agora() if callable(agora) else (agora or _agora())
    if isinstance(momento, str):
        momento = datetime.fromisoformat(momento.strip().replace("Z", "+00:00"))
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)

    job = db_mod.buscar_job_por_branch(dados["branch"])
    if not job:
        return {"status": "ignorado", "motivo": "job_ausente"}
    if job.get("status") in ("publicado", "fechado_sem_merge", "sem_mudancas", "quarentena", "falhou", "expirado"):
        return {"status": "ignorado", "motivo": "ja_terminal"}
    if job.get("pr_numero") not in (None, dados["numero"]):
        return {"status": "ignorado", "motivo": "pr_divergente"}

    status = reconciliacao.fechar_job(
        db_mod, job, mesclado=dados["mesclado"], agora=_iso(momento)
    )
    avanco = (avancar or talvez_avancar_fila)()
    return {"status": status, "avanco": avanco}


def main(argv=None, **dependencias):
    parser = argparse.ArgumentParser(
        description="Reconcilia o job quando um PR de ingestão fecha."
    )
    parser.add_argument("--evento", help="caminho do JSON do evento (padrão: GITHUB_EVENT_PATH)")
    parser.add_argument("--repositorio", help="owner/repo esperado (padrão: GITHUB_REPOSITORY)")
    argumentos = parser.parse_args(argv)

    try:
        evento = carregar_evento(argumentos.evento)
        resultado = executar(
            evento,
            repositorio=argumentos.repositorio,
            **dependencias,
        )
    except (ErroEvento, db.ErroBanco, OSError, ValueError) as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 2

    print(f"Resultado: {resultado['status']}")
    if resultado.get("motivo"):
        print(f"Motivo: {resultado['motivo']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
