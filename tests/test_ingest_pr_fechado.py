"""Testes do fechamento de PR de ingestão, com banco e GitHub falsos."""
import json
import unittest
from datetime import datetime, timezone

import app
from tools import ingest_pr_fechado as fechar


AGORA = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
REPO = "exemplo/repo"


class FakeDB:
    def __init__(self):
        self.jobs = []
        self.hashes = {}
        self.eventos = []
        self.ordem = []

    def buscar_job_por_branch(self, branch):
        for job in self.jobs:
            if job.get("branch") == branch:
                return job
        return None

    def atualizar_job_ingest(self, job_id, campos):
        for job in self.jobs:
            if job["id"] == job_id:
                job.update(campos)
                return job
        return None

    def guardar_hash_ingest(self, hash_, source_id, resultado):
        self.hashes[hash_] = {"hash_conteudo": hash_, "source_id": source_id, "resultado": resultado}

    def registrar_evento_ingest(self, job_id, evento, detalhe=None):
        self.eventos.append({"job_id": job_id, "evento": evento, "detalhe": detalhe or {}})

    def liberar_lock_fila(self, job_id):
        self.ordem.append("liberar")


def evento(*, merged, branch="ingest/ATA-2030-04-10-ABCDEF", numero=12, repo=REPO):
    return {
        "action": "closed",
        "number": numero,
        "pull_request": {
            "number": numero,
            "merged": merged,
            "head": {"ref": branch, "repo": {"full_name": repo}},
        },
    }


def criar_job(**campos):
    base = {
        "id": "job-1",
        "source_id": "ATA-2030-04-10-ABCDEF",
        "status": "aguardando_revisao",
        "motivo": None,
        "etapa": None,
        "hash_conteudo": "a" * 64,
        "conteudo_bruto": "texto secreto",
        "nomes_detectados": ["Rosa"],
        "pr_numero": 12,
        "pr_url": "https://github.com/exemplo/repo/pull/12",
        "branch": "ingest/ATA-2030-04-10-ABCDEF",
    }
    base.update(campos)
    return base


class FechamentoTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDB()
        self.avancos = []

    def executar(self, dados):
        return fechar.executar(
            dados,
            repositorio=REPO,
            db_mod=self.fake,
            avancar=lambda: self.avancos.append(1) or {"disparado": True},
            agora=lambda: AGORA,
        )

    def test_merge_marca_publicado_e_limpa_original(self):
        job = criar_job()
        self.fake.jobs = [job]

        resultado = self.executar(evento(merged=True))

        self.assertEqual(resultado["status"], "publicado")
        self.assertEqual(job["status"], "publicado")
        self.assertEqual(job["publicado_em"], "2026-11-08T12:00:00Z")
        self.assertIsNone(job["conteudo_bruto"])
        self.assertIsNone(job["nomes_detectados"])
        self.assertEqual(self.fake.hashes[job["hash_conteudo"]]["resultado"], "publicado")
        self.assertEqual(self.fake.eventos[-1]["evento"], "publicado")

    def test_fechado_sem_merge_marca_status_e_limpa_original(self):
        job = criar_job()
        self.fake.jobs = [job]

        resultado = self.executar(evento(merged=False))

        self.assertEqual(resultado["status"], "fechado_sem_merge")
        self.assertEqual(job["status"], "fechado_sem_merge")
        self.assertEqual(job["fechado_em"], "2026-11-08T12:00:00Z")
        self.assertIsNone(job["conteudo_bruto"])
        self.assertEqual(self.fake.hashes[job["hash_conteudo"]]["resultado"], "fechado_sem_merge")

    def test_libera_lock_antes_de_avancar_a_fila(self):
        job = criar_job()
        self.fake.jobs = [job]
        ordem = []

        def liberar(job_id):
            ordem.append("liberar")

        def avancar():
            ordem.append("avancar")
            return {"disparado": True}

        self.fake.liberar_lock_fila = liberar
        resultado = fechar.executar(
            evento(merged=True),
            repositorio=REPO,
            db_mod=self.fake,
            avancar=avancar,
            agora=lambda: AGORA,
        )

        self.assertEqual(ordem, ["liberar", "avancar"])
        self.assertTrue(resultado["avanco"]["disparado"])

    def test_reusa_a_funcao_talvez_avancar_fila_do_app(self):
        self.assertIs(fechar.talvez_avancar_fila, app.talvez_avancar_fila)

    def test_repositorio_diferente_e_recusado(self):
        with self.assertRaises(fechar.ErroEvento):
            self.executar(evento(merged=True, repo="outro/repo"))

    def test_branch_fora_de_ingest_e_recusada(self):
        with self.assertRaises(fechar.ErroEvento):
            self.executar(evento(merged=True, branch="feature/x"))

    def test_numero_invalido_e_recusado(self):
        dados = evento(merged=True)
        dados["pull_request"]["number"] = "doze"
        dados.pop("number")
        with self.assertRaises(fechar.ErroEvento):
            self.executar(dados)

    def test_job_ausente_nao_falha(self):
        resultado = self.executar(evento(merged=True))
        self.assertEqual(resultado["motivo"], "job_ausente")
        self.assertEqual(self.avancos, [])

    def test_job_ja_terminal_e_ignorado(self):
        job = criar_job(status="publicado")
        self.fake.jobs = [job]
        resultado = self.executar(evento(merged=False))
        self.assertEqual(resultado["motivo"], "ja_terminal")
        self.assertEqual(self.avancos, [])

    def test_pr_divergente_e_ignorado(self):
        job = criar_job(pr_numero=99)
        self.fake.jobs = [job]
        resultado = self.executar(evento(merged=True))
        self.assertEqual(resultado["motivo"], "pr_divergente")
        self.assertEqual(self.avancos, [])

    def test_payload_do_evento_e_lido_do_arquivo(self):
        with self.subTest(), __import__("tempfile").TemporaryDirectory() as tmp:
            caminho = __import__("pathlib").Path(tmp) / "evento.json"
            caminho.write_text(json.dumps(evento(merged=True)), encoding="utf-8")
            self.assertEqual(fechar.carregar_evento(caminho)["pull_request"]["number"], 12)


if __name__ == "__main__":
    unittest.main()
