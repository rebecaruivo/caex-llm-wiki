"""A fila não pode ficar travada por um worker que morreu ou quebrou.

Cobre os dois caminhos do achado A1 da revisão:
- erro inesperado no worker termina o job como `falhou`/`infra` e libera o lock;
- job parado em `processando` além do limite é liberado por qualquer envio ou
  consulta de andamento, sem esperar a manutenção diária.
"""
import unittest
from datetime import timedelta
from uuid import uuid4

import config_ingestao
import db as db_real
import fila_ingest
from tests.test_worker import AGORA, BaseWorker, FakeDB, classificador_limpo, criar_job


class FakeFilaDB(FakeDB):
    """FakeDB do worker com o que a fila também usa."""

    def __init__(self):
        super().__init__()
        self.locks_tomados = []

    def job_mais_antigo_na_fila(self):
        fila = [job for job in self.jobs if job["status"] == "na_fila"]
        return sorted(fila, key=lambda job: job["criado_em"])[0] if fila else None

    def tomar_lock_fila(self, job_id, intervalo):
        if self.estado.get("job_id") not in (None, str(job_id)):
            return False
        self.estado["job_id"] = str(job_id)
        self.locks_tomados.append(str(job_id))
        return True


def iso(momento):
    return momento.strftime("%Y-%m-%dT%H:%M:%SZ")


class JobTravadoTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeFilaDB()
        self.disparos = []
        self.avisos = []

    def avancar(self):
        return fila_ingest.talvez_avancar_fila(
            disparar=self.disparos.append,
            agora=lambda: AGORA,
            db_mod=self.fake,
            notificar=self.avisos.append,
        )

    def test_processando_parado_alem_do_limite_e_liberado_e_a_fila_anda(self):
        minutos = config_ingestao.TIMEOUT_PROCESSANDO_MINUTOS + 5
        travado = criar_job(status="processando", atualizado_em=iso(AGORA - timedelta(minutes=minutos)))
        proximo = criar_job(source_id="ATA-2030-04-11-ABCDEF", hash_conteudo="b" * 64)
        self.fake.jobs = [travado, proximo]
        self.fake.estado["job_id"] = travado["id"]

        resultado = self.avancar()

        self.assertEqual(travado["status"], "falhou")
        self.assertEqual(travado["motivo"], "timeout")
        self.assertEqual(self.fake.hashes[travado["hash_conteudo"]]["resultado"], "falhou")
        self.assertTrue(resultado["disparado"])
        self.assertEqual(self.disparos, [proximo["id"]])
        self.assertEqual(self.avisos[-1]["evento"], "falhou")
        self.assertNotIn(travado["id"], str(self.avisos))

    def test_processando_recente_nao_e_mexido(self):
        recente = criar_job(status="processando", atualizado_em=iso(AGORA - timedelta(minutes=5)))
        proximo = criar_job(source_id="ATA-2030-04-11-ABCDEF", hash_conteudo="b" * 64)
        self.fake.jobs = [recente, proximo]
        self.fake.estado["job_id"] = recente["id"]

        resultado = self.avancar()

        self.assertEqual(recente["status"], "processando")
        self.assertFalse(resultado["disparado"])
        self.assertEqual(self.disparos, [])

    def test_pr_aguardando_revisao_nao_e_liberado(self):
        com_pr = criar_job(status="aguardando_revisao", atualizado_em=iso(AGORA - timedelta(days=3)))
        self.fake.jobs = [com_pr]
        self.fake.estado["job_id"] = com_pr["id"]

        self.avancar()

        self.assertEqual(com_pr["status"], "aguardando_revisao")
        self.assertEqual(self.fake.estado["job_id"], com_pr["id"])

    def test_banco_instavel_na_conferencia_nao_derruba_a_fila(self):
        class BancoInstavel(FakeFilaDB):
            def estado_fila(self):
                raise db_real.ErroBanco("fora do ar")

        self.fake = BancoInstavel()
        job = criar_job()
        self.fake.jobs = [job]

        resultado = self.avancar()

        self.assertTrue(resultado["disparado"])


class ErroInesperadoWorkerTest(BaseWorker):
    def test_erro_inesperado_marca_falhou_infra_e_libera_lock(self):
        def compilador_quebrado(prompt):
            raise RuntimeError("bug inesperado com texto da fonte")

        job = criar_job()
        self.fake.jobs = [job]
        self.fake.estado["job_id"] = job["id"]

        resultado = self.executar(
            job["id"], chamar_classificador=classificador_limpo, chamar_compilador=compilador_quebrado
        )

        self.assertEqual(resultado["status"], "falhou")
        self.assertEqual(resultado["motivo"], "infra")
        self.assertEqual(job["status"], "falhou")
        self.assertIsNone(self.fake.estado["job_id"])
        self.assertEqual(self.payloads[-1]["evento"], "falhou")

    def test_erro_inesperado_com_banco_fora_do_ar_nao_levanta(self):
        class BancoQueCai(FakeDB):
            def atualizar_job_ingest(self, job_id, campos):
                if campos.get("status") == "falhou":
                    raise db_real.ErroBanco("fora do ar")
                return super().atualizar_job_ingest(job_id, campos)

        self.fake = BancoQueCai()

        def compilador_quebrado(prompt):
            raise RuntimeError("bug")

        job = criar_job()
        self.fake.jobs = [job]
        self.fake.estado["job_id"] = job["id"]

        resultado = self.executar(job["id"], chamar_compilador=compilador_quebrado)

        self.assertEqual(resultado["status"], "falhou")
        self.assertEqual(resultado["motivo"], "infra")

    def test_erro_antes_de_processando_nao_marca_falhou(self):
        class BancoQueCaiNaLeitura(FakeDB):
            def estado_fila(self):
                raise RuntimeError("inesperado")

        self.fake = BancoQueCaiNaLeitura()
        job = criar_job()
        self.fake.jobs = [job]

        resultado = self.executar(job["id"])

        self.assertEqual(resultado["status"], "falhou")
        self.assertEqual(job["status"], "na_fila")


if __name__ == "__main__":
    unittest.main()
