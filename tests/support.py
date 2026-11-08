"""Fixtures mínimas de repositório para os testes do ingest."""
import json
from pathlib import Path

PAGINA = """---
id: exemplo
titulo: Exemplo
tipo: processo
status: rascunho
confianca: alta
fontes: [ATA-2030-03-10]
atualizado_em: 2030-03-10
validado_por: null
validado_em: null
---

# Exemplo

## Resumo

Resumo curto. [fonte: ATA-2030-03-10]

## O que as fontes dizem

- Fato existente. [fonte: ATA-2030-03-10]

## Passo a passo

1. Passo existente. [fonte: ATA-2030-03-10]

## O que ainda não sabemos

- Uma lacuna.

## Quem procurar

- Secretaria.

## Páginas relacionadas

- [Índice](../index.md)
"""

PENDENCIAS = """---
id: pendencias
titulo: Pendências
tipo: manutencao
status: rascunho
confianca: alta
fontes: []
atualizado_em: 2030-03-10
validado_por: null
validado_em: null
---

# Pendências

## Documentos faltando

- [ ] Estatuto.
"""


def criar_repo(destino):
    """Cria um repositório mínimo e válido para lint em `destino`."""
    raiz = Path(destino)
    (raiz / "sources/atas").mkdir(parents=True)
    (raiz / "evals").mkdir()
    (raiz / "wiki/processos").mkdir(parents=True)

    (raiz / "sources/atas/ata.md").write_text("# Ata\n\nDADOS FICTÍCIOS.\n", encoding="utf-8")
    manifesto = {
        "atualizado_em": "2030-04-06",
        "aviso": "Todas as fontes são fictícias.",
        "fontes": [
            {
                "id": "ATA-2030-03-10",
                "titulo": "Ata de exemplo",
                "tipo": "ata",
                "autoridade": "ca-deliberativa",
                "data": "2030-03-10",
                "arquivo": "sources/atas/ata.md",
                "uso": "wiki",
            }
        ],
    }
    (raiz / "sources/manifest.json").write_text(json.dumps(manifesto), encoding="utf-8")
    (raiz / "evals/perguntas.json").write_text("[]", encoding="utf-8")
    (raiz / "wiki/index.md").write_text(
        "# Índice\n\n## Processos\n\n- [Exemplo](processos/exemplo.md)\n\n"
        "## Manutenção\n\n- [Pendências](pendencias.md)\n",
        encoding="utf-8",
    )
    (raiz / "wiki/processos/exemplo.md").write_text(PAGINA, encoding="utf-8")
    (raiz / "wiki/pendencias.md").write_text(PENDENCIAS, encoding="utf-8")
    return raiz


def fonte(**mudancas):
    dados = {
        "tipo": "ata",
        "titulo": "Ata de teste",
        "data": "2030-04-10",
        "texto": "A assembleia aprovou o novo calendário.",
    }
    dados.update(mudancas)
    return dados
