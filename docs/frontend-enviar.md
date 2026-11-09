# Frontend de envio de fontes

As páginas públicas de envio usam o mesmo desenho do oráculo e da revisão:
`static/enviar.html` é servido por `GET /enviar` e `GET /enviar/{uuid}`, sempre
com `Cache-Control: no-cache`. O navegador lê o UUID do caminho; o mesmo HTML
mostra o formulário em `/enviar` e o andamento em `/enviar/<uuid>`.

## Formulário (`/enviar`)

- Título “Adicionar fonte à wiki”, introdução e os quatro passos do fluxo.
- Tipo de documento: Ata (`ata`), Guia (`guia`) ou Relato (`outro`).
- Título, data do documento (`type="date"`) e texto da fonte.
- Arquivo `.md`/`.txt` opcional, lido no navegador com `File.text()` e enviado
  como texto. O limite é `max_bytes` de `GET /api/enviar/config` (50 KB,
  `51200` bytes) e também é conferido antes do envio.
- Consentimento obrigatório e dica contra dados pessoais.
- Botão “Enviar fonte”.

`GET /api/enviar/config` decide o estado da página. Com `aberto: false`, a
página mostra “Envio fechado para testes.” e mantém o formulário escondido.
Com `aberto: true`, o formulário aparece. O widget e as chaves do Turnstile
ficam para o ticket #9; a página envia `turnstile_token` vazio até lá.

No sucesso (`201`), o formulário some e a página mostra “Fonte recebida.” com
um link para `/enviar/<uuid>`. O link é compartilhável e o UUID funciona como
capacidade de acesso: quem tiver o endereço acompanha o envio.

### Mensagens de erro exibidas

A página mostra `detail` da API, que segue a especificação §6.2:

| Situação | HTTP | Mensagem |
|---|---|---|
| Fechado | `503` | “O envio está fechado para testes.” |
| Chaves do Turnstile ausentes | `503` | “O envio está fechado para testes.” |
| PII | `422` | “Remova dados pessoais (CPF, RA, telefone ou e-mail pessoal) e envie de novo.” |
| Duplicata | `409` | “Esta fonte já foi registrada como {source_id}.” |
| Limite por IP | `429` | “Limite de 3 envios por dia. Tente novamente amanhã.” |
| Limite global | `429` | “A fila de envios está cheia hoje. Tente novamente amanhã.” |
| Campo inválido | `422` | Mensagem específica do campo. |
| Turnstile | `403` | “Não foi possível verificar o navegador. Tente novamente.” |

## Andamento (`/enviar/<uuid>`)

A página consulta `GET /api/ingest/{uuid}`:

- a cada 5 s enquanto o estado é `processando`;
- a cada 15 s enquanto o estado é `na_fila` ou `aguardando_revisao`;
- para nos estados terminais: `publicado`, `fechado_sem_merge`,
  `sem_mudancas`, `quarentena`, `falhou` e `expirado`.

Mostra a mensagem do estado, a posição na fila (quando existir), o tempo
decorrido desde `criado_em`, o `source_id`, o link do Pull Request quando
`pr_url` começa com `https://` e o botão “Atualizar” depois de um estado
terminal. `404` exibe “Este envio não está mais disponível.” e encerra a
consulta.

### Mensagens por estado

| Estado | Mensagem |
|---|---|
| `na_fila` | “Na fila. Posição {n}. A espera depende da revisão da gestão.” |
| `processando` | “Processando: {etapa em português}.” |
| `aguardando_revisao` | “Proposta enviada para revisão da gestão.” |
| `publicado` | “Publicado após revisão da gestão.” |
| `fechado_sem_merge` | “A gestão encerrou a revisão sem publicar.” |
| `sem_mudancas` | “Nenhum fato novo encontrado. Nada foi publicado.” |
| `quarentena` | “A fonte foi enviada para revisão da gestão. Nenhuma página foi alterada.” |
| `falhou` | “Não foi possível concluir. A gestão foi avisada.” |
| `falhou` com `motivo: pii` | “Remova nomes de pessoas e reenvie.” |
| `expirado` | “Este envio expirou. Envie a fonte novamente.” |

As etapas em português são `triagem`, `compilando a proposta`,
`validando a proposta` e `abrindo o Pull Request`.

## Segurança e acessibilidade

- O texto da API nunca vai para `innerHTML`; a página usa `textContent`,
  `createElement` e `setAttribute`. O link do PR só é criado para `https://`.
- O título recebe foco (`tabindex="-1"`) depois de sucesso, erro e mudança de
  estado, e a mensagem de andamento usa `aria-live="polite"`.
- Campos têm `label`, o erro usa `role="alert"` e os controles são botões,
  links e campos nativos, acessíveis por teclado.
- O UUID não aparece em logs, commits ou notificações; só no caminho da
  página de andamento.

## Checklist manual dos estados visuais

Com `python3 -m uvicorn app:app --reload`, abra as páginas e confira:

- [ ] `/enviar` com `INGEST_ABERTO=false`: mostra apenas “Envio fechado para
  testes.” e nenhum campo.
- [ ] `/enviar` com `INGEST_ABERTO=true`: mostra nav, título, introdução, os
  quatro passos, todos os campos, consentimento, botão e dica de PII.
- [ ] Sem consentimento ou com campo vazio: o navegador aponta o campo e nada
  é enviado.
- [ ] Arquivo `.md` pequeno: o texto aparece na área de texto e a mensagem
  confirma o carregamento.
- [ ] Arquivo maior que 50 KB ou com extensão diferente: erro claro e nenhum
  envio.
- [ ] Envio aceito: aparece “Fonte recebida.” e o link para
  `/enviar/<uuid>`.
- [ ] Envio com PII, duplicata, limite por IP, limite global, campo inválido
  ou Turnstile: cada mensagem da tabela aparece sem `innerHTML` e sem sumir
  com o texto digitado.
- [ ] `/enviar/<uuid>` em `na_fila`: mostra posição, tempo e mensagem; a
  consulta se repete a cada 15 s.
- [ ] `/enviar/<uuid>` em `processando`: mostra a etapa em português e
  consulta a cada 5 s.
- [ ] Estado terminal: consulta para, aparece “Atualizar” e o link do PR,
  quando houver.
- [ ] UUID inexistente ou removido: aparece “Este envio não está mais
  disponível.” e a consulta para.
- [ ] Com teclado: é possível percorrer e acionar todos os controles, e o
  foco vai para o título após cada mudança de estado.
- [ ] Com leitor de tela: a mensagem de andamento é anunciada sem interromper.
- [ ] Tema claro e escuro mantêm contraste e não quebram o layout no celular.
