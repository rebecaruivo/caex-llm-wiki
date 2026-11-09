"""Notificações best-effort da ingestão para o webhook da gestão.

Um único `NOTIFICACOES_WEBHOOK_URL`, com `X-Webhook-Secret` quando configurado.
Uma tentativa extra em caso de falha. A notificação nunca derruba o job: todas
as exceções são engolidas e o retorno é apenas `True`/`False`.

O payload tem uma lista fixa de campos seguros. Ele nunca inclui o UUID do job,
o texto bruto da fonte ou nomes de pessoas.
"""
import json
import os
import urllib.error
import urllib.request

EVENTOS = ("pr_aberto", "quarentena", "falhou", "lock_antigo")
TENTATIVAS = 2

CAMPOS_SEGUROS = (
    "evento",
    "source_id",
    "status",
    "motivo",
    "pr_url",
    "paginas",
    "criado_em",
    "atualizado_em",
)


def _abrir(pedido, timeout=10):
    """Abre o pedido HTTP. É o único ponto de rede, trocado nos testes."""
    return urllib.request.urlopen(pedido, timeout=timeout)


def montar_payload(
    evento,
    *,
    source_id=None,
    status=None,
    motivo=None,
    pr_url=None,
    paginas=None,
    criado_em=None,
    atualizado_em=None,
):
    """Monta o payload público de uma notificação, sem UUID nem texto bruto."""
    if evento not in EVENTOS:
        raise ValueError(f"evento de notificação inválido: {evento!r}")
    payload = {"evento": evento}
    if source_id is not None:
        payload["source_id"] = str(source_id)
    if status is not None:
        payload["status"] = str(status)
    payload["motivo"] = motivo
    if pr_url is not None:
        payload["pr_url"] = str(pr_url)
    if paginas is not None:
        payload["paginas"] = [str(pagina) for pagina in paginas]
    if criado_em is not None:
        payload["criado_em"] = str(criado_em)
    if atualizado_em is not None:
        payload["atualizado_em"] = str(atualizado_em)
    return payload


def notificar(payload, *, url=None, secret=None, abrir=None):
    """Envia o payload ao webhook. Devolve `True` se uma tentativa deu certo.

    Sem URL configurada, não faz nada e devolve `False`. Falhas de rede ou HTTP
    são silenciosas: a próxima tentativa é feita e, no fim, o job continua.
    """
    url = url if url is not None else os.environ.get("NOTIFICACOES_WEBHOOK_URL", "")
    if not url:
        return False
    if not isinstance(payload, dict):
        return False
    seguro = {chave: payload[chave] for chave in CAMPOS_SEGUROS if chave in payload}
    corpo = json.dumps(seguro, ensure_ascii=False).encode("utf-8")
    cabecalhos = {"Content-Type": "application/json"}
    segredo = secret if secret is not None else os.environ.get("NOTIFICACOES_WEBHOOK_SECRET", "")
    if segredo:
        cabecalhos["X-Webhook-Secret"] = segredo

    for _ in range(TENTATIVAS):
        pedido = urllib.request.Request(url, data=corpo, headers=cabecalhos, method="POST")
        try:
            with (abrir or _abrir)(pedido) as resposta:
                resposta.read()
            return True
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            continue
        except Exception:  # noqa: BLE001 - notificação nunca derruba o job
            continue
    return False


def notificar_job(enviar, evento, job, *, paginas=None, pr_url=None, status=None):
    """Monta o payload a partir do job e chama `enviar` sem derrubar o fluxo.

    `enviar` recebe apenas o payload seguro. Exceções são engolidas: a
    notificação é sempre best-effort, como em `notificar`.
    """
    if enviar is None:
        return False
    payload = montar_payload(
        evento,
        source_id=job.get("source_id"),
        status=status or job.get("status"),
        motivo=job.get("motivo"),
        pr_url=pr_url if pr_url is not None else job.get("pr_url"),
        paginas=paginas,
        criado_em=job.get("criado_em"),
        atualizado_em=job.get("atualizado_em"),
    )
    try:
        return bool(enviar(payload))
    except Exception:  # noqa: BLE001 - notificação nunca derruba o job
        return False
