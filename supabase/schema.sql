-- Histórico compartilhado do Oráculo do CAEX.
-- Rode uma vez no SQL Editor do Supabase.

-- Uma linha por conversa
create table chats (
  id uuid primary key default gen_random_uuid(),
  titulo text not null check (char_length(titulo) between 3 and 500),
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);

-- Uma linha por pergunta + resposta
create table mensagens (
  id bigint generated always as identity primary key,
  chat_id uuid not null references chats(id) on delete cascade,
  pergunta text not null check (char_length(pergunta) between 3 and 500),
  resposta text not null,
  fontes jsonb not null default '[]',
  citacoes_invalidas jsonb not null default '[]',
  criado_em timestamptz not null default now()
);

create index mensagens_por_chat on mensagens (chat_id, criado_em);

-- Quando entra uma mensagem nova, o chat sobe para o topo do histórico
create function tocar_chat() returns trigger language plpgsql as $$
begin
  update chats set atualizado_em = now() where id = new.chat_id;
  return new;
end $$;

create trigger mensagem_nova after insert on mensagens
for each row execute function tocar_chat();

-- Sem políticas: só a chave secreta, usada pela API, acessa as tabelas
alter table chats enable row level security;
alter table mensagens enable row level security;

-- Versão completa da pergunta, quando ela dependia das anteriores (passo de reescrita)
alter table mensagens add column pergunta_interpretada text check (char_length(pergunta_interpretada) <= 500);
