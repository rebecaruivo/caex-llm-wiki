"""Rota do relatório semanal: autenticação e montagem, com banco falso."""
import os
import unittest
from unittest.mock import patch

import app


class FakeRequest:
    def __init__(self, authorization=None):
        self.headers = {"authorization": authorization} if authorization else {}


class FakeDB:
    ErroBanco = app.db.ErroBanco

    def __init__(self):
        self.pedidos = []

    def configurado(self):
        return True

    def dados_do_periodo(self, inicio_iso):
        self.pedidos.append(inicio_iso)
        return (
            [{"pergunta": "Qual é o quórum?", "fontes": [], "criado_em": "2030-04-20T12:00:00+00:00"}],
            [{"status": "publicado"}],
        )


class RelatorioSemanalTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDB()
        patcher = patch.object(app, "db", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def chamar(self, autorizacao, token="segredo", dias=7):
        with patch.dict(os.environ, {"RELATORIO_TOKEN": token}, clear=False):
            return app.relatorio_semanal(FakeRequest(autorizacao), dias)

    def test_sem_token_ou_token_errado_recusa(self):
        for autorizacao in (None, "Bearer errado"):
            with self.assertRaises(app.HTTPException) as erro:
                self.chamar(autorizacao)
            self.assertEqual(erro.exception.status_code, 401)
        self.assertEqual(self.fake.pedidos, [])

    def test_sem_segredo_configurado_recusa(self):
        with self.assertRaises(app.HTTPException) as erro:
            self.chamar("Bearer ", token="")
        self.assertEqual(erro.exception.status_code, 401)

    def test_cron_secret_nao_abre_o_relatorio(self):
        with patch.dict(os.environ, {"CRON_SECRET": "do-cron"}, clear=False):
            with self.assertRaises(app.HTTPException):
                self.chamar("Bearer do-cron")

    def test_token_certo_devolve_relatorio(self):
        dados = self.chamar("Bearer segredo")
        self.assertEqual(dados["perguntas"], 1)
        self.assertEqual(dados["lacunas"][0]["pergunta"], "Qual é o quórum?")
        self.assertEqual(dados["envios_de_atas"], {"publicado": 1})

    def test_periodo_limitado_entre_1_e_31_dias(self):
        from datetime import datetime

        for dias, esperado in ((0, 1), (365, 31)):
            dados = self.chamar("Bearer segredo", dias=dias)
            inicio = datetime.fromisoformat(dados["periodo"]["inicio"])
            fim = datetime.fromisoformat(dados["periodo"]["fim"])
            self.assertEqual((fim - inicio).days, esperado)


    def test_hoje_invalido_recusa_e_valido_entra_nos_prazos(self):
        with patch.dict(os.environ, {"RELATORIO_TOKEN": "segredo"}, clear=False):
            with self.assertRaises(app.HTTPException) as erro:
                app.relatorio_semanal(FakeRequest("Bearer segredo"), 7, "19/04/2030")
            self.assertEqual(erro.exception.status_code, 422)
            dados = app.relatorio_semanal(FakeRequest("Bearer segredo"), 7, "2030-04-19")
        self.assertIn("html_fatos", dados)
        self.assertIn("para_ia", dados)

if __name__ == "__main__":
    unittest.main()
