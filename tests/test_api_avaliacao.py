"""Avaliação das respostas: útil, erro ou lacuna, guardada no histórico."""
import unittest
from unittest.mock import patch

import app


class FakeDB:
    ErroBanco = app.db.ErroBanco
    MensagemNaoEncontrada = app.db.MensagemNaoEncontrada

    def __init__(self):
        self.avaliadas = []
        self.cache_apagado = []

    def configurado(self):
        return True

    def avaliar_mensagem(self, mensagem_id, avaliacao, agora_iso):
        if mensagem_id == 404:
            raise self.MensagemNaoEncontrada("não")
        self.avaliadas.append((mensagem_id, avaliacao))
        return {"id": mensagem_id, "pergunta": "Quando será a Semana?", "pergunta_interpretada": None}

    def apagar_cache(self, chave):
        self.cache_apagado.append(chave)


class AvaliacaoTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDB()
        patcher = patch.object(app, "db", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_util_guarda_e_mantem_o_cache(self):
        resposta = app.avaliar(41, app.Avaliacao(tipo="positiva"))
        self.assertEqual(resposta, {"mensagem_id": 41, "avaliacao": "positiva"})
        self.assertEqual(self.fake.avaliadas, [(41, "positiva")])
        self.assertEqual(self.fake.cache_apagado, [])

    def test_erro_guarda_e_tira_a_resposta_do_cache(self):
        app.avaliar(41, app.Avaliacao(tipo="erro"))
        self.assertEqual(self.fake.avaliadas, [(41, "erro")])
        self.assertEqual(len(self.fake.cache_apagado), 1)

    def test_mensagem_inexistente_da_404(self):
        with self.assertRaises(app.HTTPException) as erro:
            app.avaliar(404, app.Avaliacao(tipo="positiva"))
        self.assertEqual(erro.exception.status_code, 404)

    def test_tipo_desconhecido_e_recusado_pelo_modelo(self):
        with self.assertRaises(Exception):
            app.Avaliacao(tipo="excelente")


class AvaliarMensagemDbTest(unittest.TestCase):
    def test_valida_antes_de_chamar_o_banco(self):
        for mensagem_id, avaliacao in ((1, "otima"), (0, "positiva"), (True, "positiva"), ("1", "erro")):
            with self.assertRaises(ValueError):
                app.db.avaliar_mensagem(mensagem_id, avaliacao, "2026-11-16T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
