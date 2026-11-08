"""Testes da CLI de compilação local, com modelo injetado e sem rede."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.support import criar_repo, fonte
from tools import compilar_local


def modelo_que_adiciona(prompt):
    return json.dumps(
        {
            "fatos_novos": [{"texto": "Novo fato.", "fonte": "ATA-2030-03-10"}],
            "mudancas": [],
            "contradicoes": [],
            "lacunas": [],
            "operacoes": [
                {
                    "tipo": "adicionar_linha",
                    "pagina": "wiki/processos/exemplo.md",
                    "secao": "O que as fontes dizem",
                    "texto": "Novo fato. [fonte: ATA-2030-03-10]",
                }
            ],
        }
    )


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = criar_repo(self.tmp.name)
        self.original = (self.raiz / "wiki/processos/exemplo.md").read_text(encoding="utf-8")

    def executar(self, modelo=modelo_que_adiciona, extras=None):
        argumentos = [
            "--wiki",
            str(self.raiz),
            "--tipo",
            "ata",
            "--titulo",
            "Ata de teste",
            "--data",
            "2030-04-10",
            "--texto",
            "A assembleia aprovou o novo calendário.",
        ] + (extras or [])
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(saida):
            codigo = compilar_local.main(argumentos, chamar_modelo=modelo)
        return codigo, saida.getvalue()

    def test_aplica_linha_mostra_diff_e_nao_altera_original(self):
        codigo, saida = self.executar()
        self.assertEqual(codigo, 0)
        self.assertIn("+- Novo fato. [fonte: ATA-2030-03-10]", saida)
        self.assertIn("erros", saida)
        self.assertEqual((self.raiz / "wiki/processos/exemplo.md").read_text(encoding="utf-8"), self.original)

    def test_lint_com_erro_aparece_na_saida(self):
        def modelo_ruim(prompt):
            return json.dumps(
                {
                    "operacoes": [
                        {
                            "tipo": "adicionar_linha",
                            "pagina": "wiki/processos/exemplo.md",
                            "secao": "O que as fontes dizem",
                            "texto": "Contato pessoa@gmail.com [fonte: ATA-2030-03-10]",
                        }
                    ]
                }
            )

        codigo, saida = self.executar(modelo=modelo_ruim)
        self.assertEqual(codigo, 1)
        self.assertIn("1 erros", saida)
        self.assertIn("e-mail", saida)

    def test_operacao_nao_suportada_termina_com_erro_claro(self):
        def modelo_invalido(prompt):
            return json.dumps({"operacoes": [{"tipo": "apagar_linha"}]})

        codigo, saida = self.executar(modelo=modelo_invalido)
        self.assertEqual(codigo, 2)
        self.assertIn("apagar_linha", saida)
        self.assertIn("não suportada", saida)

    def test_ajuda_funciona(self):
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida), self.assertRaises(SystemExit) as erro:
            compilar_local.main(["--help"], chamar_modelo=modelo_que_adiciona)
        self.assertEqual(erro.exception.code, 0)
        self.assertIn("--modelo", saida.getvalue())


if __name__ == "__main__":
    unittest.main()
