"""Testes das notificações best-effort, sem rede."""
import json
import unittest
from unittest.mock import patch

import notificacoes


class RespostaFalsa:
    def __init__(self):
        self.lido = False

    def read(self):
        self.lido = True
        return b"ok"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class NotificacoesTest(unittest.TestCase):
    def test_payload_nao_tem_uuid_texto_bruto_nem_nomes(self):
        payload = notificacoes.montar_payload(
            "pr_aberto",
            source_id="ATA-2030-04-10-A1B2C3",
            status="aguardando_revisao",
            pr_url="https://github.com/exemplo/repo/pull/12",
            paginas=["wiki/decisoes/semana.md"],
            criado_em="2026-11-08T12:00:00Z",
            atualizado_em="2026-11-08T12:05:00Z",
        )
        texto = json.dumps(payload)
        self.assertEqual(payload["evento"], "pr_aberto")
        self.assertNotIn("job_id", payload)
        self.assertNotIn("id", payload)
        self.assertNotIn("Rosa", texto)
        self.assertNotIn("texto", payload)
        self.assertNotIn("conteudo_bruto", payload)

    def test_evento_invalido_e_recusado(self):
        with self.assertRaises(ValueError):
            notificacoes.montar_payload("outro")

    def test_sem_url_nao_chama_rede(self):
        chamadas = []
        with patch.dict("os.environ", {"NOTIFICACOES_WEBHOOK_URL": ""}, clear=False):
            enviado = notificacoes.notificar(
                {"evento": "falhou"}, abrir=lambda *a, **k: chamadas.append(1)
            )
        self.assertFalse(enviado)
        self.assertEqual(chamadas, [])

    def test_envia_assinatura_quando_configurada(self):
        capturado = {}

        def abrir(pedido, timeout=10):
            capturado["url"] = pedido.full_url
            capturado["segredo"] = pedido.get_header("X-webhook-secret")
            capturado["corpo"] = json.loads(pedido.data.decode("utf-8"))
            return RespostaFalsa()

        enviado = notificacoes.notificar(
            {"evento": "quarentena", "source_id": "ATA-2030-04-10-ABCDEF"},
            url="https://exemplo.test/hook",
            secret="segredo-teste",
            abrir=abrir,
        )
        self.assertTrue(enviado)
        self.assertEqual(capturado["url"], "https://exemplo.test/hook")
        self.assertEqual(capturado["segredo"], "segredo-teste")
        self.assertEqual(capturado["corpo"]["evento"], "quarentena")

    def test_sem_segredo_nao_envia_cabecalho(self):
        capturado = {}

        def abrir(pedido, timeout=10):
            capturado["segredo"] = pedido.get_header("X-webhook-secret")
            return RespostaFalsa()

        notificacoes.notificar({"evento": "falhou"}, url="https://exemplo.test/hook", abrir=abrir)
        self.assertIsNone(capturado["segredo"])

    def test_falha_tenta_de_novo_uma_vez(self):
        tentativas = []

        def abrir(pedido, timeout=10):
            tentativas.append(1)
            if len(tentativas) == 1:
                raise OSError("indisponível")
            return RespostaFalsa()

        self.assertTrue(
            notificacoes.notificar({"evento": "falhou"}, url="https://exemplo.test/hook", abrir=abrir)
        )
        self.assertEqual(len(tentativas), 2)

    def test_duas_falhas_nao_derrubam_o_job(self):
        def abrir(pedido, timeout=10):
            raise RuntimeError("explodiu")

        self.assertFalse(
            notificacoes.notificar({"evento": "falhou"}, url="https://exemplo.test/hook", abrir=abrir)
        )


if __name__ == "__main__":
    unittest.main()
