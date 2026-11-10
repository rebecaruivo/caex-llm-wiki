"""Reconciliação de um job de ingestão quando o PR correspondente fecha.

Usada pelo workflow de fechamento e pela manutenção diária, para que os dois
caminhos deixem o banco no mesmo estado: status terminal, hash atualizado,
original e nomes apagados e lock liberado. O disparo do próximo worker fica a
cargo de quem chama, depois de liberar o lock.
"""


def fechar_job(db, job, *, mesclado, agora):
    """Fecha o job como `publicado` ou `fechado_sem_merge` e limpa o original."""
    status = "publicado" if mesclado else "fechado_sem_merge"
    campos = {
        "status": status,
        "motivo": None,
        "etapa": None,
        "conteudo_bruto": None,
        "nomes_detectados": None,
        "atualizado_em": agora,
        "fechado_em": agora,
    }
    if mesclado:
        campos["publicado_em"] = agora
    db.atualizar_job_ingest(job["id"], campos)
    if job.get("hash_conteudo"):
        db.guardar_hash_ingest(job["hash_conteudo"], job["source_id"], status)
    _registrar(db, job["id"], status)
    db.liberar_lock_fila(job["id"])
    return status


def _registrar(db, job_id, evento):
    try:
        db.registrar_evento_ingest(job_id, evento, {})
    except Exception:  # noqa: BLE001 - evento é secundário à limpeza
        pass
