"""Painel: agregação semanal pura, sem banco nem rede."""
import unittest
from datetime import date

import painel

HOJE = date(2026, 11, 18)  # quarta-feira; semana atual começa em 16/11


def mensagem(criado_em, fontes=("A",), **campos):
    base = {"pergunta": "Pergunta?", "fontes": list(fontes), "do_cache": False,
            "avaliacao": None, "criado_em": criado_em}
    base.update(campos)
    return base


class SemanasTest(unittest.TestCase):
    def test_semanas_comecam_na_segunda_e_terminam_na_atual(self):
        semanas = painel.semanas_do_periodo(HOJE, 4)
        self.assertEqual([s.isoformat() for s in semanas],
                         ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"])

    def test_domingo_a_noite_em_brasilia_conta_na_semana_dele(self):
        # 23h de domingo 15/11 em Brasília = 02h de segunda 16/11 em UTC.
        dados = painel.montar_painel([mensagem("2026-11-16T02:00:00+00:00")], [], HOJE, 4)
        por_semana = {s["inicio"]: s["perguntas"] for s in dados["semanas"]}
        self.assertEqual(por_semana["2026-11-09"], 1)
        self.assertEqual(por_semana["2026-11-16"], 0)


class MontarPainelTest(unittest.TestCase):
    def setUp(self):
        self.dados = painel.montar_painel(
            [
                mensagem("2026-11-17T12:00:00Z", avaliacao="positiva"),
                mensagem("2026-11-17T12:05:00Z", do_cache=True),
                mensagem("2026-11-17T13:00:00Z", fontes=(), pergunta="Qual é o quórum?"),
                mensagem("2026-11-10T13:00:00Z", fontes=(), pergunta="qual é o quórum"),
                mensagem("2026-11-10T14:00:00Z", fontes=(), pergunta="Tem estatuto?"),
                mensagem("2026-11-10T15:00:00Z", avaliacao="erro", pergunta="Quem organiza?"),
                mensagem("2026-01-01T12:00:00Z"),  # fora do período
            ],
            [
                {"status": "publicado", "criado_em": "2026-11-17T10:00:00Z"},
                {"status": "falhou", "criado_em": "2026-11-11T10:00:00Z"},
                {"status": "publicado", "criado_em": "2025-01-01T10:00:00Z"},  # fora
            ],
            HOJE, 4, wiki={"paginas": 5},
        )

    def test_totais_e_porcentagens(self):
        t = self.dados["totais"]
        self.assertEqual(t["perguntas"], 6)
        self.assertEqual((t["com_fonte"], t["sem_fonte"]), (3, 3))
        self.assertEqual(t["pct_com_fonte"], 50)
        self.assertEqual(t["pct_do_cache"], 17)
        self.assertEqual((t["uteis"], t["erros"]), (1, 1))
        self.assertEqual((t["publicadas"], t["envios"]), (1, 2))

    def test_serie_semanal_soma_os_totais(self):
        serie = self.dados["semanas"]
        self.assertEqual(len(serie), 4)
        self.assertEqual(sum(s["perguntas"] for s in serie), 6)
        for s in serie:
            self.assertEqual(s["com_fonte"] + s["sem_fonte"], s["perguntas"])
        atual = serie[-1]
        self.assertEqual((atual["perguntas"], atual["publicadas"]), (3, 1))

    def test_lacunas_agrupadas_e_ordenadas(self):
        self.assertEqual(self.dados["lacunas_frequentes"][0], {"pergunta": "Qual é o quórum?", "vezes": 2})
        self.assertEqual(self.dados["lacunas_frequentes"][1]["vezes"], 1)
        self.assertEqual(self.dados["respostas_com_erro"], [{"pergunta": "Quem organiza?", "vezes": 1}])

    def test_envios_por_status_e_wiki(self):
        self.assertEqual(self.dados["envios_por_status"], {"publicado": 1, "falhou": 1})
        self.assertEqual(self.dados["wiki"], {"paginas": 5})

    def test_sem_perguntas_nao_divide_por_zero(self):
        vazio = painel.montar_painel([], [], HOJE, 4)
        self.assertIsNone(vazio["totais"]["pct_com_fonte"])
        self.assertEqual(vazio["lacunas_frequentes"], [])

    def test_nao_leva_respostas(self):
        dados = painel.montar_painel([mensagem("2026-11-17T12:00:00Z", resposta="segredo")], [], HOJE, 4)
        self.assertNotIn("segredo", str(dados))


if __name__ == "__main__":
    unittest.main()
