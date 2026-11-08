#!/usr/bin/env python3
"""CLI de triagem local: pré-filtro, classificador e nomes.

Exemplo:
    python3 tools/triar_local.py --titulo "Ata de 10/04" --texto-arquivo ata.txt

A saída mostra apenas o resultado, o motivo e contagens: nunca imprime nomes
nem o texto bruto da fonte. Use `--derivado-saida` para gravar a fonte
derivada (com `[nome removido]`) em um arquivo local.
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
    if argumentos.titulo is not None:
        dados["titulo"] = argumentos.titulo
    if argumentos.texto is not None:
        dados["texto"] = argumentos.texto
    if argumentos.texto_arquivo:
        dados["texto"] = Path(argumentos.texto_arquivo).read_text(encoding="utf-8")
    faltando = [campo for campo in ("titulo", "texto") if not dados.get(campo)]
    if faltando:
        raise ValueError(
            "faltam campos da fonte: " + ", ".join(faltando) + " (use flags ou --fonte)"
        )
    return dados["titulo"], dados["texto"]


def executar(
    titulo, texto, chamar_modelo, *, liberado=False, derivado_saida=None
):
    """Roda a triagem e imprime um resumo sem nomes nem texto bruto."""
    resultado = ingerir.triar(
        titulo, texto, chamar_modelo, liberado_pela_gestao=liberado
    )
    if derivado_saida and resultado["texto_derivado"] is not None:
        Path(derivado_saida).write_text(resultado["texto_derivado"], encoding="utf-8")

    print(f"Resultado: {resultado['resultado']}")
    if resultado["motivo"]:
        print(f"Motivo: {resultado['motivo']}")
    print(f"Injeção: {resultado['injecao']}")
    print(f"Sinais: {len(resultado['sinais'])}")
    print(f"Nomes removidos: {len(resultado['nomes'])}")
    print(f"Substituições: {ingerir.contar_substituicoes(texto, resultado['nomes'])}")
    return {"ok": 0, "quarentena": 1, "falhou": 2}[resultado["resultado"]]


def construir_parser():
    parser = argparse.ArgumentParser(
        description="Triagem local de uma fonte: pré-filtro, classificador e nomes.",
        epilog=(
            "Códigos de saída: 0 aprovada, 1 quarentena e 2 falha ou entrada inválida."
        ),
    )
    parser.add_argument("--fonte", help="arquivo JSON com titulo e texto")
    parser.add_argument("--titulo", help="título do documento")
    parser.add_argument("--texto", help="texto integral da fonte")
    parser.add_argument("--texto-arquivo", help="arquivo com o texto da fonte")
    parser.add_argument(
        "--liberado-pela-gestao",
        action="store_true",
        help="pula o pré-filtro, usa os nomes e ignora o veredito de injeção",
    )
    parser.add_argument(
        "--derivado-saida",
        help="grava a fonte derivada (com [nome removido]) neste arquivo local",
    )
    parser.add_argument(
        "--modelo", default=None, help=f"modelo Gemini (padrão: {ingerir.MODELO_INGEST})"
    )
    return parser


def main(argv=None, chamar_modelo=None):
    parser = construir_parser()
    argumentos = parser.parse_args(argv)

    if chamar_modelo is None:
        from oraculo import carregar_env

        carregar_env()

        def chamar_modelo(prompt):
            return ingerir.chamar_gemini_json(
                prompt,
                modelo=argumentos.modelo,
                response_schema=ingerir.ESQUEMA_CLASSIFICADOR,
            )

    try:
        titulo, texto = _ler_fonte(argumentos)
        return executar(
            titulo,
            texto,
            chamar_modelo,
            liberado=argumentos.liberado_pela_gestao,
            derivado_saida=argumentos.derivado_saida,
        )
    except (OSError, ValueError, json.JSONDecodeError) as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
