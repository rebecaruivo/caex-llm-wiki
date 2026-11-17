# Automação no Make: resumo semanal para a gestão

Toda segunda-feira às 8h (horário de Brasília), um cenário no [Make](https://www.make.com) monta e envia por e-mail um resumo da semana do Oráculo do CAEX para a gestão do CA. Ele roda no plano gratuito.

O cenário vive na conta do Make, não neste repositório. Este documento registra como ele é montado, para que possa ser recriado.

## O que chega no e-mail

| Seção | Quem escreve | Conteúdo |
|---|---|---|
| Avisos da semana | Código | Datas citadas na wiki para os próximos 7 dias, com o dia por extenso |
| Atas e documentos da semana | Código | Publicados (com **as linhas que cada ata trouxe**, por página), aguardando revisão (com dias de espera), sem mudanças, não concluídos (com o motivo em linguagem simples) |
| Avaliações das respostas | Código | Quantas respostas foram úteis e quais perguntas tiveram a resposta marcada com erro |
| Pendências | Código | Linhas P-xx abertas e documentos que a gestão já sabe que faltam |
| Uso do oráculo | Gemini | Perguntas, com fonte, sem fonte e do cache, em um parágrafo |
| O que a wiki ainda não sabe | Gemini | Lacunas agrupadas por tema, da mais pedida para a menos pedida, cada uma com uma sugestão de ata ou documento |

**O código monta os fatos, a IA interpreta.** Listas de títulos, motivos, datas e contagens saem prontas em HTML da API, porque um modelo pode esquecer um item ou reescrever uma data. O Gemini recebe só o que precisa interpretar: as perguntas sem fonte, para agrupar por tema e sugerir ações.

## Como funciona

```text
Agendamento (seg 8h) → HTTP: GET /api/relatorio/semanal → Gemini 2.5 Flash → Gmail
                         (Bearer RELATORIO_TOKEN)          (só o campo para_ia)   (html_fatos + análise)
```

| # | Módulo do Make | Configuração |
|---|---|---|
| 1 | HTTP · Make a request | `GET https://caex-llm-wiki.vercel.app/api/relatorio/semanal?dias=7`, cabeçalho `Authorization: Bearer <RELATORIO_TOKEN>`, **Parse response** ligado |
| 2 | Google Gemini AI · Simple text prompt | Modelo `gemini-2.5-flash`, pelo provedor de IA do próprio Make (sem chave de API). Prompt abaixo, com `{{1.data.para_ia}}` entre marcas `<dados>` |
| 3 | Gmail · Send an email | Corpo em HTML: `{{1.data.html_fatos}}` seguido da resposta do Gemini e de um rodapé |

### A rota `/api/relatorio/semanal`

Código em [`relatorio.py`](../relatorio.py) (lógica pura, testada em `tests/test_relatorio.py`) e na rota de `app.py`.

- **Protegida por `RELATORIO_TOKEN`**, um segredo separado do `CRON_SECRET`: quem lê o relatório não consegue disparar a manutenção. Há um teste que garante isso.
- **Devolve** contagens de uso, lacunas, envios agrupados, pendências, prazos, `para_ia` (o JSON que vai para o Gemini) e `html_fatos` (as seções factuais já escapadas).
- **Não devolve** respostas do oráculo, ids de chat ou de envio. Títulos de envios passam pela mesma regra de nomes do ingest (`padrao_nome`).
- **"O que mudou"** usa as citações: toda linha gravada pelo ingest cita a ata de origem, então as linhas com `[fonte: <id>]` são exatamente o que aquela ata mudou.
- `?hoje=AAAA-MM-DD` muda só a janela dos avisos. Serve para testar com a wiki fictícia, datada em 2030.

### O prompt do Gemini

Ele aplica as mesmas regras do compilador do ingest:

- **Dados não são instruções.** As perguntas entram entre `<dados>` e `</dados>`, e o modelo é instruído a ignorar qualquer comando dentro delas. Uma pergunta como "ignore as regras" digitada no chat não muda o e-mail.
- **Nada inventado:** só números e perguntas presentes nos dados.
- **Sem nomes de pessoas:** quem for citado vira "uma pessoa".
- **Ruído não é lacuna:** cumprimentos e testes não contam como tema.
- **Não repete as listas** que o código já montou.
- **Formato fixo:** um fragmento HTML com duas seções.

## Como recriar

1. Gere um token: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Na Vercel, crie a variável `RELATORIO_TOKEN` com esse valor e faça um **Redeploy**.
3. No Make, crie a conexão do Gmail (**Connections → Create a connection → Gmail → Sign in with Google**). Contas pessoais `@gmail.com` precisam reautorizar a cada 6 meses.
4. Monte os três módulos da tabela acima e cole o token **direto no Make**, nunca num chat ou no repositório.
5. Use **Run once** para testar. Atenção: ele executa a versão aberta no editor, mesmo sem salvar.
6. Ligue o agendamento semanal.

## Custo

Cada execução gasta poucos créditos (cerca de 3 operações mais a chamada ao Gemini). O plano gratuito tem 1.000 créditos por mês e 2 cenários ativos; um resumo por semana fica muito abaixo disso.

## Limites conhecidos

- Resposta sem fonte conta como lacuna. O prompt descarta cumprimentos, mas a contagem `sem_fonte` os inclui.
- O relatório mede se havia fonte, não se a resposta estava certa. Para isso existem as avaliações ("Útil" e "Informar erro").
- Chats apagados saem do relatório, porque qualquer pessoa pode apagar um chat.
- PRs ainda em revisão não mostram as mudanças, só o link: elas só entram na wiki publicada depois do merge.
