"""Testes da manutenção diária idempotente, com banco e GitHub falsos."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import app
import github_ingest


def iso(momento):
    return momento.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


AGORA = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


class FakeRequest:
    def __init__(self, authorization=None):
        self.headers = {"authorization": authorization} if authorization else {}


class FakeDB:
    def __init__(self):
        self.jobs = []
        self.estado = {
            "id": 1,
            "job_id": None,
            "pr_numero": None,
            "pr_url": None,
            "aberto_em": None,
            "ultima_tentativa": None,
        }
        self.hashes = {}
        self.eventos = []
        self.limites = []
        self.buscas = 0

    def configurado(self):
        return True

    def _job(self, job_id):
        for job in self.jobs:
            if str(job["id"]) == str(job_id):
                return job
        return None

    def listar_jobs_expirados(self, agora):
        return [job for job in self.jobs if job.get("expira_em") and job["expira_em"] < agora]

    def atualizar_job_ingest(self, job_id, campos):
        job = self._job(job_id)
        if job is None:
            return None
        job.update(campos)
        return job

    def registrar_evento_ingest(self, job_id, evento, detalhe=None):
        self.eventos.append(
            {"job_id": str(job_id), "evento": evento, "detalhe": detalhe or {}}
        )

    def liberar_lock_fila(self, job_id):
        if str(self.estado.get("job_id")) == str(job_id):
            self.estado.update(
                {"job_id": None, "aberto_em": None, "pr_numero": None, "pr_url": None}
            )

    def limpar_conteudo_job(self, job_id):
        job = self._job(job_id)
        if job is not None:
            job["conteudo_bruto"] = None
            job["nomes_detectados"] = None

    def limpar_limites_expirados(self, agora):
        self.limites = [item for item in self.limites if item["expira_em"] >= agora]

    def listar_jobs_terminais_antigos(self, limite, statuss):
        return [
            job
            for job in self.jobs
            if job.get("status") in statuss and job.get("atualizado_em", "") < limite
        ]

    def apagar_job_ingest(self, job_id):
        self.jobs = [job for job in self.jobs if str(job["id"]) != str(job_id)]
        self.eventos = [
            evento for evento in self.eventos if str(evento["job_id"]) != str(job_id)
        ]

    def listar_processando_parados(self, limite):
        return [
            job
            for job in self.jobs
            if job.get("status") == "processando" and job.get("atualizado_em", "") < limite
        ]

    def estado_fila(self):
        return dict(self.estado)

    def buscar_job_ingest(self, job_id):
        self.buscas += 1
        return self._job(job_id)

    def guardar_hash_ingest(self, hash_, source_id, resultado):
        self.hashes[hash_] = {
            "hash_conteudo": hash_,
            "source_id": source_id,
            "resultado": resultado,
        }

    def atualizar_estado_fila(self, campos):
        self.estado.update(campos)
        return dict(self.estado)


def job(**campos):
    base = {
        "id": str(uuid4()),
        "source_id": "ATA-2030-04-10-ABCDEF",
        "status": "na_fila",
        "motivo": None,
        "etapa": None,
        "hash_conteudo": "a" * 64,
        "conteudo_bruto": "texto secreto",
        "nomes_detectados": ["Rosa"],
        "expira_em": iso(AGORA - timedelta(days=1)),
        "criado_em": iso(AGORA - timedelta(days=40)),
        "atualizado_em": iso(AGORA - timedelta(days=1)),
        "pr_numero": None,
        "pr_url": None,
    }
    base.update(campos)
    return base


class BaseManutencao(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDB()
        self.payloads = []
        self.avancos = []
        self.patch_db = patch.object(app, "db", self.fake)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)

    def executar(self, **kwargs):
        kwargs.setdefault("agora", lambda: AGORA)
        kwargs.setdefault("notificar", self.payloads.append)
        kwargs.setdefault("avancar", lambda: self.avancos.append(1) or {"disparado": False})
        return app.executar_manutencao(**kwargs)

    def test_rota_de_manutencao_vem_antes_do_uuid_dinamico(self):
        caminhos = [rota.path for rota in app.app.routes]
        self.assertLess(
            caminhos.index("/api/ingest/manutencao"),
            caminhos.index("/api/ingest/{job_id}"),
        )

    def test_auth_exige_bearer_do_cron_secret(self):
        with patch.dict(os.environ, {"CRON_SECRET": "segredo"}, clear=False):
            with self.assertRaises(app.HTTPException) as erro:
                app.manutencao(FakeRequest())
            self.assertEqual(erro.exception.status_code, 401)
            with self.assertRaises(app.HTTPException) as erro:
                app.manutencao(FakeRequest("Bearer errado"))
            self.assertEqual(erro.exception.status_code, 401)

    def test_auth_sem_cron_secret_recusa(self):
        with patch.dict(os.environ, {"CRON_SECRET": ""}, clear=False):
            with self.assertRaises(app.HTTPException) as erro:
                app.manutencao(FakeRequest("Bearer qualquer"))
        self.assertEqual(erro.exception.status_code, 401)

    def test_auth_correta_executa(self):
        with patch.dict(os.environ, {"CRON_SECRET": "segredo"}, clear=False):
            with patch.object(app, "executar_manutencao", lambda: {"expirados": 0}):
                resposta = app.manutencao(FakeRequest("Bearer segredo"))
        self.assertEqual(resposta["status"], "ok")


class PassosManutencaoTest(BaseManutencao):
    def test_passo1_expira_fila_e_purga_aguardando_revisao(self):
        na_fila = job(status="na_fila")
        aguardando = job(
            status="aguardando_revisao",
            hash_conteudo="b" * 64,
            expira_em=iso(AGORA - timedelta(days=2)),
        )
        self.fake.jobs = [na_fila, aguardando]
        self.fake.estado.update({"job_id": na_fila["id"], "aberto_em": iso(AGORA - timedelta(days=1))})

        self.executar()

        self.assertEqual(na_fila["status"], "expirado")
        self.assertIsNone(na_fila["conteudo_bruto"])
        self.assertIsNone(self.fake.estado["job_id"])
        self.assertEqual(aguardando["status"], "aguardando_revisao")
        self.assertIsNone(aguardando["conteudo_bruto"])
        self.assertIsNone(aguardando["nomes_detectados"])
        self.assertIn("expirado", [evento["evento"] for evento in self.fake.eventos])
        self.assertEqual(
            self.fake.hashes[na_fila["hash_conteudo"]]["resultado"], "expirado"
        )
        self.assertNotIn(aguardando["hash_conteudo"], self.fake.hashes)

    def test_passo1_idempotente_nao_repete_evento(self):
        na_fila = job()
        self.fake.jobs = [na_fila]
        self.executar()
        eventos = len(self.fake.eventos)
        self.executar()
        self.assertEqual(len(self.fake.eventos), eventos)

    def test_passo2_limpa_limites_expirados(self):
        self.fake.limites = [
            {"chave": "velho", "expira_em": iso(AGORA - timedelta(minutes=1))},
            {"chave": "novo", "expira_em": iso(AGORA + timedelta(minutes=1))},
        ]
        self.executar()
        self.assertEqual([item["chave"] for item in self.fake.limites], ["novo"])

    def test_passo3_remove_job_terminal_antigo_e_eventos(self):
        antigo = job(
            status="publicado",
            atualizado_em=iso(AGORA - timedelta(days=91)),
            fechado_em=iso(AGORA - timedelta(days=91)),
        )
        recente = job(
            status="publicado",
            atualizado_em=iso(AGORA - timedelta(days=10)),
            hash_conteudo="c" * 64,
        )
        self.fake.jobs = [antigo, recente]
        self.fake.eventos = [
            {"job_id": antigo["id"], "evento": "publicado", "detalhe": {}},
            {"job_id": recente["id"], "evento": "publicado", "detalhe": {}},
        ]
        self.executar()
        self.assertNotIn(antigo, self.fake.jobs)
        self.assertIn(recente, self.fake.jobs)
        self.assertEqual([evento["job_id"] for evento in self.fake.eventos], [recente["id"]])

    def test_passo4_timeout_marca_falhou_notifica_e_libera(self):
        parado = job(
            status="processando",
            atualizado_em=iso(AGORA - timedelta(minutes=16)),
            expira_em=iso(AGORA + timedelta(days=10)),
        )
        self.fake.jobs = [parado]
        self.fake.estado.update({"job_id": parado["id"]})

        self.executar()

        self.assertEqual(parado["status"], "falhou")
        self.assertEqual(parado["motivo"], "timeout")
        self.assertIn(parado["hash_conteudo"], self.fake.hashes)
        self.assertIsNone(self.fake.estado["job_id"])
        self.assertEqual(self.payloads[-1]["evento"], "falhou")
        self.assertEqual(self.payloads[-1]["motivo"], "timeout")
        self.assertNotIn("job_id", self.payloads[-1])
        self.assertNotIn(parado["id"], json.dumps(self.payloads))

    def test_passo4_idempotente_nao_notifica_de_novo(self):
        parado = job(
            status="processando",
            atualizado_em=iso(AGORA - timedelta(minutes=16)),
            expira_em=iso(AGORA + timedelta(days=10)),
        )
        self.fake.jobs = [parado]
        self.executar()
        quantidade = len(self.payloads)
        self.executar()
        self.assertEqual(len(self.payloads), quantidade)

    def test_passo5_lock_orfao_inexistente(self):
        self.fake.estado.update({"job_id": str(uuid4())})
        self.executar()
        self.assertIsNone(self.fake.estado["job_id"])

    def test_passo5_lock_apontando_para_terminal(self):
        terminal = job(status="publicado")
        self.fake.jobs = [terminal]
        self.fake.estado.update({"job_id": terminal["id"], "aberto_em": iso(AGORA)})
        self.executar()
        self.assertIsNone(self.fake.estado["job_id"])

    def test_passo5_na_fila_antigo_marca_falhou_dispatch(self):
        preso = job(status="na_fila", expira_em=iso(AGORA + timedelta(days=10)))
        self.fake.jobs = [preso]
        self.fake.estado.update({
            "job_id": preso["id"],
            "ultima_tentativa": iso(AGORA - timedelta(days=8)),
        })

        self.executar()

        self.assertEqual(preso["status"], "falhou")
        self.assertEqual(preso["motivo"], "dispatch")
        self.assertIn(preso["hash_conteudo"], self.fake.hashes)
        self.assertIsNone(self.fake.estado["job_id"])
        self.assertEqual(self.payloads[-1]["evento"], "falhou")

    def test_passo6_pr_aberto_renova_e_notifica_lock_antigo(self):
        job_aberto = job(
            status="aguardando_revisao",
            pr_numero=12,
            pr_url="https://github.com/exemplo/repo/pull/12",
        )
        self.fake.jobs = [job_aberto]
        self.fake.estado.update({
            "job_id": job_aberto["id"],
            "pr_numero": 12,
            "pr_url": job_aberto["pr_url"],
            "aberto_em": iso(AGORA - timedelta(days=8)),
        })

        self.executar(
            buscar_pr=lambda numero: {
                "numero": 12,
                "state": "open",
                "merged": False,
                "url": job_aberto["pr_url"],
            }
        )

        self.assertEqual(self.fake.estado["aberto_em"], iso(AGORA))
        self.assertEqual(self.payloads[-1]["evento"], "lock_antigo")
        self.assertEqual(job_aberto["status"], "aguardando_revisao")

    def test_passo6_pr_mesclado_reconcilia_publicado(self):
        job_aberto = job(
            status="aguardando_revisao",
            pr_numero=12,
            pr_url="https://github.com/exemplo/repo/pull/12",
        )
        self.fake.jobs = [job_aberto]
        self.fake.estado.update({
            "job_id": job_aberto["id"],
            "aberto_em": iso(AGORA - timedelta(days=8)),
        })

        self.executar(
            buscar_pr=lambda numero: {
                "numero": 12,
                "state": "closed",
                "merged": True,
                "url": job_aberto["pr_url"],
            }
        )

        self.assertEqual(job_aberto["status"], "publicado")
        self.assertEqual(job_aberto["publicado_em"], iso(AGORA))
        self.assertIsNone(job_aberto["conteudo_bruto"])
        self.assertIsNone(self.fake.estado["job_id"])
        self.assertEqual(self.fake.hashes[job_aberto["hash_conteudo"]]["resultado"], "publicado")

    def test_passo6_pr_fechado_sem_merge_reconcilia(self):
        job_aberto = job(status="aguardando_revisao", pr_numero=12)
        self.fake.jobs = [job_aberto]
        self.fake.estado.update({
            "job_id": job_aberto["id"],
            "aberto_em": iso(AGORA - timedelta(days=8)),
        })
        self.executar(
            buscar_pr=lambda numero: {"numero": 12, "state": "closed", "merged": False}
        )
        self.assertEqual(job_aberto["status"], "fechado_sem_merge")
        self.assertIsNone(job_aberto["conteudo_bruto"])
        self.assertEqual(self.fake.hashes[job_aberto["hash_conteudo"]]["resultado"], "fechado_sem_merge")

    def test_passo6_aberto_em_nulo_busca_pr_por_branch(self):
        job_aberto = job(status="aguardando_revisao")
        self.fake.jobs = [job_aberto]
        self.fake.estado.update({"job_id": job_aberto["id"]})

        self.executar(
            buscar_pr_branch=lambda branch: {
                "numero": 15,
                "state": "open",
                "merged": False,
                "url": "https://github.com/exemplo/repo/pull/15",
            }
        )

        self.assertEqual(job_aberto["pr_numero"], 15)
        self.assertEqual(job_aberto["pr_url"], "https://github.com/exemplo/repo/pull/15")
        self.assertEqual(self.fake.estado["aberto_em"], iso(AGORA))
        self.assertEqual(self.payloads[-1]["evento"], "lock_antigo")

    def test_passo6_branch_sem_pr_marca_falhou_infra(self):
        job_aberto = job(status="aguardando_revisao")
        self.fake.jobs = [job_aberto]
        self.fake.estado.update({"job_id": job_aberto["id"]})
        self.executar(buscar_pr_branch=lambda branch: None)
        self.assertEqual(job_aberto["status"], "falhou")
        self.assertEqual(job_aberto["motivo"], "infra")
        self.assertIsNone(self.fake.estado["job_id"])
        self.assertEqual(self.payloads[-1]["evento"], "falhou")

    def test_github_fora_do_ar_nao_interrompe_e_tenta_de_novo(self):
        job_aberto = job(status="aguardando_revisao", pr_numero=12)
        self.fake.jobs = [job_aberto]
        self.fake.estado.update({
            "job_id": job_aberto["id"],
            "aberto_em": iso(AGORA - timedelta(days=8)),
        })

        def fora_do_ar(numero):
            raise github_ingest.ErroGitHub("sem conexão")

        resultado = self.executar(buscar_pr=fora_do_ar)
        self.assertTrue(resultado["github_falhou"])
        self.assertEqual(job_aberto["status"], "aguardando_revisao")
        self.assertEqual(self.fake.estado["job_id"], job_aberto["id"])
        self.assertEqual(self.avancos, [1])

        self.executar(
            buscar_pr=lambda numero: {"state": "closed", "merged": True}
        )
        self.assertEqual(job_aberto["status"], "publicado")

    def test_avanca_fila_uma_vez_no_fim(self):
        self.fake.jobs = [job()]
        self.executar()
        self.assertEqual(len(self.avancos), 1)


if __name__ == "__main__":
    unittest.main()
