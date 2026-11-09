#!/usr/bin/env python3
"""CLI de publicação de uma fonte: triagem, compilador, lint e PR.

Exemplo:
    python3 tools/ingerir.py --fonte fonte.json

O caminho é o mesmo usado pelo worker (ticket #7): triagem determinística,
compilador com guardrails, fonte derivada, manifesto, lint e abertura de PR.
Git, GitHub, lint, relógio e modelo são injetáveis nos testes; nenhuma rede é
usada pela suíte determinística.
"""
import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import ingerir  # noqa: E402


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


def construir_parser():
    parser = argparse.ArgumentParser(
        description="Publica uma fonte: triagem, compilador, lint, commit e PR.",
        epilog=(
            "Códigos de saída: 0 publicado ou sem mudanças, 1 quarentena, "
            "2 falha ou entrada inválida."
        ),
    )
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

    if chamar_classificador is None or chamar_compilador is None:
        from oraculo import carregar_env

        carregar_env()

        if chamar_classificador is None:
            def chamar_classificador(prompt):
                return ingerir.chamar_gemini_json(
                    prompt,
                    modelo=argumentos.modelo,
                    response_schema=ingerir.ESQUEMA_CLASSIFICADOR,
                )

        if chamar_compilador is None:
            def chamar_compilador(prompt):
                return ingerir.chamar_gemini_json(prompt, modelo=argumentos.modelo)

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
