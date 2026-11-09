"""Erros do Supabase não podem ecoar a linha nem o texto bruto."""
import io
import os
import unittest
import urllib.error
from unittest.mock import patch

import db


def http_error(codigo, corpo):
    return urllib.error.HTTPError(
        "https://projeto.supabase.co/rest/v1/ingest_jobs",
        codigo,
        "erro",
        {},
        io.BytesIO(corpo.encode("utf-8")),
    )


class ErroSeguroTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "https://projeto.supabase.co",
                "SUPABASE_SECRET_KEY": "sb_secret",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_erro_de_constraint_nao_vaza_titulo_nem_conteudo(self):
        corpo = (
            '{"code":"23514","details":"Failing row contains (ATA-1, Ata secreta, '
            'texto bruto da fonte).","hint":null,"message":"new row violates check '
            'constraint \\"ingest_jobs_titulo_check\\""}'
        )
        erro = http_error(400, corpo)
        with patch.object(db, "_abrir", lambda pedido, timeout=10: (_ for _ in ()).throw(erro)):
            with self.assertRaises(db.ErroBanco) as contexto:
                db.buscar_job_ingest("123e4567-e89b-12d3-a456-426614174000")
        mensagem = str(contexto.exception)
        self.assertIn("400", mensagem)
        self.assertIn("23514", mensagem)
        self.assertIn("ingest_jobs_titulo_check", mensagem)
        self.assertNotIn("Ata secreta", mensagem)
        self.assertNotIn("texto bruto", mensagem)
        self.assertNotIn("Failing row", mensagem)

    def test_duplicata_continua_identificavel_pelo_codigo_e_restricao(self):
        corpo = (
            '{"code":"23505","details":"Key (hash_conteudo)=(abc) already exists.",'
            '"hint":null,"message":"duplicate key value violates unique constraint '
            '\\"ingest_jobs_hash_bloqueante\\""}'
        )
        erro = http_error(409, corpo)
        with patch.object(db, "_abrir", lambda pedido, timeout=10: (_ for _ in ()).throw(erro)):
            with self.assertRaises(db.DuplicataIngest):
                db.criar_job_ingest({"source_id": "ATA-2030-04-10-ABCDEF"})

    def test_rpc_tambem_nao_vaza_corpo(self):
        corpo = (
            '{"code":"22023","details":"valor recebido: texto secreto",'
            '"hint":null,"message":"invalid parameter"}'
        )
        erro = http_error(400, corpo)
        with patch.object(db, "_abrir", lambda pedido, timeout=10: (_ for _ in ()).throw(erro)):
            with self.assertRaises(db.ErroBanco) as contexto:
                db.consumir_global("global:2030-04-10", "2030-04-10", 20, "2030-04-11T00:00:00Z")
        self.assertNotIn("texto secreto", str(contexto.exception))
        self.assertIn("22023", str(contexto.exception))


if __name__ == "__main__":
    unittest.main()
