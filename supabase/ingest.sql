-- Ingestão de fontes: jobs, fila, limites, eventos e hashes.
-- Rode uma vez no SQL Editor do Supabase, depois de `supabase/schema.sql`.

-- Uma linha por envio. O UUID é a capacidade de acompanhamento.
create table ingest_jobs (
  id uuid primary key default gen_random_uuid(),
  source_id text not null unique
    check (source_id ~ '^(ATA|GUIA|REL)-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9A-F]{6,10}$'),
  status text not null default 'na_fila'
    check (status in ('na_fila','processando','aguardando_revisao','publicado',
                      'fechado_sem_merge','sem_mudancas','quarentena','falhou','expirado')),
  motivo text
    check (motivo in ('classificacao','compilacao','schema','lint','pii',
                      'dispatch','timeout','infra') or motivo is null),
  etapa text
    check (etapa in ('triagem','compilando','validando','abrindo_pr') or etapa is null),
  tipo text not null check (tipo in ('ata','guia','outro')),
  origem text not null default 'web' check (origem in ('web','registro')),
  titulo text not null check (char_length(titulo) between 3 and 200),
  data_documento date not null,
  conteudo_bruto text,
  hash_conteudo text not null check (hash_conteudo ~ '^[0-9a-f]{64}$'),
  nomes_detectados jsonb,
  sinais_injecao jsonb,
  liberado_pela_gestao boolean not null default false,
  tentativas int not null default 0 check (tentativas >= 0),
  falhas_dispatch int not null default 0 check (falhas_dispatch >= 0),
  ultima_tentativa timestamptz,
  pr_numero int unique,
  pr_url text,
  branch text,
  paginas_afetadas jsonb not null default '[]'::jsonb,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  publicado_em timestamptz,
  fechado_em timestamptz,
  expira_em timestamptz not null
);

create index ingest_jobs_hash_idx on ingest_jobs (hash_conteudo);
-- Só um envio bloqueante por conteúdo. A corrida entre dois envios iguais é
-- resolvida por este índice, não por consulta prévia.
create unique index ingest_jobs_hash_bloqueante
  on ingest_jobs (hash_conteudo)
  where status in ('na_fila','processando','aguardando_revisao',
                   'publicado','sem_mudancas','quarentena');

-- Uma linha só, com o lock da fila.
create table ingest_estado (
  id smallint primary key default 1 check (id = 1),
  job_id uuid references ingest_jobs(id) on delete set null,
  pr_numero int,
  pr_url text,
  aberto_em timestamptz,
  ultima_tentativa timestamptz
);
insert into ingest_estado (id) values (1) on conflict do nothing;

-- Limites atômicos por IP (3/dia), global (INGEST_MAX_DIA/dia) e status
-- (60/minuto por IP). Nenhum texto bruto é guardado aqui.
create table limites_ingest (
  chave text primary key,
  dia date not null,
  contagem int not null default 0 check (contagem >= 0),
  expira_em timestamptz not null
);
create index limites_ingest_expira_idx on limites_ingest (expira_em);

-- Eventos curtos do job. Nunca guarda texto bruto, nomes ou IP.
create table ingest_eventos (
  id bigint generated always as identity primary key,
  job_id uuid not null references ingest_jobs(id) on delete cascade,
  evento text not null,
  detalhe jsonb not null default '{}'::jsonb,
  criado_em timestamptz not null default now()
);
create index ingest_eventos_job_idx on ingest_eventos (job_id, criado_em);

-- Último resultado terminal de cada hash. Sem PII e sem texto; mantém a
-- detecção de duplicata depois que as linhas de ingest_jobs são apagadas.
create table ingest_hashes (
  hash_conteudo text primary key check (hash_conteudo ~ '^[0-9a-f]{64}$'),
  source_id text not null,
  resultado text not null,
  atualizado_em timestamptz not null default now()
);

-- RLS ligada em todas as tabelas, sem nenhuma policy pública.
alter table ingest_jobs enable row level security;
alter table ingest_estado enable row level security;
alter table limites_ingest enable row level security;
alter table ingest_eventos enable row level security;
alter table ingest_hashes enable row level security;

-- Lock da fila, com intervalo de 30 segundos. Retomar o lock do mesmo job
-- não apaga `aberto_em`, `pr_numero` nem `pr_url`.
create or replace function tomar_lock_fila(p_job_id uuid, p_intervalo_segundos int)
returns boolean language plpgsql as $$
begin
  update ingest_estado
     set job_id = p_job_id,
         ultima_tentativa = now()
   where id = 1
     and (job_id is null or job_id = p_job_id)
     and (ultima_tentativa is null
          or ultima_tentativa < now() - make_interval(secs => p_intervalo_segundos));
  return found;
end;
$$;

create or replace function liberar_lock_fila(p_job_id uuid)
returns void language sql as $$
  update ingest_estado
     set job_id = null, aberto_em = null, pr_numero = null, pr_url = null
   where id = 1 and job_id = p_job_id;
$$;

create or replace function marcar_pr_aberto(p_job_id uuid, p_pr_numero int, p_pr_url text)
returns void language sql as $$
  update ingest_estado
     set pr_numero = p_pr_numero, pr_url = p_pr_url, aberto_em = now()
   where id = 1 and job_id = p_job_id;
$$;

-- Limite atômico. Não devolve linha quando o limite já foi atingido.
create or replace function consumir_limite(
  p_chave text,
  p_dia date,
  p_limite int,
  p_expira_em timestamptz
) returns int language sql as $$
  insert into limites_ingest (chave, dia, contagem, expira_em)
  values (p_chave, p_dia, 1, p_expira_em)
  on conflict (chave) do update
    set contagem = limites_ingest.contagem + 1
    where limites_ingest.contagem < p_limite
  returning contagem;
$$;

create or replace function consumir_ip(
  p_chave text, p_dia date, p_limite int, p_expira_em timestamptz
) returns int language sql as $$
  select consumir_limite(p_chave, p_dia, p_limite, p_expira_em);
$$;

create or replace function consumir_global(
  p_chave text, p_dia date, p_limite int, p_expira_em timestamptz
) returns int language sql as $$
  select consumir_limite(p_chave, p_dia, p_limite, p_expira_em);
$$;

-- Compensa o limite global quando o job não chega a ser criado (falha ou
-- corrida de duplicata). Nunca deixa a contagem negativa.
create or replace function devolver_global(p_chave text, p_dia date)
returns int language sql as $$
  update limites_ingest
     set contagem = greatest(contagem - 1, 0)
   where chave = p_chave and dia = p_dia
  returning contagem;
$$;
