#!/usr/bin/env python3
"""Testa a conexão com o Supabase: cria um chat de teste, lê e apaga."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
from oraculo import carregar_env  # noqa: E402


def main():
    carregar_env()
    if not db.configurado():
        print("Faltam SUPABASE_URL e SUPABASE_SECRET_KEY no .env.")
        return 1
    try:
        chat = db.criar_chat("Teste de conexão")
        print(f"1. Chat criado: {chat['id']}")
        db.salvar_mensagem(chat["id"], "Pergunta de teste", {
            "resposta": "Resposta de teste", "fontes": [], "citacoes_invalidas": [],
        })
        print("2. Mensagem salva")
        aberto = db.abrir_chat(chat["id"])
        print(f"3. Chat lido com {len(aberto['mensagens'])} mensagem")
        print(f"4. O histórico tem {len(db.listar_chats())} chat(s)")
        db.apagar_chat(chat["id"])
        print("5. Chat apagado")
        try:
            db.abrir_chat(chat["id"])
            print("ERRO: o chat ainda existe depois de apagado.")
            return 1
        except db.ChatNaoEncontrado:
            print("6. Confirmado: o chat e as mensagens sumiram")
    except db.ErroBanco as erro:
        print(f"Falhou: {erro}")
        return 1
    print("\nConexão com o Supabase funcionando.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
