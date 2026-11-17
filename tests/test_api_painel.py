"""Rota e página do painel, com banco falso."""
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class FakeDB:
    ErroBanco = app.db.ErroBanco

    def __init__(self, falhar=False):
        self.falhar = falhar
        self.inicios = []

    def configurado(self):
        return True

    def dados_painel(self, inicio_iso):
        if self.falhar:
            raise self.ErroBanco("fora do ar")
        self.inicios.append(inicio_iso)
        return ([{"pergunta": "Qual é o quórum?", "fontes": [], "criado_em": inicio_iso}], [])


class PainelTest(unittest.TestCase):
    def usar(self, fake):
        patcher = patch.object(app, "db", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_devolve_semanas_totais_e_wiki(self):
        fake = self.usar(FakeDB())
        dados = app.ver_painel(8)
        self.assertEqual(len(dados["semanas"]), 8)
        self.assertEqual(dados["totais"]["perguntas"], 1)
        self.assertEqual(dados["lacunas_frequentes"][0]["pergunta"], "Qual é o quórum?")
        self.assertIn("paginas", dados["wiki"])
        self.assertTrue(fake.inicios[0].endswith("+00:00"))

    def test_periodo_limitado_entre_4_e_12_semanas(self):
        self.usar(FakeDB())
        self.assertEqual(len(app.ver_painel(1)["semanas"]), 4)
        self.assertEqual(len(app.ver_painel(52)["semanas"]), 12)

    def test_banco_fora_do_ar_da_503(self):
        self.usar(FakeDB(falhar=True))
        with self.assertRaises(app.HTTPException) as erro:
            app.ver_painel(8)
        self.assertEqual(erro.exception.status_code, 503)

    def test_pagina_existe_e_nao_usa_innerhtml(self):
        caminhos = [rota.path for rota in app.app.routes]
        self.assertIn("/painel", caminhos)
        html = (Path(app.RAIZ) / "static/painel.html").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", html)
        self.assertIn('aria-current="page">Painel', html)


if __name__ == "__main__":
    unittest.main()
