#!/usr/bin/env python3
"""Testa o passo de reescrita das perguntas de continuação.

Diferente de run_evals.py, aqui não há modelo avaliador: as checagens são
textuais e determinísticas (contém, não contém, igual à original).
"""
import datetime
import json
import os
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oraculo import MODELO_PADRAO, ErroApi, carregar_env, carregar_prompt_reescrita, reescrever  # noqa: E402


def normalizar(texto):
    """Minúsculas e sem acentos, para "orçamento" bater com "orcamento"."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower().strip().rstrip("?.! ")


def checar(teste, reescrita):
    falhas = []
    texto = normalizar(reescrita)
    if teste.get("igual_original") and texto != normalizar(teste["pergunta"]):
        falhas.append("deveria ficar igual à original")
    for termo in teste.get("deve_conter", []):
        if termo not in texto:
            falhas.append(f"falta '{termo}'")
    for termo in teste.get("nao_deve_conter", []):
        if termo in texto:
            falhas.append(f"não deveria ter '{termo}'")
    return falhas


def main():
    carregar_env()
    chave = os.environ.get("GEMINI_API_KEY", "")
    if not chave or chave == "cole_sua_chave_aqui":
        print("Configure GEMINI_API_KEY no .env.")
        return 1
    modelo = os.environ.get("GEMINI_MODEL", MODELO_PADRAO)
    prompt = carregar_prompt_reescrita()
    testes = json.loads((ROOT / "evals/reescrita.json").read_text(encoding="utf-8"))

    resultados, aprovados = [], 0
    for teste in testes:
        try:
            reescrita = reescrever(teste["pergunta"], teste["anteriores"], chave, modelo, modelo_prompt=prompt)
            falhas = checar(teste, reescrita)
        except ErroApi as erro:
            reescrita, falhas = "", [f"erro da API: {erro}"]
        ok = not falhas
        aprovados += ok
        print(f"{teste['id']} {'OK   ' if ok else 'FALHA'} {teste['pergunta']}")
        print(f"      → {reescrita}")
        for falha in falhas:
            print(f"      ! {falha}")
        resultados.append({**teste, "reescrita": reescrita, "aprovado": ok, "falhas": falhas})

    print(f"\n{aprovados}/{len(testes)} aprovados · modelo {modelo}")
    pasta = ROOT / "evals/resultados"
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    destino = pasta / f"reescrita-{carimbo}.json"
    destino.write_text(json.dumps({"modelo": modelo, "aprovados": aprovados, "total": len(testes), "resultados": resultados}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Resultado salvo em {destino.relative_to(ROOT)}")
    return 0 if aprovados == len(testes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
