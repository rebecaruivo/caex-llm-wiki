"""Relógio e conversões ISO compartilhadas pela API e pelos tools do ingest."""
from datetime import datetime, timezone


def agora():
    """Relógio UTC padrão; sempre injetável nos chamadores."""
    return datetime.now(timezone.utc)


def como_datetime(valor):
    """Converte datetime ou texto ISO em datetime UTC.

    Valores vazios usam o relógio atual. Texto inválido levanta `ValueError`.
    """
    if valor is None:
        return agora()
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return agora()
        valor = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    if not isinstance(valor, datetime):
        raise ValueError("momento inválido: use datetime ou texto ISO")
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


def instante(valor):
    """Como `como_datetime`, mas devolve `None` para entrada inválida ou vazia."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    try:
        return como_datetime(valor)
    except (TypeError, ValueError):
        return None


def iso_utc(valor):
    """Formata um momento em ISO 8601 UTC com segundos e sufixo `Z`."""
    return (
        como_datetime(valor)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
