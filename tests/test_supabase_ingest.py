"""A migração do ingest precisa conter tabelas, índices, RLS e RPCs."""
import unittest
from pathlib import Path

SQL = Path(__file__).resolve().parents[1] / "supabase" / "ingest.sql"


class TestMigracaoIngest(unittest.TestCase):
    def setUp(self):
        self.texto = SQL.read_text(encoding="utf-8")

    def test_tabelas(self):
        for tabela in (
            "ingest_jobs",
            "ingest_estado",
            "limites_ingest",
            "ingest_eventos",
            "ingest_hashes",
        ):
            with self.subTest(tabela=tabela):
                self.assertIn(f"create table {tabela}", self.texto)

    def test_indice_unico_parcial_do_hash(self):
        self.assertIn("create unique index ingest_jobs_hash_bloqueante", self.texto)
        self.assertIn("where status in ('na_fila','processando','aguardando_revisao'", self.texto)
        self.assertIn("'publicado','sem_mudancas','quarentena')", self.texto)

    def test_rls_sem_policy_publica(self):
        for tabela in (
            "ingest_jobs",
            "ingest_estado",
            "limites_ingest",
            "ingest_eventos",
            "ingest_hashes",
        ):
            with self.subTest(tabela=tabela):
                self.assertIn(f"alter table {tabela} enable row level security", self.texto)
        self.assertNotIn("create policy", self.texto.lower())

    def test_rpcs(self):
        for funcao in (
            "tomar_lock_fila",
            "liberar_lock_fila",
            "marcar_pr_aberto",
            "consumir_limite",
            "consumir_ip",
            "consumir_global",
            "devolver_global",
        ):
            with self.subTest(funcao=funcao):
                self.assertIn(f"function {funcao}", self.texto)

    def test_seed_do_estado(self):
        self.assertIn("insert into ingest_estado (id) values (1)", self.texto)


if __name__ == "__main__":
    unittest.main()
