-- Avaliação das respostas do oráculo pelo CA: útil, erro ou lacuna.
-- Rode no SQL Editor do Supabase ANTES de publicar o código que lê estas colunas.
alter table mensagens add column if not exists avaliacao text
  check (avaliacao in ('positiva', 'erro', 'lacuna'));
alter table mensagens add column if not exists avaliado_em timestamptz;
