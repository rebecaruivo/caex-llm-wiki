"""O esquema JSON do compilador precisa declarar todo campo que o código lê.

Em modo estruturado, o Gemini só devolve campos presentes no `responseSchema`.
Um campo lido por `aplicar_operacoes` e ausente do esquema nunca chega, e a
operação é recusada em todas as tentativas. Os testes com modelo falso não
pegam isso, porque o modelo falso não respeita o esquema; estes testes pegam.
"""

import inspect
import json
import re
import tempfile
import unittest
from pathlib import Path

import ingerir
from tests.support import criar_repo, fonte
from tests.test_guardrails import registrar_fonte

RAIZ = Path(__file__).resolve().parents[1]
PROPRIEDADES = ingerir.ESQUEMA_RESPOSTA["properties"]["operacoes"]["items"]["properties"]


def campos_lidos_pelo_aplicador():
    """Campos lidos com `operacao.get("...")` dentro de `aplicar_operacoes`."""
    codigo = inspect.getsource(ingerir.aplicar_operacoes)
    return set(re.findall(r"operacao\.get\(\s*[\"']([a-z_]+)[\"']", codigo))


def operacoes_do_exemplo_no_prompt():
    """Operações do exemplo JSON em `prompts/compilador.md`."""
    texto = (RAIZ / "prompts/compilador.md").read_text(encoding="utf-8")
    bloco = re.search(r"```json\n(.*?)\n```", texto, re.S)
    return json.loads(bloco.group(1))["operacoes"]


class EsquemaCompiladorTest(unittest.TestCase):
    def test_todo_campo_lido_pelo_aplicador_esta_no_esquema(self):
        faltando = campos_lidos_pelo_aplicador() - set(PROPRIEDADES)
        self.assertEqual(
            faltando,
            set(),
            f"campos lidos pelo código e ausentes do esquema: {sorted(faltando)}",
        )

    def test_campos_do_exemplo_do_prompt_estao_no_esquema(self):
        for operacao in operacoes_do_exemplo_no_prompt():
            faltando = set(operacao) - set(PROPRIEDADES)
            self.assertEqual(
                faltando,
                set(),
                f"{operacao['tipo']}: o prompt pede campos que o esquema não aceita: {sorted(faltando)}",
            )

    def test_exemplo_do_prompt_cobre_todos_os_tipos(self):
        tipos = {operacao["tipo"] for operacao in operacoes_do_exemplo_no_prompt()}
        self.assertEqual(tipos, set(ingerir.TIPOS_OPERACAO))

    def test_tipo_da_operacao_e_restrito_aos_tipos_aceitos(self):
        self.assertEqual(PROPRIEDADES["tipo"]["enum"], list(ingerir.TIPOS_OPERACAO))


class SecoesEmListaTest(unittest.TestCase):
    """O formato do esquema (lista de seções) cria a mesma página que o antigo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = criar_repo(self.tmp.name)
        registrar_fonte(self.raiz)
        self.validos = {"ATA-2030-03-10", "ATA-2030-04-10"}

    def aplicar(self, secoes):
        ingerir.aplicar_operacoes(
            self.raiz,
            [{
                "tipo": "criar_pagina",
                "caminho": "wiki/decisoes/nova-decisao.md",
                "tipo_pagina": "decisao",
                "titulo": "Nova decisão",
                "secoes": secoes,
            }],
            self.validos,
            fonte=fonte(),
            source_id="ATA-2030-04-10",
            data_documento="2030-04-10",
        )
        return (self.raiz / "wiki/decisoes/nova-decisao.md").read_text(encoding="utf-8")

    def test_secoes_em_lista_criam_a_pagina(self):
        pagina = self.aplicar([
            {"secao": "Resumo", "linhas": ["Resumo da decisão."]},
            {"secao": "O que as fontes dizem", "linhas": ["Fato novo. [fonte: ATA-2030-04-10]"]},
            {"secao": "O que ainda não sabemos", "linhas": ["Ainda falta detalhe."]},
            {"secao": "Quem procurar", "linhas": ["Secretaria."]},
            {"secao": "Páginas relacionadas", "linhas": ["[Índice](../index.md)"]},
        ])
        self.assertIn("- Fato novo. [fonte: ATA-2030-04-10]", pagina)
        self.assertIn("fontes: [ATA-2030-04-10]", pagina)

    def test_secao_repetida_e_recusada(self):
        with self.assertRaises(ingerir.ErroOperacao):
            self.aplicar([
                {"secao": "Resumo", "linhas": ["Um."]},
                {"secao": "Resumo", "linhas": ["Dois."]},
            ])

    def test_secao_sem_nome_e_recusada(self):
        with self.assertRaises(ingerir.ErroOperacao):
            self.aplicar([{"linhas": ["Sem nome."]}])


if __name__ == "__main__":
    unittest.main()
