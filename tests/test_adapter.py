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
