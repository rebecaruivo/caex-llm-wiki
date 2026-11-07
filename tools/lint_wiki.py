#!/usr/bin/env python3
"""Valida estrutura, citações, quarentena e contatos da wiki.

Pelo terminal: python3 tools/lint_wiki.py
Pela API: app.py chama revisar() e devolve o resultado como JSON.
"""
import json
import re
import sys
from pathlib import Path

CITACAO = re.compile(r"\[fonte: ([A-Z0-9-]+)\]")
LINK = re.compile(r"\[[^\]]+\]\(([^)#]+\.md)\)")
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
CAMPOS = ["id", "titulo", "tipo", "status", "confianca", "fontes", "atualizado_em", "validado_por", "validado_em"]
SECOES = ["## Resumo", "## O que as fontes dizem", "## O que ainda não sabemos", "## Quem procurar", "## Páginas relacionadas"]
DOMINIO = "@example.edu"


def item(tipo, pagina, problema, detalhe, acao, mensagem):
    """Um problema encontrado. "mensagem" é o texto da saída no terminal."""
    return {"tipo": tipo, "pagina": pagina, "problema": problema, "detalhe": detalhe, "acao": acao, "mensagem": mensagem}


def revisar(raiz):
    """Revisa a wiki em `raiz` e devolve páginas, testes, erros e avisos."""
    raiz = Path(raiz)
    wiki = raiz / "wiki"
    erros, avisos = [], []

    def erro(pagina, problema, detalhe, acao, mensagem):
        erros.append(item("erro", pagina, problema, detalhe, acao, mensagem))

    manifesto = json.loads((raiz / "sources/manifest.json").read_text(encoding="utf-8"))
    fontes = {f["id"]: f for f in manifesto["fontes"]}
    for fonte in fontes.values():
        if not (raiz / fonte["arquivo"]).exists():
            erro("sources/manifest.json", "Arquivo de fonte ausente", fonte["arquivo"],
                 "Adicionar o arquivo ou corrigir o caminho no manifesto.", f"arquivo ausente {fonte['arquivo']}")
    paginas = sorted(wiki.rglob("*.md"))
    indice = (wiki / "index.md").read_text(encoding="utf-8")

    for pagina in paginas:
        nome = pagina.relative_to(raiz).as_posix()
        texto = pagina.read_text(encoding="utf-8")
        relativo = pagina.relative_to(wiki).as_posix()
        if relativo != "index.md" and f"({relativo})" not in indice:
            erro(nome, "Página órfã", "Não aparece no índice", "Adicionar a página ao wiki/index.md.", "página órfã")
        citadas = set(CITACAO.findall(texto))
        for fonte in sorted(citadas):
            if fonte not in fontes:
                erro(nome, "Fonte desconhecida", f"{fonte} não está no manifesto",
                     "Conferir o ID ou registrar a fonte no manifesto.", f"fonte desconhecida {fonte}")
            elif fontes[fonte]["uso"] == "quarentena":
                erro(nome, "Fonte em quarentena", f"{fonte} não é confiável",
                     "Remover a citação. Fontes em quarentena não sustentam a wiki.", f"fonte em quarentena {fonte}")
        for link in LINK.findall(texto):
            if not (pagina.parent / link).resolve().exists():
                erro(nome, "Link quebrado", link, "Corrigir o caminho ou criar a página.", f"link quebrado {link}")
        if CPF.search(texto):
            erro(nome, "Possível CPF", "Número no formato de CPF", "Remover o dado da página.", "possível CPF")
        for email in EMAIL.findall(texto):
            if not email.endswith(DOMINIO):
                erro(nome, "E-mail fora do domínio institucional", email,
                     "Trocar pelo contato do setor responsável.", f"e-mail fora do domínio de exemplo {email}")
        if pagina.name in ("index.md", "log.md"):
            continue
        partes = texto.split("---", 2)
        if not texto.startswith("---") or len(partes) < 3:
            erro(nome, "Cabeçalho ausente", "A página não começa com o bloco ---",
                 "Adicionar o cabeçalho com os campos do CLAUDE.md.", "frontmatter ausente")
            continue
        for campo in CAMPOS:
            if not re.search(rf"^{campo}:", partes[1], re.M):
                erro(nome, "Campo ausente no cabeçalho", campo, "Adicionar o campo ao cabeçalho.", f"campo ausente {campo}")
        linha = re.search(r"^fontes: \[(.*)\]$", partes[1], re.M)
        declaradas = {x.strip() for x in linha.group(1).split(",") if x.strip()} if linha else set()
        for fonte in sorted(citadas ^ declaradas):
            erro(nome, "Cabeçalho e citações divergem", fonte,
                 "Toda fonte citada no texto deve estar no campo fontes, e vice-versa.",
                 f"frontmatter e citações divergem em {fonte}")
        if "status: rascunho" in partes[1]:
            avisos.append(item("aviso", nome, "Aguardando validação humana", "status: rascunho",
                               "A gestão revisa a página e muda o status para validado.", "aguardando validação humana"))
        if pagina.parent.name in ("processos", "decisoes"):
            posicoes = [texto.find(s) for s in SECOES]
            if -1 in posicoes or posicoes != sorted(posicoes):
                erro(nome, "Seções ausentes ou fora de ordem", "Seções obrigatórias do CLAUDE.md",
                     "Reorganizar a página nas seções e na ordem do CLAUDE.md.", "seções ausentes ou fora de ordem")
                continue
            for linha_item in texto[posicoes[1]:posicoes[2]].splitlines():
                if re.match(r"^(- |\d+\. )", linha_item) and not CITACAO.search(linha_item):
                    erro(nome, "Item sem fonte", linha_item[:70],
                         "Adicionar a citação [fonte: ID] ou remover o item.", f"item sem fonte: {linha_item[:70]}")

    perguntas = json.loads((raiz / "evals/perguntas.json").read_text(encoding="utf-8"))
    for pergunta in perguntas:
        for fonte in pergunta["fontes_esperadas"]:
            if fonte not in fontes:
                erro("evals/perguntas.json", "Teste usa fonte desconhecida", f"{pergunta['id']}: {fonte}",
                     "Corrigir o ID esperado no teste.", f"{pergunta['id']} usa fonte desconhecida {fonte}")

    return {"paginas": len(paginas), "testes": len(perguntas), "erros": erros, "avisos": avisos}


def main():
    raiz = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    resultado = revisar(raiz)
    for aviso in resultado["avisos"]:
        print(f"AVISO {aviso['pagina']}: {aviso['mensagem']}")
    for erro in resultado["erros"]:
        prefixo = "evals" if erro["pagina"] == "evals/perguntas.json" else "manifest" if erro["pagina"] == "sources/manifest.json" else erro["pagina"]
        print(f"ERRO  {prefixo}: {erro['mensagem']}")
    print(f"\n{resultado['paginas']} páginas, {resultado['testes']} testes, "
          f"{len(resultado['erros'])} erros, {len(resultado['avisos'])} avisos")
    return 1 if resultado["erros"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
