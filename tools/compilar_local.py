#!/usr/bin/env python3
"""CLI da fatia vertical: fonte local → compilador → lint → diff.

Exemplo:
    python3 tools/compilar_local.py --wiki . \
      --tipo ata --titulo "Ata de 10/04" --data 2030-04-10 \
      --texto "A assembleia aprovou o calendário."

O repositório original nunca é alterado: a compilação roda em uma cópia
temporária, o lint revisa a cópia e só o diff é impresso.
"""
import argparse
import difflib
import json
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import ingerir  # noqa: E402
from tools.lint_wiki import revisar  # noqa: E402

IGNORAR = shutil.ignore_patterns(
    ".git", ".env", ".venv", "__pycache__", "node_modules", "evals/resultados"
)


def copiar_repo(origem, destino):
    """Copia a árvore para um diretório temporário, sem metadados pesados."""
    shutil.copytree(origem, destino, ignore=IGNORAR, dirs_exist_ok=True)
    return destino


def gerar_diff(origem, copia):
    """Unified diff apenas das páginas de `wiki/`."""
    origem = Path(origem) / "wiki"
    copia = Path(copia) / "wiki"
    caminhos = sorted(
        {p.relative_to(origem) for p in origem.rglob("*") if p.is_file()}
        | {p.relative_to(copia) for p in copia.rglob("*") if p.is_file()}
    )
    partes = []
    for relativo in caminhos:
        antes = (origem / relativo).read_text(encoding="utf-8") if (origem / relativo).exists() else ""
        depois = (copia / relativo).read_text(encoding="utf-8") if (copia / relativo).exists() else ""
        if antes == depois:
            continue
        partes.extend(
            difflib.unified_diff(
                antes.splitlines(keepends=True),
                depois.splitlines(keepends=True),
                fromfile=f"a/wiki/{relativo.as_posix()}",
                tofile=f"b/wiki/{relativo.as_posix()}",
            )
        )
    return "".join(partes)


def executar(fonte, raiz, chamar_modelo):
    """Compila em cópia temporária, roda lint e imprime o diff. Devolve o código."""
    raiz = Path(raiz)
    with tempfile.TemporaryDirectory(prefix="caex-ingest-") as temporario:
        copia = copiar_repo(raiz, Path(temporario) / "repo")
        resultado = ingerir.compilar(fonte, copia, chamar_modelo, source_id=None)
        lint = revisar(copia)
        diff = gerar_diff(raiz, copia)

    print(f"Fonte: {resultado['source_id']} ({resultado['fonte']['tipo']})")
    print(f"Operações aplicadas: {len(resultado['aplicadas'])}")
    for aplicada in resultado["aplicadas"]:
        print(f"  + {aplicada['pagina']}: {aplicada['texto']}")
    print(f"Lint: {len(lint['erros'])} erros, {len(lint['avisos'])} avisos")
    for erro in lint["erros"]:
        print(f"  ERRO {erro['pagina']}: {erro['mensagem']}")
    print("")
    print(diff.rstrip() if diff else "(sem diff)")
    return 1 if lint["erros"] else 0


def _ler_fonte(argumentos):
    if argumentos.fonte:
        dados = json.loads(Path(argumentos.fonte).read_text(encoding="utf-8"))
    else:
        dados = {}
    for campo in ("tipo", "titulo", "data", "texto"):
        valor = getattr(argumentos, campo)
        if valor is not None:
            dados[campo] = valor
    if argumentos.texto_arquivo:
        dados["texto"] = Path(argumentos.texto_arquivo).read_text(encoding="utf-8")
    faltando = [c for c in ("tipo", "titulo", "data", "texto") if not dados.get(c)]
    if faltando:
        raise ValueError(
            "faltam campos da fonte: " + ", ".join(faltando) + " (use flags ou --fonte)"
        )
    return dados


def construir_parser():
    parser = argparse.ArgumentParser(
        description="Compila uma fonte local sobre uma cópia da wiki e mostra o diff.",
        epilog=(
            "Exemplo: python3 tools/compilar_local.py --tipo ata "
            "--titulo 'Ata' --data 2030-04-10 --texto '...'"
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
            return ingerir.chamar_gemini_json(prompt, modelo=argumentos.modelo)

    try:
        fonte = _ler_fonte(argumentos)
        return executar(fonte, argumentos.wiki, chamar_modelo)
    except ingerir.ErroCompilacao as erro:
        if erro.motivo == "lint":
            print(f"ERRO: lint com {len(erro.erros)} erros", file=sys.stderr)
            for mensagem in erro.erros:
                print(f"  ERRO {mensagem}", file=sys.stderr)
            return 1
        print(f"ERRO: {erro}", file=sys.stderr)
        for mensagem in erro.erros:
            print(f"  ERRO {mensagem}", file=sys.stderr)
        return 2
    except (ingerir.ErroFonte, ingerir.ErroOperacao, ingerir.ErroResposta, ingerir.ErroApi) as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
