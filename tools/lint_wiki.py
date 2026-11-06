#!/usr/bin/env python3
"""Valida estrutura, citações, quarentena e contatos da wiki."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
WIKI = ROOT / "wiki"
CITACAO = re.compile(r"\[fonte: ([A-Z0-9-]+)\]")
LINK = re.compile(r"\[[^\]]+\]\(([^)#]+\.md)\)")
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
CAMPOS = ["id", "titulo", "tipo", "status", "confianca", "fontes", "atualizado_em", "validado_por", "validado_em"]
SECOES = ["## Resumo", "## O que as fontes dizem", "## O que ainda não sabemos", "## Quem procurar", "## Páginas relacionadas"]


def main():
    erros, avisos = [], []
    manifesto = json.loads((ROOT / "sources/manifest.json").read_text(encoding="utf-8"))
    fontes = {f["id"]: f for f in manifesto["fontes"]}
    for fonte in fontes.values():
        if not (ROOT / fonte["arquivo"]).exists():
            erros.append(f"manifest: arquivo ausente {fonte['arquivo']}")
    paginas = sorted(WIKI.rglob("*.md"))
    indice = (WIKI / "index.md").read_text(encoding="utf-8")

    for pagina in paginas:
        nome = pagina.relative_to(ROOT).as_posix()
        texto = pagina.read_text(encoding="utf-8")
        relativo = pagina.relative_to(WIKI).as_posix()
        if relativo != "index.md" and f"({relativo})" not in indice:
            erros.append(f"{nome}: página órfã")
        citadas = set(CITACAO.findall(texto))
        for fonte in sorted(citadas):
            if fonte not in fontes:
                erros.append(f"{nome}: fonte desconhecida {fonte}")
            elif fontes[fonte]["uso"] == "quarentena":
                erros.append(f"{nome}: fonte em quarentena {fonte}")
        for link in LINK.findall(texto):
            if not (pagina.parent / link).resolve().exists():
                erros.append(f"{nome}: link quebrado {link}")
        if CPF.search(texto):
            erros.append(f"{nome}: possível CPF")
        for email in EMAIL.findall(texto):
            if not email.endswith("@example.edu"):
                erros.append(f"{nome}: e-mail fora do domínio de exemplo {email}")
        if pagina.name in ("index.md", "log.md"):
            continue
        partes = texto.split("---", 2)
        if not texto.startswith("---") or len(partes) < 3:
            erros.append(f"{nome}: frontmatter ausente")
            continue
        for campo in CAMPOS:
            if not re.search(rf"^{campo}:", partes[1], re.M):
                erros.append(f"{nome}: campo ausente {campo}")
        linha = re.search(r"^fontes: \[(.*)\]$", partes[1], re.M)
        declaradas = {x.strip() for x in linha.group(1).split(",") if x.strip()} if linha else set()
        for fonte in sorted(citadas ^ declaradas):
            erros.append(f"{nome}: frontmatter e citações divergem em {fonte}")
        if "status: rascunho" in partes[1]:
            avisos.append(f"{nome}: aguardando validação humana")
        if pagina.parent.name in ("processos", "decisoes"):
            posicoes = [texto.find(s) for s in SECOES]
            if -1 in posicoes or posicoes != sorted(posicoes):
                erros.append(f"{nome}: seções ausentes ou fora de ordem")
                continue
            for item in texto[posicoes[1]:posicoes[2]].splitlines():
                if re.match(r"^(- |\d+\. )", item) and not CITACAO.search(item):
                    erros.append(f"{nome}: item sem fonte: {item[:70]}")

    perguntas = json.loads((ROOT / "evals/perguntas.json").read_text(encoding="utf-8"))
    for pergunta in perguntas:
        for fonte in pergunta["fontes_esperadas"]:
            if fonte not in fontes:
                erros.append(f"evals: {pergunta['id']} usa fonte desconhecida {fonte}")

    for aviso in avisos:
        print(f"AVISO {aviso}")
    for erro in erros:
        print(f"ERRO  {erro}")
    print(f"\n{len(paginas)} páginas, {len(perguntas)} testes, {len(erros)} erros, {len(avisos)} avisos")
    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
