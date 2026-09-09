-- Run only against an isolated Supabase project. The transaction rolls back all
-- synthetic content. Capture each SELECT result in the capacity review artifact.
begin;
create temporary table collaboration_capacity_samples (
  fixture text primary key, messages integer not null, utf8_bytes bigint not null,
  table_bytes bigint not null, index_bytes bigint not null, total_bytes bigint not null
);
create temporary table collaboration_capacity_fixture (
  event_id uuid primary key, session_id uuid not null, seq bigint not null,
  kind text not null, message_id uuid not null, author_id uuid not null,
  role text not null, content text, revision integer not null, created_at timestamptz not null default now(),
  unique(session_id,seq)
);
do $fixtures$
declare n integer; label text; sid uuid; before_total bigint; after_total bigint; payload text;
begin
  foreach n in array array[40,200,1000] loop
    label:=n::text||'_messages'; sid:=gen_random_uuid();
    before_total:=pg_total_relation_size('pg_temp.collaboration_capacity_fixture');
    insert into collaboration_capacity_fixture(event_id,session_id,seq,kind,message_id,author_id,role,content,revision)
    select gen_random_uuid(),sid,g,'message',gen_random_uuid(),gen_random_uuid(),case when g%2=0 then 'assistant' else 'user' end,
      repeat(case when g%3=0 then '你好 🐕 résumé ' else 'bounded response ' end,64),1 from generate_series(1,n) g;
    -- Retained edit and delete events exercise revision/index growth.
    insert into collaboration_capacity_fixture(event_id,session_id,seq,kind,message_id,author_id,role,content,revision)
    select gen_random_uuid(),sid,n+row_number() over(order by seq),'edit',message_id,author_id,role,content||' edited',2 from collaboration_capacity_fixture where session_id=sid and seq<=greatest(1,n/10);
    insert into collaboration_capacity_fixture(event_id,session_id,seq,kind,message_id,author_id,role,content,revision)
    select gen_random_uuid(),sid,n+(n/10)+row_number() over(order by seq),'delete',message_id,author_id,role,null,3 from collaboration_capacity_fixture where session_id=sid and seq<=greatest(1,n/20);
    after_total:=pg_total_relation_size('pg_temp.collaboration_capacity_fixture');
    select string_agg(coalesce(content,''),'') into payload from collaboration_capacity_fixture where session_id=sid;
    insert into collaboration_capacity_samples values(label,n,octet_length(convert_to(payload,'UTF8')),
      pg_table_size('pg_temp.collaboration_capacity_fixture'),pg_indexes_size('pg_temp.collaboration_capacity_fixture'),after_total-before_total);
  end loop;
end $fixtures$;
select jsonb_build_object(
  'samples',(select jsonb_agg(to_jsonb(sample) order by messages) from (
    select fixture,messages,utf8_bytes,table_bytes,index_bytes,total_bytes,
      round(total_bytes::numeric/greatest(utf8_bytes,1),2) as physical_to_payload_factor
    from collaboration_capacity_samples
  ) sample),
  'database_bytes',pg_database_size(current_database()),
  'collaboration_bytes',coalesce((select sum(pg_total_relation_size(format('%I.%I',schemaname,tablename)::regclass)) from pg_tables where schemaname='public' and tablename like 'collaboration_%'),0),
  'measured_at',now()
) as capacity_calibration;
rollback;
