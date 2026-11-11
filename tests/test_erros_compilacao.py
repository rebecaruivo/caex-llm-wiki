"""Erros de compilação: visíveis para a gestão, sem dados pessoais, sem gasto à toa.

- M4: a lista de erros vai para o evento `falhou` no Supabase, limpa de nomes
  e PII, e é a mesma versão limpa que aparece no log público do Actions.
- M3: falha da API do Gemini encerra a compilação na primeira vez, porque as
  retentativas de erro temporário já acontecem dentro de `chamar_gemini_json`.
"""
import json
import tempfile
import unittest

import ingerir
from tests.support import criar_repo, fonte
from tests.test_guardrails import registrar_fonte
from tests.test_worker import BaseWorker, criar_job
from tools import ingerir as worker


def classificador_com_nome(prompt):
    return json.dumps({"injecao": False, "sinais": [], "nomes": ["Marta Silva"]})


def compilador_pagina_inexistente(prompt):
    """Erro cuja mensagem repete um valor gerado: nome e telefone no caminho."""
    return json.dumps({
        "fatos_novos": [],
        "mudancas": [],
        "contradicoes": [],
        "lacunas": [],
        "operacoes": [{
            "tipo": "adicionar_linha",
            "pagina": "wiki/processos/Marta Silva (11) 98765-4321.md",
            "secao": "O que as fontes dizem",
            "texto": "Fato. [fonte: ATA-2030-04-10-ABCDEF]",
        }],
    })


class ErrosSegurosTest(unittest.TestCase):
    def test_troca_nomes_mascara_pii_e_corta(self):
        erros = worker.erros_seguros(
            [
                "página não encontrada: 'Marta Silva'",
                "contato (11) 98765-4321 e pessoa@gmail.com",
                "x" * 1000,
            ],
            ["Marta Silva"],
        )
        self.assertEqual(erros[0], "página não encontrada: '[nome removido]'")
        self.assertNotIn("98765", erros[1])
        self.assertNotIn("gmail", erros[1])
        self.assertLessEqual(len(erros[2]), worker.LIMITE_CARACTERES_ERRO)

    def test_limita_quantidade(self):
        self.assertEqual(len(worker.erros_seguros([str(i) for i in range(50)])), worker.LIMITE_ERROS)

    def test_sem_erros_devolve_lista_vazia(self):
        self.assertEqual(worker.erros_seguros(None), [])


class ErrosNoEventoTest(BaseWorker):
    def test_falha_de_compilacao_grava_erros_limpos_no_evento(self):
        job = criar_job()
        self.fake.jobs = [job]
        self.fake.estado["job_id"] = job["id"]

        resultado = self.executar(
            job["id"],
            chamar_classificador=classificador_com_nome,
            chamar_compilador=compilador_pagina_inexistente,
        )

        self.assertEqual(resultado["status"], "falhou")
        self.assertEqual(resultado["motivo"], "compilacao")
        self.assertTrue(resultado["erros"])
        eventos = json.dumps(self.fake.eventos, ensure_ascii=False)
        self.assertIn("página não encontrada", eventos)
        self.assertIn("[nome removido]", eventos)
        self.assertNotIn("Marta", eventos)
        self.assertNotIn("98765", eventos)
        self.assertNotIn("Marta", json.dumps(resultado["erros"], ensure_ascii=False))
        self.assertNotIn("erros", json.dumps(self.payloads, ensure_ascii=False))


class ApiForaDoArTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = criar_repo(self.tmp.name)
        registrar_fonte(self.raiz)

    def test_erro_de_api_encerra_na_primeira_chamada_com_motivo_infra(self):
        chamadas = []

        def gemini_fora(prompt):
            chamadas.append(1)
            raise ingerir.ErroApi("HTTP 503 ao chamar o Gemini")

        with self.assertRaises(ingerir.ErroCompilacao) as contexto:
            ingerir.compilar(fonte(), self.raiz, gemini_fora, source_id="ATA-2030-04-10")

        self.assertEqual(len(chamadas), 1)
        self.assertEqual(contexto.exception.motivo, "infra")
        self.assertEqual(contexto.exception.erros, ["HTTP 503 ao chamar o Gemini"])


if __name__ == "__main__":
    unittest.main()
