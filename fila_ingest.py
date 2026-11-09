"""Fila de ingestão: disparo do worker, lock e notificação de falhas.

Este módulo concentra a parte operacional da fila para que `app.py` (API) e
`tools/ingest_pr_fechado.py` (workflow de fechamento) usem a mesma
implementação. O banco, o relógio, o disparo e a notificação são injetáveis.
"""
import json
import os
import urllib.error
import urllib.request

import config_ingestao
import db as db_padrao
import github_ingest
import notificacoes
from tempo import agora as agora_padrao
from tempo import iso_utc


class ErroDispatch(Exception):
    """Falha curta ao disparar o worker; nunca guarda o token."""


def disparar_worker(job_id, *, repositorio=None):
    """Dispara `ingerir.yml` via GitHub Actions. Sem token, falha curta."""
    token = os.environ.get("GITHUB_DISPATCH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ErroDispatch("token de dispatch ausente")
    corpo = json.dumps({"ref": "main", "inputs": {"job_id": str(job_id)}}).encode("utf-8")
    pedido = urllib.request.Request(
        f"https://api.github.com/repos/{repositorio or github_ingest.repositorio()}"
        "/actions/workflows/ingerir.yml/dispatches",
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


def talvez_avancar_fila(
    *, disparar=None, agora=None, db_mod=None, notificar=None
):
    """Toma o lock e dispara o worker; após o limite, marca `falhou`/`dispatch`."""
    db_mod = db_mod or db_padrao
    if not db_mod.configurado():
        return {"disparado": False, "motivo": "banco_nao_configurado"}
    try:
        job = db_mod.job_mais_antigo_na_fila()
    except db_padrao.ErroBanco as erro:
        print(f"Falha ao ler a fila: {erro}", flush=True)
        return {"disparado": False, "motivo": "erro_banco"}
    if not job:
        return {"disparado": False, "motivo": "fila_vazia"}

    try:
        tomou = db_mod.tomar_lock_fila(
            job["id"], config_ingestao.INTERVALO_DISPATCH_SEGUNDOS
        )
    except db_padrao.ErroBanco as erro:
        print(f"Falha ao tomar o lock: {erro}", flush=True)
        return {"disparado": False, "motivo": "erro_banco"}
    if not tomou:
        return {"disparado": False, "motivo": "lock"}

    momento = agora() if callable(agora) else (agora or agora_padrao())
    iso_agora = iso_utc(momento)
    try:
        (disparar or disparar_worker)(job["id"])
    except Exception:  # noqa: BLE001 - qualquer falha conta para o limite
        falhas = int(job.get("falhas_dispatch") or 0) + 1
        campos = {
            "falhas_dispatch": falhas,
            "ultima_tentativa": iso_agora,
            "atualizado_em": iso_agora,
        }
        esgotou = falhas >= config_ingestao.MAX_FALHAS_DISPATCH
        if esgotou:
            campos.update({
                "status": "falhou",
                "motivo": "dispatch",
                "etapa": None,
                "fechado_em": iso_agora,
            })
        try:
            db_mod.atualizar_job_ingest(job["id"], campos)
            db_mod.registrar_evento_ingest(
                job["id"], "dispatch_falhou", {"falhas": falhas}
            )
            if esgotou:
                if job.get("hash_conteudo"):
                    db_mod.guardar_hash_ingest(
                        job["hash_conteudo"], job["source_id"], "falhou"
                    )
                db_mod.liberar_lock_fila(job["id"])
                notificacoes.notificar_job(
                    notificar if notificar is not None else notificacoes.notificar,
                    "falhou",
                    {**job, "status": "falhou", "motivo": "dispatch"},
                    status="falhou",
                )
        except db_padrao.ErroBanco as erro:
            print(f"Falha ao registrar dispatch: {erro}", flush=True)
        print(f"Falha ao disparar worker para {job.get('source_id') or 'job'}", flush=True)
        return {"disparado": False, "motivo": "dispatch", "falhas": falhas}

    try:
        db_mod.registrar_evento_ingest(job["id"], "dispatch", {})
    except db_padrao.ErroBanco as erro:
        print(f"Falha ao registrar dispatch: {erro}", flush=True)
    return {"disparado": True}
