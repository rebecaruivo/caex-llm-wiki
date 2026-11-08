#!/usr/bin/env python3
"""Testa a conexão com o Supabase: cria um chat de teste, lê e apaga."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
from oraculo import carregar_env, chave_cache  # noqa: E402


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
        chave = chave_cache("Pergunta de teste do cache", "teste", "teste")
        db.guardar_cache(chave, "Pergunta de teste do cache", "teste", "teste", {"resposta": "Resposta guardada", "fontes": []})
        db.guardar_cache(chave, "Pergunta de teste do cache", "teste", "teste", {"resposta": "Outra", "fontes": []})
        guardada = db.buscar_cache(chave_cache("pergunta de TESTE do cache?", "teste", "teste"))
        if not guardada or guardada["resposta"] != "Resposta guardada":
            print("ERRO: o cache não devolveu a primeira resposta guardada.")
            return 1
        print("7. Cache: guardou, ignorou a duplicata e achou a pergunta escrita de outro jeito")
        db.apagar_cache(chave)
        if db.buscar_cache(chave):
            print("ERRO: a resposta continua no cache depois de apagada.")
            return 1
        print("8. Cache: resposta apagada")
        m = db.metricas()
        print(f"9. Métricas: {m['perguntas']} perguntas, {m['reaproveitadas']} reaproveitadas")
    except db.ErroBanco as erro:
        print(f"Falhou: {erro}")
        return 1
    print("\nConexão com o Supabase funcionando.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
