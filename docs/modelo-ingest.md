# Escolha do modelo do ingest

A autoridade continua sendo `docs/especificacao-ingest.md` (§11 e §13.2). Este
documento registra como a constante `config_ingestao.MODELO_INGEST` deve ser
escolhida com evidência real e o que fazer enquanto a comparação ao vivo não
puder ser executada.

## Situação atual

A última rodada de evals ao vivo **não foi executada** neste ambiente porque
`GEMINI_API_KEY_INGEST` não está disponível. Nenhum resultado de comparação foi
inventado ou copiado de outra rodada: `MODELO_INGEST` permanece
`gemini-3.5-flash-lite`, o valor já documentado na especificação.

Isso é um bloqueio operacional, não uma decisão de modelo. A evidência só passa
a existir depois que alguém com a chave rodar o comando abaixo e commitar o
relatório gerado.

## Como gerar a evidência

```bash
export GEMINI_API_KEY_INGEST=...
python3 tools/run_evals_ingest.py --comparar \
  --modelo gemini-3.5-flash-lite \
  --modelo-forte gemini-3.5-flash
```

O runner copia o repositório para um diretório temporário, roda as fixtures
`evals/ingest/I01.json` … `I14.json` e grava
`evals/resultados/ingest-<timestamp>.json` e `.md`. Ele nunca toca em Supabase
nem no GitHub e não imprime texto bruto nem nomes.

O relatório registra explicitamente:

- `comparacao`: `true` quando mais de um modelo foi executado;
- `modelos`: aprovados, total e casos por modelo;
- `modelo_escolhido` e `modelo_ingest_recomendado`;
- `decisao`: a recomendação em texto (ou a manutenção do modelo atual quando
  nenhum passou).

## Como escolher

1. Rode a comparação com o modelo leve atual e um modelo mais forte.
2. O escolhido é o **primeiro modelo da lista, do mais barato para o mais
   caro, que passa em todos os casos**. Se o leve passar, ele fica.
3. Se nenhum passar em todos os casos, `modelo_escolhido` fica `null` e
   `MODELO_INGEST` não muda.
4. Registre a escolha em `config_ingestao.MODELO_INGEST` com um commit em
   português e cite o relatório no corpo do commit ou do PR.

## Limites

- A comparação é manual e não roda em CI (§13.3).
- Duplicata e falha de lint após três tentativas não são casos de qualidade de
  modelo; ficam na suíte determinística.
- Um relatório de uma rodada de um modelo só prova que aquele modelo passou;
  ele não é uma comparação entre modelos.
