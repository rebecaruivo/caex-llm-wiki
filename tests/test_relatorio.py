"""Relatório semanal: lógica pura, sem banco nem rede."""
import unittest

import relatorio


def mensagem(pergunta, fontes=(), **campos):
    base = {
        "pergunta": pergunta,
        "pergunta_interpretada": None,
        "fontes": list(fontes),
        "citacoes_invalidas": [],
        "do_cache": False,
        "criado_em": "2030-04-20T12:00:00+00:00",
    }
    base.update(campos)
    return base


class MontarRelatorioTest(unittest.TestCase):
    def montar(self, mensagens, envios=()):
        return relatorio.montar_relatorio(mensagens, list(envios), "INICIO", "FIM")

    def test_conta_perguntas_cache_e_fontes(self):
        dados = self.montar([
            mensagem("Quando será a Semana?", ["ATA-1"]),
            mensagem("Quando será a Semana?", ["ATA-1"], do_cache=True),
            mensagem("Qual é o quórum?"),
            mensagem("Quem cuida da sala?", ["ATA-2"], citacoes_invalidas=["X"]),
        ])
        self.assertEqual(dados["perguntas"], 4)
        self.assertEqual(dados["reaproveitadas_do_cache"], 1)
        self.assertEqual(dados["respondidas_com_fonte"], 3)
        self.assertEqual(dados["sem_fonte"], 1)
        self.assertEqual(dados["com_citacao_invalida"], 1)
        self.assertEqual(dados["periodo"], {"inicio": "INICIO", "fim": "FIM"})

    def test_lacuna_e_resposta_sem_fonte_sem_repetir(self):
        dados = self.montar([
            mensagem("Qual é o quórum?"),
            mensagem("qual é o quórum"),
            mensagem("Tem estatuto?"),
        ])
        self.assertEqual([l["pergunta"] for l in dados["lacunas"]], ["Qual é o quórum?", "Tem estatuto?"])
        self.assertEqual(dados["sem_fonte"], 3)

    def test_usa_a_pergunta_interpretada(self):
        dados = self.montar([mensagem("E quem organiza?", pergunta_interpretada="Quem organiza a Feira?")])
        self.assertEqual(dados["lacunas"][0]["pergunta"], "Quem organiza a Feira?")

    def test_limita_tamanho_e_quantidade(self):
        dados = self.montar([mensagem(f"Pergunta {i} " + "x" * 400) for i in range(60)])
        self.assertEqual(len(dados["lacunas"]), relatorio.LIMITE_LACUNAS)
        self.assertEqual(dados["lacunas_omitidas"], 10)
        self.assertLessEqual(len(dados["lacunas"][0]["pergunta"]), relatorio.LIMITE_CARACTERES)

    def test_nao_leva_respostas_nem_ids(self):
        dados = self.montar([mensagem("Qual é o quórum?", resposta="segredo", chat_id="abc")])
        self.assertNotIn("segredo", str(dados))
        self.assertNotIn("abc", str(dados))

    def test_envios_por_status(self):
        dados = self.montar([], [{"status": "publicado"}, {"status": "publicado"}, {"status": "quarentena"}])
        self.assertEqual(dados["envios_de_atas"], {"publicado": 2, "quarentena": 1})

    def test_periodo_vazio(self):
        dados = self.montar([])
        self.assertEqual(dados["perguntas"], 0)
        self.assertEqual(dados["lacunas"], [])


if __name__ == "__main__":
    unittest.main()
