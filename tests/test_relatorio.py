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


from datetime import date
import json
import tempfile
from pathlib import Path


class EnviosTest(unittest.TestCase):
    HOJE = date(2030, 4, 19)

    def test_agrupa_por_status_com_motivo_legivel(self):
        grupos = relatorio.separar_envios([
            {"titulo": "A", "status": "publicado", "pr_url": "https://github.com/x/y/pull/1"},
            {"titulo": "B", "status": "aguardando_revisao", "atualizado_em": "2030-04-15T10:00:00+00:00"},
            {"titulo": "C", "status": "sem_mudancas"},
            {"titulo": "D", "status": "falhou", "motivo": "lint"},
            {"titulo": "E", "status": "quarentena", "motivo": "injecao"},
            {"titulo": "F", "status": "na_fila"},
        ], self.HOJE)
        self.assertEqual([i["titulo"] for i in grupos["publicados"]], ["A"])
        self.assertEqual(grupos["aguardando_revisao"][0]["dias_esperando"], 4)
        self.assertEqual([i["titulo"] for i in grupos["sem_mudancas"]], ["C"])
        motivos = {i["titulo"]: i["motivo"] for i in grupos["nao_concluidos"]}
        self.assertEqual(motivos["D"], "a revisão automática recusou as mudanças")
        self.assertIn("instrução dirigida à IA", motivos["E"])
        self.assertNotIn("F", str(grupos))

    def test_titulo_perde_os_nomes_detectados(self):
        grupos = relatorio.separar_envios(
            [{"titulo": "Ata da reunião com Rosa", "status": "falhou", "nomes_detectados": ["Rosa"]}], self.HOJE
        )
        self.assertEqual(grupos["nao_concluidos"][0]["titulo"], "Ata da reunião com [nome removido]")


class WikiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        (self.raiz / "wiki/decisoes").mkdir(parents=True)
        (self.raiz / "wiki/pendencias.md").write_text(
            "# Pendências\n\n| ID | Problema | Tratamento | Quem resolve |\n|---|---|---|---|\n"
            "| P-01 | Datas divergem. [fonte: ATA-1] | Em análise. | Secretaria |\n\n"
            "## Documentos faltando\n- [ ] Estatuto.\n- [x] Já recebido.\n",
            encoding="utf-8",
        )
        (self.raiz / "wiki/decisoes/x.md").write_text(
            "# X\n\n## O que as fontes dizem\n"
            "- Prestação de contas até 22/04/2030. [fonte: ATA-1]\n"
            "- Evento em 25/05/2030. [fonte: ATA-1]\n"
            "- Data antiga 21/04/2030. [fonte: ATA-1] (substituída por [fonte: ATA-2] em 2030-04-10)\n"
            "Texto solto 20/04/2030 fora de lista.\n",
            encoding="utf-8",
        )

    def test_pendencias_e_documentos_faltando(self):
        pendencias, faltando = relatorio.ler_pendencias(self.raiz)
        self.assertEqual(pendencias, [{"id": "P-01", "problema": "Datas divergem.", "quem_resolve": "Secretaria"}])
        self.assertEqual(faltando, ["Estatuto."])

    def test_prazos_so_da_semana_e_nao_substituidos(self):
        prazos = relatorio.prazos_da_semana(self.raiz, date(2030, 4, 19))
        self.assertEqual([p["data"] for p in prazos], ["2030-04-22"])
        self.assertEqual(prazos[0]["texto"], "Prestação de contas até 22/04/2030.")

    def test_relatorio_completo_separa_fatos_e_ia(self):
        dados = relatorio.montar_relatorio(
            [{"pergunta": "Qual é o quórum?", "fontes": []}],
            [{"titulo": "Ata <b>X</b>", "status": "publicado", "pr_url": "javascript:alert(1)"}],
            "INICIO", "2030-04-19T12:00:00+00:00", raiz=self.raiz,
        )
        fatos = dados["html_fatos"]
        self.assertIn("Ata &lt;b&gt;X&lt;/b&gt;", fatos)
        self.assertNotIn("javascript:", fatos)
        self.assertIn("22/04/2030", fatos)
        self.assertIn("P-01", fatos)
        para_ia = json.loads(dados["para_ia"])
        self.assertEqual(para_ia["lacunas"][0]["pergunta"], "Qual é o quórum?")
        self.assertEqual(para_ia["documentos_faltando"], ["Estatuto."])
        self.assertNotIn("Ata", dados["para_ia"])


class OQueMudouTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        (self.raiz / "wiki/decisoes").mkdir(parents=True)
        (self.raiz / "wiki/decisoes/semana.md").write_text(
            "---\nfontes: [ATA-A, ATA-B]\n---\n\n# Semana de Integração\n\n## O que as fontes dizem\n"
            "- Data: 22/04/2030. [fonte: ATA-A] (substituída por [fonte: ATA-B] em 2030-04-20)\n"
            "- A data passou para 25/04/2030. [fonte: ATA-B]\n"
            "- O **orçamento** é de R$ 500. [fonte: ATA-A]\n",
            encoding="utf-8",
        )
        (self.raiz / "wiki/pendencias.md").write_text(
            "# Pendências\n\n| ID | Problema | Tratamento | Quem resolve |\n|---|---|---|---|\n"
            "| P-02 | Datas divergem. [fonte: ATA-B] | Em análise. | Secretaria |\n",
            encoding="utf-8",
        )
        (self.raiz / "wiki/log.md").write_text("- Ingestão de ATA-B [fonte: ATA-B]\n", encoding="utf-8")

    def test_linhas_novas_substituicoes_e_pendencias_da_fonte(self):
        mudancas = {m["caminho"]: m for m in relatorio.mudancas_da_fonte(self.raiz, "ATA-B")}
        semana = mudancas["wiki/decisoes/semana.md"]
        self.assertEqual(semana["pagina"], "Semana de Integração")
        self.assertEqual(semana["novas"], ["A data passou para 25/04/2030."])
        self.assertEqual(semana["substituidas"], ["Data: 22/04/2030."])
        self.assertIn("wiki/pendencias.md", mudancas)
        self.assertNotIn("wiki/log.md", mudancas)

    def test_frontmatter_nao_conta_e_markdown_e_limpo(self):
        mudancas = relatorio.mudancas_da_fonte(self.raiz, "ATA-A")
        self.assertEqual(mudancas[0]["novas"], [
            "Data: 22/04/2030. (já substituída por uma fonte posterior)",
            "O orçamento é de R$ 500.",
        ])

    def test_email_mostra_o_que_mudou_e_avisos_no_topo(self):
        dados = relatorio.montar_relatorio(
            [], [{"titulo": "Ata de 20/04", "status": "publicado", "source_id": "ATA-B"}],
            "INICIO", "2030-04-19T12:00:00+00:00", raiz=self.raiz,
        )
        fatos = dados["html_fatos"]
        self.assertLess(fatos.index("Avisos da semana"), fatos.index("Atas e documentos"))
        self.assertIn("Novo: A data passou para 25/04/2030.", fatos)
        self.assertIn("Substituído: <s>Data: 22/04/2030.</s>", fatos)
        self.assertIn("Pendência registrada: P-02", fatos)
        self.assertIn("Quinta-feira, 25/04", fatos)

    def test_fonte_sem_linhas_na_wiki_avisa(self):
        dados = relatorio.montar_relatorio(
            [], [{"titulo": "Ata X", "status": "publicado", "source_id": "ATA-Z"}],
            "INICIO", "2030-04-19T12:00:00+00:00", raiz=self.raiz,
        )
        self.assertIn("não foram encontradas na wiki", dados["html_fatos"])
