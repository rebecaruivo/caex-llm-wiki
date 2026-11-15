"""Testes do adaptador Gemini em modo JSON, sem rede."""

import json
import unittest

import ingerir


class AdaptadorJsonTest(unittest.TestCase):
    def test_exige_modo_json_e_response_schema(self):
        capturado = {}

        def transporte(url, cabecalhos, corpo):
            capturado["url"] = url
            capturado["cabecalhos"] = cabecalhos
            capturado["corpo"] = corpo
            return {"candidates": [{"content": {"parts": [{"text": '{"operacoes": []}'}]}}]}

        texto = ingerir.chamar_gemini_json(
            "prompt", modelo="modelo-teste", chave="chave-teste", transporte=transporte
        )

        self.assertEqual(texto, '{"operacoes": []}')
        configuracao = capturado["corpo"]["generationConfig"]
        self.assertEqual(configuracao["responseMimeType"], "application/json")
        self.assertIn("responseSchema", configuracao)
        self.assertEqual(capturado["cabecalhos"]["x-goog-api-key"], "chave-teste")
        self.assertIn("modelo-teste", capturado["url"])

    def test_transporte_devolve_json_serializado_tambem(self):
        def transporte(url, cabecalhos, corpo):
            return json.dumps({"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})

        texto = ingerir.chamar_gemini_json("p", modelo="m", chave="k", transporte=transporte)
        self.assertEqual(texto, '{"ok": true}')

    def test_sem_chave_vira_erro_claro(self):
        with self.assertRaises(ingerir.ErroApi):
            ingerir.chamar_gemini_json("p", modelo="m", chave="", transporte=lambda *a: {})


if __name__ == "__main__":
    unittest.main()


def _erro_429(corpo, cabecalhos=None):
    import email.message
    import io
    import urllib.error

    mensagem = email.message.Message()
    for chave, valor in (cabecalhos or {}).items():
        mensagem[chave] = valor
    return urllib.error.HTTPError(
        "https://exemplo", 429, "Too Many Requests", mensagem,
        io.BytesIO(json.dumps(corpo).encode("utf-8")),
    )


def _corpo_429(cota, espera=None):
    detalhes = [{
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": cota}],
    }]
    if espera:
        detalhes.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": espera})
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": detalhes}}


RESPOSTA_OK = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}


class LimiteDeUsoTest(unittest.TestCase):
    """429 do Gemini: espera do corpo, cota diária e espera longa."""

    def test_respeita_retry_delay_do_corpo(self):
        chamadas, esperas = [], []

        def transporte(url, cabecalhos, corpo):
            chamadas.append(1)
            if len(chamadas) == 1:
                raise _erro_429(_corpo_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "40s"))
            return RESPOSTA_OK

        texto = ingerir.chamar_gemini_json(
            "p", modelo="m", chave="k", transporte=transporte, dormir=esperas.append
        )
        self.assertEqual(texto, '{"ok": true}')
        # Antes esperava 15 s fixos e repetia ainda dentro da janela do limite.
        self.assertEqual(esperas, [41.0])

    def test_cota_diaria_encerra_sem_repetir(self):
        chamadas = []

        def transporte(url, cabecalhos, corpo):
            chamadas.append(1)
            raise _erro_429(_corpo_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "30s"))

        with self.assertRaises(ingerir.ErroApi) as contexto:
            ingerir.chamar_gemini_json(
                "p", modelo="modelo-x", chave="k", transporte=transporte, dormir=lambda s: self.fail("não deveria esperar")
            )
        erro = contexto.exception
        self.assertEqual(len(chamadas), 1)
        self.assertTrue(erro.diaria)
        self.assertEqual(erro.status, 429)
        self.assertIn("cota diária", str(erro))
        self.assertIn("modelo-x", str(erro))

    def test_espera_longa_demais_encerra(self):
        def transporte(url, cabecalhos, corpo):
            raise _erro_429(_corpo_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "600s"))

        with self.assertRaises(ingerir.ErroApi) as contexto:
            ingerir.chamar_gemini_json(
                "p", modelo="m", chave="k", transporte=transporte, dormir=lambda s: self.fail("não deveria esperar")
            )
        self.assertFalse(contexto.exception.diaria)
        self.assertEqual(contexto.exception.espera, 600.0)

    def test_corpo_ilegivel_cai_na_espera_padrao(self):
        import email.message
        import io
        import urllib.error

        esperas, chamadas = [], []

        def transporte(url, cabecalhos, corpo):
            chamadas.append(1)
            if len(chamadas) == 1:
                raise urllib.error.HTTPError("u", 503, "x", email.message.Message(), io.BytesIO(b"<html>"))
            return RESPOSTA_OK

        ingerir.chamar_gemini_json("p", modelo="m", chave="k", transporte=transporte, dormir=esperas.append)
        self.assertEqual(esperas, [16])
