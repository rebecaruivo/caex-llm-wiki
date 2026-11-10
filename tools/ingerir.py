#!/usr/bin/env python3
"""Entrada do worker de ingestão e CLI de publicação de uma fonte.

Modo worker (GitHub Actions):
    python3 tools/ingerir.py <uuid-do-job>

Modo local (fatia vertical / reprocessamento):
    python3 tools/ingerir.py --fonte fonte.json

O caminho é o mesmo: triagem determinística, compilador com guardrails, fonte
derivada, manifesto, lint e abertura de PR. Git, GitHub, lint, relógio, banco e
modelo são injetáveis nos testes; nenhuma rede é usada pela suíte determinística.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import db  # noqa: E402
import ingerir  # noqa: E402
import notificacoes  # noqa: E402
import tempo  # noqa: E402


def _agora():
    return tempo.agora()


def _iso(momento):
    return tempo.iso_utc(momento)


def _uuid(valor):
    """Valida o UUID antes de qualquer consulta ao Supabase."""
    return str(UUID(str(valor)))


def _numero_pr(url):
    encontrado = re.search(r"/pull/(\d+)", str(url or ""))
    return int(encontrado.group(1)) if encontrado else None


def _paginas(resultado):
    compilacao = resultado.get("compilacao") or {}
    paginas = set(compilacao.get("novas_paginas") or [])
    for operacao in compilacao.get("operacoes") or []:
        pagina = operacao.get("pagina") or operacao.get("caminho")
        if pagina:
            paginas.add(pagina)
    return sorted(paginas)


def _duplicata_proxima(db_mod, fonte, source_id, raiz):
    jobs = []
    buscar = getattr(db_mod, "buscar_jobs_duplicata_proxima", None)
    if buscar:
        try:
            jobs = buscar(
                fonte["tipo"],
                fonte["data"],
                fonte["titulo"],
                ingerir.hash_conteudo(fonte["texto"]),
            )
        except Exception:  # noqa: BLE001 - sinal secundário não bloqueia
            jobs = []
    try:
        manifesto = ingerir.carregar_manifesto(raiz)
    except (OSError, ValueError, KeyError):
        manifesto = None
    return ingerir.detectar_duplicata_proxima(
        fonte,
        source_id=source_id,
        manifesto=manifesto,
        jobs=jobs,
    )


def _atualizar(db_mod, job_id, campos, *, evento=None, detalhe=None):
    db_mod.atualizar_job_ingest(job_id, campos)
    if evento:
        db_mod.registrar_evento_ingest(job_id, evento, detalhe or {})


def _falhar_job(db_mod, job, job_id, motivo, iso_agora, notificar):
    campos = {
        "status": "falhou",
        "motivo": motivo,
        "etapa": None,
        "atualizado_em": iso_agora,
        "fechado_em": iso_agora,
    }
    _atualizar(db_mod, job_id, campos, evento="falhou", detalhe={"motivo": motivo})
    if job.get("hash_conteudo"):
        db_mod.guardar_hash_ingest(job["hash_conteudo"], job["source_id"], "falhou")
    db_mod.liberar_lock_fila(job_id)
    notificacoes.notificar_job(notificar, "falhou", {**job, **campos}, status="falhou")
    return {"status": "falhou", "source_id": job.get("source_id"), "motivo": motivo}


def executar_job(
    job_id,
    *,
    raiz=RAIZ,
    db_mod=None,
    chamar_classificador=None,
    chamar_compilador=None,
    notificar=notificacoes.notificar,
    agora=None,
    git=None,
    gh=None,
    lint=None,
):
    """Executa um job já enfileirado, seguindo a ordem da especificação §7.1.

    A validação do UUID acontece antes de qualquer consulta. O lock só pode ser
    do próprio job. Todo caminho terminal sem PR grava o hash e libera o lock;
    com PR aberto, o lock continua com o job até o fechamento.
    """
    try:
        job_id = _uuid(job_id)
    except (ValueError, TypeError, AttributeError):
        return {"status": "ignorado", "motivo": "uuid_invalido"}

    db_mod = db_mod or db
    if chamar_classificador is None or chamar_compilador is None:
        padrao_classificador, padrao_compilador = _modelos_padrao(None)
        chamar_classificador = chamar_classificador or padrao_classificador
        chamar_compilador = chamar_compilador or padrao_compilador

    if not db_mod.configurado():
        return {"status": "ignorado", "motivo": "banco_nao_configurado"}

    try:
        job = db_mod.buscar_job_ingest(job_id)
    except db.ErroBanco:
        return {"status": "ignorado", "motivo": "erro_banco"}
    if not job:
        return {"status": "ignorado", "motivo": "job_ausente"}
    if job.get("status") != "na_fila":
        return {"status": "ignorado", "motivo": "status"}

    try:
        estado = db_mod.estado_fila()
    except db.ErroBanco:
        return {"status": "ignorado", "motivo": "erro_banco"}
    if not estado or str(estado.get("job_id")) != job_id:
        return {"status": "ignorado", "motivo": "lock"}

    momento = tempo.como_datetime(agora() if callable(agora) else agora)
    iso_agora = _iso(momento)

    _atualizar(
        db_mod,
        job_id,
        {
            "status": "processando",
            "etapa": "triagem",
            "atualizado_em": iso_agora,
            "ultima_tentativa": iso_agora,
        },
        evento="processando",
        detalhe={"etapa": "triagem"},
    )

    texto = job.get("conteudo_bruto")
    if not texto:
        _atualizar(
            db_mod,
            job_id,
            {
                "status": "expirado",
                "motivo": None,
                "etapa": None,
                "nomes_detectados": None,
                "atualizado_em": iso_agora,
                "fechado_em": iso_agora,
            },
            evento="expirado",
        )
        if job.get("hash_conteudo"):
            db_mod.guardar_hash_ingest(job["hash_conteudo"], job["source_id"], "expirado")
        db_mod.liberar_lock_fila(job_id)
        return {"status": "expirado", "source_id": job.get("source_id")}

    triagem = ingerir.triar(
        job.get("titulo") or "",
        texto,
        chamar_classificador,
        liberado_pela_gestao=bool(job.get("liberado_pela_gestao")),
    )

    if triagem["resultado"] in ("quarentena", "falhou"):
        status = triagem["resultado"]
        motivo = None if status == "quarentena" else (triagem.get("motivo") or "classificacao")
        _atualizar(
            db_mod,
            job_id,
            {
                "status": status,
                "motivo": motivo,
                "etapa": None,
                "nomes_detectados": triagem["nomes"],
                "sinais_injecao": triagem["sinais"],
                "atualizado_em": iso_agora,
                "fechado_em": iso_agora,
            },
            evento=status,
            detalhe={"motivo": motivo},
        )
        if job.get("hash_conteudo"):
            db_mod.guardar_hash_ingest(job["hash_conteudo"], job["source_id"], status)
        db_mod.liberar_lock_fila(job_id)
        atualizado = {**job, "status": status, "motivo": motivo}
        notificacoes.notificar_job(notificar, status, atualizado, status=status)
        return {"status": status, "source_id": job.get("source_id"), "motivo": motivo}

    _atualizar(
        db_mod,
        job_id,
        {
            "etapa": "compilando",
            "nomes_detectados": triagem["nomes"],
            "sinais_injecao": triagem["sinais"],
            "atualizado_em": iso_agora,
        },
        evento="triagem",
        detalhe={"resultado": "ok"},
    )

    fonte = {
        "tipo": job["tipo"],
        "titulo": job["titulo"],
        "data": str(job["data_documento"])[:10],
        "texto": texto,
    }
    possivel_duplicata = _duplicata_proxima(db_mod, fonte, job["source_id"], raiz)
    try:
        resultado = ingerir.publicar(
            fonte,
            raiz,
            chamar_compilador,
            resultado_triagem=triagem,
            source_id=job["source_id"],
            existentes=db_mod.listar_source_ids_ingest(),
            submetido_em=job.get("submetido_em") or job.get("criado_em") or iso_agora,
            agora=momento,
            lint=lint,
            git=git,
            gh=gh,
            possivel_duplicata=possivel_duplicata,
        )
    except ingerir.ErroFonte as erro:
        motivo = "pii" if "PII" in str(erro) else "schema"
        return _falhar_job(db_mod, job, job_id, motivo, iso_agora, notificar)
    except (ingerir.ErroOperacao, OSError):
        return _falhar_job(db_mod, job, job_id, "infra", iso_agora, notificar)
    status = resultado["status"]
    paginas = _paginas(resultado)

    if status == "aguardando_revisao":
        pr = resultado.get("pr") or {}
        numero = _numero_pr(pr.get("url"))
        campos = {
            "status": status,
            "motivo": None,
            "etapa": None,
            "pr_numero": numero,
            "pr_url": pr.get("url"),
            "branch": pr.get("branch") or f"ingest/{job['source_id']}",
            "paginas_afetadas": paginas,
            "atualizado_em": iso_agora,
        }
        _atualizar(db_mod, job_id, campos, evento="pr_aberto", detalhe={"paginas": len(paginas)})
        if numero:
            db_mod.marcar_pr_aberto(job_id, numero, pr.get("url"))
        atualizado = {**job, **campos}
        notificacoes.notificar_job(notificar, "pr_aberto", atualizado, paginas=paginas, status=status)
        return {
            "status": status,
            "source_id": job.get("source_id"),
            "pr": pr,
            "paginas": paginas,
        }

    campos = {
        "status": status,
        "motivo": resultado.get("motivo"),
        "etapa": None,
        "paginas_afetadas": paginas,
        "atualizado_em": iso_agora,
        "fechado_em": iso_agora,
    }
    _atualizar(
        db_mod,
        job_id,
        campos,
        evento=status,
        detalhe={"motivo": resultado.get("motivo")} if resultado.get("motivo") else {},
    )
    if job.get("hash_conteudo"):
        db_mod.guardar_hash_ingest(job["hash_conteudo"], job["source_id"], status)
    db_mod.liberar_lock_fila(job_id)
    if status == "falhou":
        atualizado = {**job, **campos}
        notificacoes.notificar_job(notificar, "falhou", atualizado, status="falhou")
    return {
        "status": status,
        "source_id": job.get("source_id"),
        "motivo": resultado.get("motivo"),
        "erros": resultado.get("erros") or [],
    }


def _ler_fonte(argumentos):
    dados = {}
    if argumentos.fonte:
        dados = json.loads(Path(argumentos.fonte).read_text(encoding="utf-8"))
    for campo in ("tipo", "titulo", "data", "texto"):
        valor = getattr(argumentos, campo)
        if valor is not None:
            dados[campo] = valor
    if argumentos.texto_arquivo:
        dados["texto"] = Path(argumentos.texto_arquivo).read_text(encoding="utf-8")
    faltando = [
        campo for campo in ("tipo", "titulo", "data", "texto") if not dados.get(campo)
    ]
    if faltando:
        raise ValueError(
            "faltam campos da fonte: " + ", ".join(faltando) + " (use flags ou --fonte)"
        )
    return dados


def executar(
    fonte,
    raiz,
    chamar_classificador,
    chamar_compilador,
    *,
    liberado_pela_gestao=False,
    submetido_em=None,
    source_id=None,
    existentes=None,
    agora=None,
    lint=None,
    git=None,
    gh=None,
):
    """Executa a triagem e a publicação; devolve um dicionário de resultado."""
    triagem = ingerir.triar(
        fonte["titulo"],
        fonte["texto"],
        chamar_classificador,
        liberado_pela_gestao=liberado_pela_gestao,
    )
    resultado = ingerir.publicar(
        fonte,
        raiz,
        chamar_compilador,
        resultado_triagem=triagem,
        source_id=source_id,
        existentes=existentes,
        submetido_em=submetido_em,
        agora=agora,
        lint=lint,
        git=git,
        gh=gh,
    )
    resultado["triagem"] = {
        "resultado": triagem["resultado"],
        "motivo": triagem["motivo"],
        "injecao": triagem["injecao"],
        "sinais": list(triagem["sinais"]),
        "nomes": len(triagem["nomes"]),
    }
    return resultado


def _imprimir(resultado):
    print(f"Fonte: {resultado.get('source_id') or '-'}")
    print(f"Resultado: {resultado['status']}")
    print(f"Status: {resultado['status']}")
    if resultado.get("motivo"):
        print(f"Motivo: {resultado['motivo']}")
    pr = resultado.get("pr") or {}
    if pr.get("url"):
        print(f"PR: {pr['url']}")
    if resultado.get("erros"):
        print(f"Erros: {len(resultado['erros'])}")
        for erro in resultado["erros"]:
            print(f"  ERRO {erro}")


def _modelos_padrao(modelo):
    from oraculo import carregar_env

    carregar_env()

    def chamar_classificador(prompt):
        return ingerir.chamar_gemini_json(
            prompt,
            modelo=modelo,
            response_schema=ingerir.ESQUEMA_CLASSIFICADOR,
        )

    def chamar_compilador(prompt):
        return ingerir.chamar_gemini_json(prompt, modelo=modelo)

    return chamar_classificador, chamar_compilador


def construir_parser():
    parser = argparse.ArgumentParser(
        description="Worker de ingestão ou publicação local de uma fonte.",
        epilog=(
            "Códigos de saída: 0 publicado ou sem mudanças, 1 quarentena, "
            "2 falha ou entrada inválida."
        ),
    )
    parser.add_argument("job_id", nargs="?", help="UUID do job, no modo worker")
    parser.add_argument("--job-id", dest="job_id_flag", help="UUID do job, no modo worker")
    parser.add_argument("--fonte", help="arquivo JSON com tipo, titulo, data e texto")
    parser.add_argument("--tipo", choices=list(ingerir.TIPOS), help="tipo da fonte")
    parser.add_argument("--titulo", help="título do documento")
    parser.add_argument("--data", help="data do documento em AAAA-MM-DD")
    parser.add_argument("--texto", help="texto integral da fonte")
    parser.add_argument("--texto-arquivo", help="arquivo com o texto da fonte")
    parser.add_argument(
        "--wiki", default=str(RAIZ), help="raiz do repositório/wiki (padrão: este repositório)"
    )
    parser.add_argument(
        "--liberado-pela-gestao",
        action="store_true",
        help="pula o pré-filtro, usa os nomes e ignora o veredito de injeção",
    )
    parser.add_argument("--submetido-em", help="timestamp ISO de submissão")
    parser.add_argument("--source-id", help="força um source_id (testes e reprocessamento)")
    parser.add_argument(
        "--existente",
        action="append",
        default=[],
        help="source_id já usado por job ou manifesto, para colisão",
    )
    parser.add_argument(
        "--modelo", default=None, help=f"modelo Gemini (padrão: {ingerir.MODELO_INGEST})"
    )
    return parser


def main(
    argv=None,
    chamar_classificador=None,
    chamar_compilador=None,
    chamar_modelo=None,
    **efeitos,
):
    parser = construir_parser()
    argumentos = parser.parse_args(argv)

    if chamar_modelo is not None:
        chamar_classificador = chamar_classificador or chamar_modelo
        chamar_compilador = chamar_compilador or chamar_modelo

    alvo_job = argumentos.job_id_flag or argumentos.job_id or os.environ.get("JOB_ID")
    if alvo_job:
        if chamar_classificador is None or chamar_compilador is None:
            padrao_classificador, padrao_compilador = _modelos_padrao(argumentos.modelo)
            chamar_classificador = chamar_classificador or padrao_classificador
            chamar_compilador = chamar_compilador or padrao_compilador
        resultado = executar_job(
            alvo_job,
            raiz=argumentos.wiki,
            chamar_classificador=chamar_classificador,
            chamar_compilador=chamar_compilador,
            **efeitos,
        )
        _imprimir(resultado)
        if resultado.get("status") == "quarentena":
            return 1
        if resultado.get("status") == "falhou":
            return 2
        return 0

    if chamar_classificador is None or chamar_compilador is None:
        padrao_classificador, padrao_compilador = _modelos_padrao(argumentos.modelo)
        chamar_classificador = chamar_classificador or padrao_classificador
        chamar_compilador = chamar_compilador or padrao_compilador

    try:
        fonte = _ler_fonte(argumentos)
        resultado = executar(
            fonte,
            argumentos.wiki,
            chamar_classificador,
            chamar_compilador,
            liberado_pela_gestao=argumentos.liberado_pela_gestao,
            submetido_em=argumentos.submetido_em,
            source_id=argumentos.source_id,
            existentes=argumentos.existente,
            **efeitos,
        )
    except (
        ingerir.ErroFonte,
        ingerir.ErroOperacao,
        ingerir.ErroResposta,
        ingerir.ErroApi,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 2

    _imprimir(resultado)
    if resultado["status"] == "quarentena":
        return 1
    if resultado["status"] == "falhou":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
