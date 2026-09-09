#!/usr/bin/env node
/**
 * Runtime validation for the shared-session migration using an isolated PGlite DB.
 *
 * Run from the repository root:
 *   node tools/test_collaboration_backend.mjs
 *
 * PGlite 0.3.14 does not ship pgcrypto. The loader below replaces only
 * `create extension pgcrypto`, maps digest(..., 'sha256') to PGlite's core
 * sha256(bytea), and supplies deterministic random bytes for link-code tests.
 * These tests validate SQL authorization, constraints, transactionality, quota,
 * identity binding, archive receipts, and purge gates; they do not validate the
 * cryptographic implementation supplied by hosted PostgreSQL/Supabase.
 */
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { PGlite } from '../.tmp/collaboration-validation/node_modules/@electric-sql/pglite/dist/index.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const migrationsDir = resolve(root, 'supabase/migrations');

const ids = {
  alice: '00000000-0000-4000-8000-000000000001',
  bob: '00000000-0000-4000-8000-000000000002',
  mallory: '00000000-0000-4000-8000-000000000003',
  orgA: '10000000-0000-4000-8000-000000000001',
  orgB: '10000000-0000-4000-8000-000000000002',
  sessionA: '20000000-0000-4000-8000-000000000001',
  sessionArchive: '20000000-0000-4000-8000-000000000002',
  sessionStale: '20000000-0000-4000-8000-000000000003',
  archiveFile: '25000000-0000-4000-8000-000000000001',
  archiveFileReservation: '26000000-0000-4000-8000-000000000001',
  lifecycleFile: '25000000-0000-4000-8000-000000000002',
  eventA: '30000000-0000-4000-8000-000000000001',
  messageA: '40000000-0000-4000-8000-000000000001',
  runA: '50000000-0000-4000-8000-000000000001',
  reservationA: '60000000-0000-4000-8000-000000000001',
  aliceDevice: '70000000-0000-4000-8000-000000000001',
  bobDevice: '70000000-0000-4000-8000-000000000002',
  worker: '80000000-0000-4000-8000-000000000001',
};

const db = new PGlite();
let currentStep = 'startup';

async function sql(text, params = []) {
  return db.query(text, params);
}

async function scalar(text, params = []) {
  const result = await sql(text, params);
  return Object.values(result.rows[0] ?? {})[0];
}

async function asActor(actor, text, params = []) {
  await sql("select set_config('request.jwt.claim.sub',$1,false)", [actor]);
  await sql('set role authenticated');
  try {
    return await sql(text, params);
  } finally {
    await sql('reset role');
  }
}

async function command(actor, name, payload) {
  currentStep = `actor ${actor} command ${name}`;
  const result = await asActor(actor, 'select public.collaboration_command($1,$2::jsonb) as value', [name, JSON.stringify(payload)]);
  return result.rows[0].value;
}

async function maintenanceCommand(name, payload) {
  currentStep = `maintenance command ${name}`;
  await sql('set role service_role');
  try {
    const result = await sql('select public.collaboration_maintenance_command($1,$2::jsonb) as value', [name, JSON.stringify(payload)]);
    return result.rows[0].value;
  } finally {
    await sql('reset role');
  }
}

async function rolloutCommand(name, payload) {
  currentStep = `rollout command ${name}`;
  await sql('set role service_role');
  try {
    const result = await sql('select public.collaboration_rollout_command($1,$2::jsonb) as value', [name, JSON.stringify(payload)]);
    return result.rows[0].value;
  } finally {
    await sql('reset role');
  }
}

async function filesCommand(actor, name, payload) {
  currentStep = `actor ${actor} files command ${name}`;
  const result = await asActor(actor, 'select public.collaboration_files_command($1,$2::jsonb) as value', [name, JSON.stringify(payload)]);
  return result.rows[0].value;
}

async function verifyFile(fileId, byteLength, sha256) {
  currentStep = `service verify file ${fileId}`;
  await sql('set role service_role');
  try {
    const result = await sql('select public.collaboration_files_verify($1,$2,$3) as value', [fileId, byteLength, sha256]);
    return result.rows[0].value;
  } finally {
    await sql('reset role');
  }
}

async function slackCommand(name, payload) {
  currentStep = `slack command ${name}`;
  await sql('set role service_role');
  try {
    const result = await sql('select public.collaboration_slack_command($1,$2::jsonb) as value', [name, JSON.stringify(payload)]);
    return result.rows[0].value;
  } finally {
    await sql('reset role');
  }
}

async function rejectsCode(operation, code) {
  await assert.rejects(operation, error => {
    assert.match(String(error?.message ?? error), new RegExp(`\\b${code}\\b`));
    return true;
  });
}

async function installMigration() {
  await db.exec(`
    create role anon nologin;
    create role authenticated nologin;
    create role service_role nologin bypassrls;
    create schema auth;
    create table auth.users(id uuid primary key);
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
    $$;
    create schema storage;
    create table storage.buckets(id text primary key, name text not null, public boolean not null default false, file_size_limit bigint);
    create table storage.objects(id uuid primary key default gen_random_uuid(), bucket_id text not null, name text not null);
    grant usage on schema auth, storage, public to anon, authenticated, service_role;
    grant select, insert on storage.objects to authenticated;
  `);
  const names = (await readdir(migrationsDir))
    .filter(name => /shared.*\.sql$/i.test(name))
    .sort();
  assert.ok(names.length > 0, 'expected at least one shared-session migration');
  for (const name of names) {
    let migration = await readFile(resolve(migrationsDir, name), 'utf8');
    migration = migration.replace(/create\s+extension\s+(?:if\s+not\s+exists\s+)?pgcrypto(?:\s+with\s+schema\s+\w+)?\s*;/ig, () => `
    create schema if not exists extensions;
    create or replace function extensions.digest(value bytea, algorithm text) returns bytea
    language sql immutable strict as $$
      select case lower(algorithm) when 'sha256' then sha256(value)
        else null end
    $$;
    create or replace function extensions.gen_random_bytes(length integer) returns bytea
    language sql volatile strict as $$
      select decode(repeat('ab', length), 'hex')
    $$;
    `);
    try {
      await db.exec(migration);
    } catch (error) {
      error.migrationName = name;
      throw error;
    }
  }
  await sql('insert into auth.users(id) values ($1),($2),($3)', [ids.alice, ids.bob, ids.mallory]);
  for (const userId of [ids.alice, ids.bob, ids.mallory]) {
    await rolloutCommand('set_user', { user_id: userId, enabled: true });
  }
  await sql(`update public.collaboration_config set
    shared_text_enabled=true, shared_execution_enabled=true, archive_enabled=true,
    session_warn_bytes=3000, session_limit_bytes=4096, org_limit_bytes=12000,
    default_response_reservation_bytes=700`);
  await sql(`insert into public.collaboration_capacity_measurements
    (database_bytes,collaboration_bytes,storage_bytes) values(0,0,0)`);
}

async function createOrg(actor, orgId, name) {
  const result = await command(actor, 'create_organization', { org_id: orgId, name, display_name: actor === ids.alice ? 'Alice' : 'Mallory' });
  await rolloutCommand('set_organization', { org_id: orgId, enabled: true });
  return result;
}

async function addOrgMember(orgId, userId, displayName) {
  await sql(`insert into public.collaboration_org_members(org_id,user_id,display_name,role)
    values($1,$2,$3,'member')`, [orgId, userId, displayName]);
}

async function createSession(actor, orgId, sessionId, title = 'validation') {
  return command(actor, 'create_session', { org_id: orgId, session_id: sessionId, title });
}

async function inviteAndAccept(sessionId, invitee, suffix) {
  const inviteId = `90000000-0000-4000-8000-${suffix.padStart(12, '0')}`;
  await rejectsCode(command(ids.alice, 'invite', { session_id: sessionId, invite_id: inviteId, user_id: invitee }),
    'AUDIENCE_POLICY_REQUIRED');
  await command(ids.alice, 'invite', { session_id: sessionId, invite_id: inviteId, user_id: invitee,
    audience_policy: { summary: 'Owners may invite additional participants who can read all published history.', includes_existing_history: true },
    audience_policy_version: 1 });
  const pending = (await command(invitee, 'bootstrap', {})).invites.find(item => item.invite_id === inviteId);
  assert.equal(pending.consent_required, true);
  assert.equal(pending.audience_policy_version, 1);
  await rejectsCode(command(invitee, 'accept_invite', { invite_id: inviteId }), 'AUDIENCE_CONSENT_REQUIRED');
  await command(invitee, 'accept_invite', { invite_id: inviteId, audience_consent: true, audience_policy_version: 1 });
}

async function append(actor, sessionId, eventId, messageId, extra = {}) {
  return command(actor, 'append_message', {
    session_id: sessionId,
    event_id: eventId,
    message_id: messageId,
    content: 'hello',
    role: 'user',
    ...extra,
  });
}

async function testAuthorizationAndDedup() {
  await createOrg(ids.alice, ids.orgA, 'Org A');
  await addOrgMember(ids.orgA, ids.bob, 'Bob');
  await createOrg(ids.mallory, ids.orgB, 'Org B');
  await createSession(ids.alice, ids.orgA, ids.sessionA);
  await inviteAndAccept(ids.sessionA, ids.bob, '1');

  await sql('delete from public.collaboration_session_audience_consents where session_id=$1 and user_id=$2', [ids.sessionA, ids.bob]);
  await rejectsCode(command(ids.alice, 'invite', {
    session_id: ids.sessionA, invite_id: '90000000-0000-4000-8000-000000000098', user_id: ids.mallory,
    audience_policy: { summary: 'Owners may invite additional participants who can read all published history.', includes_existing_history: true },
    audience_policy_version: 1,
  }), 'AUDIENCE_CONSENT_REQUIRED');
  await sql(`insert into public.collaboration_session_audience_consents(session_id,user_id,policy_version)
    values($1,$2,1)`, [ids.sessionA, ids.bob]);

  await rejectsCode(command(ids.mallory, 'read_events', { session_id: ids.sessionA }), 'FORBIDDEN');
  await rejectsCode(append(ids.mallory, ids.sessionA, '30000000-0000-4000-8000-000000000099', '40000000-0000-4000-8000-000000000099'), 'FORBIDDEN');
  await rejectsCode(command(ids.alice, 'invite', {
    session_id: ids.sessionA, invite_id: '90000000-0000-4000-8000-000000000099', user_id: ids.mallory,
    audience_policy: { summary: 'Owners may invite additional participants who can read all published history.', includes_existing_history: true },
    audience_policy_version: 1,
  }), 'foreign key');

  const first = await append(ids.alice, ids.sessionA, ids.eventA, ids.messageA, {
    author_id: ids.bob, mentioned_user_ids: [ids.bob, ids.mallory],
  });
  assert.equal(first.seq, 1);
  const author = await scalar('select author_id from public.collaboration_events where event_id=$1', [ids.eventA]);
  assert.equal(author, ids.alice, 'the command must derive event authorship from authentication');
  assert.equal((await append(ids.alice, ids.sessionA, ids.eventA, ids.messageA, {
    author_id: ids.bob, mentioned_user_ids: [ids.bob, ids.mallory],
  })).deduplicated, true);
  await rejectsCode(append(ids.alice, ids.sessionA, ids.eventA, ids.messageA, { content: 'changed payload' }), 'IDEMPOTENCY_CONFLICT');
  await rejectsCode(append(ids.alice, ids.sessionA, '30000000-0000-4000-8000-000000000098', '40000000-0000-4000-8000-000000000098', { role: 'assistant' }), 'FORBIDDEN');

  const notifications = await command(ids.bob, 'notifications', { cursor: 0, limit: 20 });
  assert.deepEqual(notifications.notifications.map(item => item.kind), ['session_invite', 'mention']);
  assert.equal(notifications.notifications[1].event_id, ids.eventA);
  assert.equal(notifications.notifications.some(item => item.actor_id !== ids.alice), false);
  await command(ids.bob, 'ack_notifications', { through: notifications.high_water });
  assert.equal(Number(await scalar(`select count(*) from public.collaboration_notifications
    where recipient_id=$1 and read_at is null`, [ids.bob])), 0);
  assert.equal((await command(ids.mallory, 'notifications', { cursor: 0 })).notifications.length, 0,
    'mentions must not notify directory members outside the session');

  const hidden = await asActor(ids.mallory, 'select count(*)::int as n from public.collaboration_events where session_id=$1', [ids.sessionA]);
  assert.equal(hidden.rows[0].n, 0, 'RLS must hide another session');
  await rejectsCode(asActor(ids.alice, `insert into public.collaboration_events
    (event_id,org_id,session_id,seq,kind,message_id,author_id,role,content,revision,logical_bytes,request_hash)
    values(gen_random_uuid(),$1,$2,99,'message',gen_random_uuid(),$3,'user','forged',1,1,'x')`,
    [ids.orgA, ids.sessionA, ids.bob]), 'permission denied');
}

async function testQuotaReservationsAndRequesterClaims() {
  // A committed reservation is visible to the next writer, modeling the serialized
  // result guaranteed by the quota-counter/session row locks under a real race.
  const accepted = await append(ids.alice, ids.sessionA,
    '30000000-0000-4000-8000-000000000002', '40000000-0000-4000-8000-000000000002', {
      request_run: true, run_id: ids.runA, reservation_id: ids.reservationA,
      response_reservation_bytes: 700,
    });
  assert.equal(accepted.run_id, ids.runA);
  await rejectsCode(append(ids.bob, ids.sessionA,
    '30000000-0000-4000-8000-000000000003', '40000000-0000-4000-8000-000000000003', {
      request_run: true, run_id: '50000000-0000-4000-8000-000000000002',
      reservation_id: '60000000-0000-4000-8000-000000000002', response_reservation_bytes: 3500,
    }), 'QUOTA_EXCEEDED');
  const quota = (await sql(`select reserved_bytes,archive_manifest_bytes
    from public.collaboration_sessions where session_id=$1`, [ids.sessionA])).rows[0];
  assert.equal(Number(quota.reserved_bytes) - Number(quota.archive_manifest_bytes), 700,
    'a rejected competing request must leave only the accepted response reservation');
  assert.equal(Number(await scalar(`select count(*) from public.collaboration_quota_reservations
    where session_id=$1 and kind='response' and status='active'`, [ids.sessionA])), 1);

  // A failed command transaction must not retain its event or consume logical quota.
  const before = Number(await scalar('select logical_bytes from public.collaboration_sessions where session_id=$1', [ids.sessionA]));
  await rejectsCode(append(ids.bob, ids.sessionA,
    '30000000-0000-4000-8000-000000000004', '40000000-0000-4000-8000-000000000004', {
      content: 'x'.repeat(5000), request_run: true,
    }), 'QUOTA_EXCEEDED');
  assert.equal(Number(await scalar('select logical_bytes from public.collaboration_sessions where session_id=$1', [ids.sessionA])), before);

  const enrolled = await command(ids.alice, 'enroll_device', { device_id: ids.aliceDevice, label: 'Alice laptop', public_key: 'alice-key' });
  const reenrolled = await command(ids.alice, 'enroll_device', { device_id: ids.aliceDevice, label: 'Alice laptop renamed', public_key: 'alice-key' });
  assert.equal(reenrolled.fence, enrolled.fence, 'idempotent enrollment must preserve the device fence');
  await rejectsCode(command(ids.alice, 'enroll_device', {
    device_id: ids.aliceDevice, label: 'attacker replacement', public_key: 'different-key',
  }), 'DEVICE_KEY_MISMATCH');
  await command(ids.bob, 'enroll_device', { device_id: ids.bobDevice, label: 'Bob laptop', public_key: 'bob-key' });
  await rolloutCommand('set_organization', { org_id: ids.orgA, enabled: false });
  await rejectsCode(command(ids.alice, 'claim_run', {
    run_id: ids.runA, device_id: ids.aliceDevice,
    session_id: '21000000-0000-4000-8000-000000000001', org_id: ids.orgB,
  }), 'ROLLOUT_DISABLED');
  await rolloutCommand('set_organization', { org_id: ids.orgA, enabled: true });
  await rejectsCode(command(ids.bob, 'claim_run', { run_id: ids.runA, device_id: ids.bobDevice }), 'RUN_UNAVAILABLE');
  await rejectsCode(command(ids.alice, 'claim_run', { run_id: ids.runA, device_id: ids.bobDevice }), 'DEVICE_FENCED');
  const lease = await command(ids.alice, 'claim_run', { run_id: ids.runA, device_id: ids.aliceDevice });
  assert.equal(lease.run_id, ids.runA);
}

async function testSharedFileLifecycle() {
  const session = (await sql(`select revision,membership_revision from public.collaboration_sessions
    where session_id=$1`, [ids.sessionA])).rows[0];
  const payload = {
    session_id: ids.sessionA,
    file_id: ids.lifecycleFile,
    byte_length: 16,
    name: 'evidence.txt',
    content_type: 'text/plain',
    sha256: 'b'.repeat(64),
    expected_session_revision: session.revision,
    membership_revision: session.membership_revision,
  };
  await rejectsCode(filesCommand(ids.mallory, 'reserve', payload), 'FORBIDDEN');
  const reserved = await filesCommand(ids.alice, 'reserve', payload);
  assert.equal(reserved.status, 'reserved');
  assert.match(reserved.object_path, new RegExp(`${ids.alice}/${ids.lifecycleFile}/v1$`));
  assert.equal((await filesCommand(ids.alice, 'reserve', payload)).file_id, ids.lifecycleFile,
    'an identical file reservation retry must deduplicate');
  await rejectsCode(filesCommand(ids.alice, 'reserve', { ...payload, name: 'different.txt' }), 'IDEMPOTENCY_CONFLICT');
  await rejectsCode(filesCommand(ids.bob, 'authorize_upload', { file_id: ids.lifecycleFile }), 'UPLOAD_NOT_AUTHORIZED');
  await filesCommand(ids.alice, 'authorize_upload', { file_id: ids.lifecycleFile });
  const verified = await verifyFile(ids.lifecycleFile, 16, 'b'.repeat(64));
  assert.equal(verified.status, 'uploaded');
  assert.equal((await filesCommand(ids.bob, 'authorize_download', { file_id: ids.lifecycleFile })).name, 'evidence.txt');
  await rejectsCode(filesCommand(ids.mallory, 'authorize_download', { file_id: ids.lifecycleFile }), 'DOWNLOAD_NOT_AUTHORIZED');
  const hidden = await asActor(ids.mallory, 'select count(*)::int as n from public.collaboration_file_reservations where file_id=$1', [ids.lifecycleFile]);
  assert.equal(hidden.rows[0].n, 0, 'file metadata RLS must hide files from nonparticipants');
}

async function testClosingRunCanSettle() {
  const sessionId = '21000000-0000-4000-8000-000000000099';
  const eventId = '31000000-0000-4000-8000-000000000099';
  const messageId = '41000000-0000-4000-8000-000000000099';
  const runId = '51000000-0000-4000-8000-000000000099';
  const reservationId = '61000000-0000-4000-8000-000000000099';
  await createSession(ids.alice, ids.orgA, sessionId, 'closing-run');
  await append(ids.alice, sessionId, eventId, messageId, {
    request_run: true, run_id: runId, reservation_id: reservationId,
  });
  const lease = await command(ids.alice, 'claim_run', { run_id: runId, device_id: ids.aliceDevice });
  for (const field of ['session_id','requester_id','credential_owner_id','executor_device_id',
    'audience_revision','context_cutoff','run_id','lease_token','lease_expires_at']) {
    assert.notEqual(lease[field], null, `claim must include ${field}`);
    assert.notEqual(lease[field], undefined, `claim must include ${field}`);
  }
  await command(ids.alice, 'request_archive', { session_id: sessionId });
  await command(ids.alice, 'renew_run', {
    run_id: runId, device_id: ids.aliceDevice, lease_token: lease.lease_token,
  });
  await command(ids.alice, 'complete_run', {
    run_id: runId, lease_token: lease.lease_token,
    event_id: '71000000-0000-4000-8000-000000000099',
    message_id: '81000000-0000-4000-8000-000000000099', content: 'settled while closing',
  });
  assert.equal(await scalar('select status from public.collaboration_sessions where session_id=$1', [sessionId]), 'closing');
  const advanced = await maintenanceCommand('advance_archives', { limit: 10 });
  assert.ok(advanced.advanced > 0, 'archive must progress after the accepted run settles');
  const session = (await sql(`select final_seq,next_seq from public.collaboration_sessions
    where session_id=$1`, [sessionId])).rows[0];
  const row = (await sql(`select final_seq,manifest_json from public.collaboration_archive_manifests
    where session_id=$1 order by archive_revision desc limit 1`, [sessionId])).rows[0];
  const canonicalCount = Number(await scalar('select count(*) from public.collaboration_events where session_id=$1', [sessionId]));
  const manifest = JSON.parse(row.manifest_json);
  assert.equal(Number(session.final_seq), Number(session.next_seq) - 1);
  assert.equal(Number(row.final_seq), Number(session.final_seq));
  assert.equal(Number(manifest.final_seq), Number(session.final_seq));
  assert.equal(Number(manifest.final_sequence), Number(session.final_seq));
  assert.equal(manifest.events.length, canonicalCount);
  assert.equal(manifest.events.some(event => event.event_id === '71000000-0000-4000-8000-000000000099'), true,
    'the archive must contain the completion accepted while closing');
}

async function testSessionAdmissionCap() {
  for (let index = 1; index <= 5; index += 1) {
    await createSession(ids.mallory, ids.orgB, `21000000-0000-4000-8000-${String(index).padStart(12, '0')}`, `cap-${index}`);
  }
  await rejectsCode(createSession(ids.mallory, ids.orgB,
    '21000000-0000-4000-8000-000000000006', 'over-cap'), 'QUOTA_EXCEEDED');
  assert.equal(Number(await scalar(`select count(*) from public.collaboration_sessions
    where org_id=$1 and status<>'archived_local'`, [ids.orgB])), 5);
}

async function testRolloutControls() {
  await rejectsCode(asActor(ids.bob, `select collaboration_private.command_with_rollout($1,'read_events',$2::jsonb)`,
    [ids.alice, JSON.stringify({ session_id: ids.sessionA })]), 'UNAUTHENTICATED');
  await rolloutCommand('set_user', { user_id: ids.bob, enabled: false });
  await rejectsCode(append(ids.bob, ids.sessionA,
    '32000000-0000-4000-8000-000000000001', '42000000-0000-4000-8000-000000000001'), 'ROLLOUT_DISABLED');
  assert.equal((await command(ids.bob, 'read_events', { session_id: ids.sessionA, cursor: 0 })).events.length, 1,
    'rollout rollback must preserve access to existing history');
  await rolloutCommand('set_user', { user_id: ids.bob, enabled: true });
  await rolloutCommand('set_organization', { org_id: ids.orgA, enabled: false });
  await rejectsCode(append(ids.alice, ids.sessionA,
    '32000000-0000-4000-8000-000000000002', '42000000-0000-4000-8000-000000000002',
    { org_id: ids.orgB }), 'ROLLOUT_DISABLED');
  await rejectsCode(command(ids.bob, 'accept_invite', {
    invite_id: '90000000-0000-4000-8000-000000000001',
    session_id: '21000000-0000-4000-8000-000000000001', org_id: ids.orgB,
    audience_consent: true, audience_policy_version: 1,
  }), 'ROLLOUT_DISABLED');
  assert.equal((await command(ids.alice, 'read_events', { session_id: ids.sessionA, cursor: 0 })).events.length, 1);
  await rejectsCode(asActor(ids.alice, `select collaboration_private.command($1,'append_message',$2::jsonb)`,
    [ids.alice, JSON.stringify({ session_id: ids.sessionA })]), 'permission denied');
  const current = (await sql('select revision,membership_revision from public.collaboration_sessions where session_id=$1', [ids.sessionA])).rows[0];
  await rejectsCode(filesCommand(ids.alice, 'reserve', {
    session_id: ids.sessionA, file_id: '25000000-0000-4000-8000-000000000099', byte_length: 1,
    org_id: ids.orgB,
    name: 'blocked.txt', content_type: 'text/plain', sha256: 'c'.repeat(64),
    expected_session_revision: current.revision, membership_revision: current.membership_revision,
  }), 'ROLLOUT_DISABLED');
  const installation = (await sql(`insert into public.collaboration_slack_installations(org_id,team_id,bot_user_id,token_ciphertext)
    values($1,'T-rollout','B-rollout','ciphertext') returning installation_id`, [ids.orgA])).rows[0];
  await sql(`insert into public.collaboration_slack_sender_links(installation_id,slack_user_id,user_id)
    values($1,'U-bob',$2)`, [installation.installation_id, ids.bob]);
  await rejectsCode(slackCommand('ingest_message', {
    installation_id: installation.installation_id, slack_user_id: 'U-bob', channel_id: 'C1', thread_ts: '1.0',
    event_id: '33000000-0000-4000-8000-000000000001', content: 'blocked', is_mention: false,
  }), 'ROLLOUT_DISABLED');
  await rolloutCommand('set_organization', { org_id: ids.orgA, enabled: true });
  await sql('update public.collaboration_config set shared_text_enabled=false');
  await rejectsCode(append(ids.alice, ids.sessionA,
    '32000000-0000-4000-8000-000000000003', '42000000-0000-4000-8000-000000000003'), 'FEATURE_DISABLED');
  await sql('update public.collaboration_config set shared_text_enabled=true');
}

async function makeArchiveSession(sessionId, suffix, withFile = false) {
  await createSession(ids.alice, ids.orgA, sessionId, `archive-${suffix}`);
  await inviteAndAccept(sessionId, ids.bob, suffix);
  await append(ids.alice, sessionId,
    `31000000-0000-4000-8000-${suffix.padStart(12, '0')}`,
    `41000000-0000-4000-8000-${suffix.padStart(12, '0')}`);
  if (withFile) {
    await sql(`insert into public.collaboration_quota_reservations
      (reservation_id,org_id,session_id,kind,reserved_bytes,used_bytes,status,expires_at,settled_at)
      values($1,$2,$3,'file',16,16,'settled',now()+interval '1 hour',now())`,
      [ids.archiveFileReservation, ids.orgA, sessionId]);
    await sql(`insert into public.collaboration_file_reservations
      (file_id,reservation_id,org_id,session_id,uploader_id,object_path,expected_bytes,sha256,status)
      values($1,$2,$3,$4,$5,$6,16,$7,'uploaded')`, [ids.archiveFile, ids.archiveFileReservation,
      ids.orgA, sessionId, ids.alice, `${ids.orgA}/${sessionId}/${ids.archiveFile}`, 'a'.repeat(64)]);
  }
  await command(ids.alice, 'request_archive', { session_id: sessionId });
  const advanced = await maintenanceCommand('advance_archives', { limit: 10 });
  assert.ok(advanced.advanced > 0);
  const manifest = await command(ids.alice, 'archive_manifest', { session_id: sessionId });
  return { session_id: sessionId, archive_revision: manifest.archive_revision };
}

async function receipt(actor, deviceId, manifest, overrides = {}) {
  const details = (await command(actor, 'archive_manifest', { session_id: manifest.session_id }));
  return command(actor, 'ack_archive', {
    session_id: manifest.session_id,
    archive_revision: manifest.archive_revision,
    device_id: deviceId,
    digest: details.digest,
    byte_length: details.byte_length,
    final_seq: details.final_seq,
    ...overrides,
  });
}

async function testArchiveReceiptAndPurgeGates() {
  const manifest = await makeArchiveSession(ids.sessionArchive, '2', true);
  assert.equal(await maintenanceCommand('claim_purge', {}), null);
  assert.equal(Number(await scalar('select count(*) from public.collaboration_events where session_id=$1', [ids.sessionArchive])), 1);

  await rejectsCode(receipt(ids.alice, ids.aliceDevice, manifest, { digest: '0'.repeat(64) }), 'ARCHIVE_RECEIPT_INVALID');
  await rejectsCode(receipt(ids.alice, ids.aliceDevice, manifest, { final_seq: 999 }), 'ARCHIVE_RECEIPT_INVALID');
  await receipt(ids.alice, ids.aliceDevice, manifest);
  assert.equal(await maintenanceCommand('claim_purge', {}), null,
    'an offline participant without a receipt must block purge');
  assert.equal(Number(await scalar('select count(*) from public.collaboration_events where session_id=$1', [ids.sessionArchive])), 1,
    'content must remain while any required receipt is absent');
  await receipt(ids.bob, ids.bobDevice, manifest);
  const claimed = await maintenanceCommand('claim_purge', {});
  assert.equal(claimed.session_id, ids.sessionArchive);
  assert.deepEqual(claimed.objects.map(object => object.id), [ids.archiveFile]);
  await rejectsCode(maintenanceCommand('check_purge', {
    session_id: claimed.session_id, fence: claimed.fence + 1,
    manifest_digest: claimed.manifest_digest, object_id: ids.archiveFile,
  }), 'LEASE_LOST');
  await rejectsCode(maintenanceCommand('check_purge', {
    session_id: claimed.session_id, fence: claimed.fence,
    manifest_digest: '0'.repeat(64), object_id: ids.archiveFile,
  }), 'PURGE_GUARD_FAILED');
  await maintenanceCommand('check_purge', { ...claimed, object_id: ids.archiveFile });
  await maintenanceCommand('checkpoint_purge', { ...claimed, object_id: ids.archiveFile });
  const finished = await maintenanceCommand('finish_purge', claimed);
  assert.equal(finished.status, 'archived_local');
  assert.equal(Number(await scalar('select count(*) from public.collaboration_events where session_id=$1', [ids.sessionArchive])), 0);
  assert.equal(await scalar('select status from public.collaboration_sessions where session_id=$1', [ids.sessionArchive]), 'archived_local');

  const stale = await makeArchiveSession(ids.sessionStale, '3');
  await receipt(ids.alice, ids.aliceDevice, stale);
  await command(ids.alice, 'revoke_member', { session_id: ids.sessionStale, user_id: ids.bob });
  assert.equal(Number(await scalar('select count(*) from public.collaboration_archive_manifests where session_id=$1', [ids.sessionStale])), 0,
    'membership revision changes invalidate the frozen manifest and receipts');
  const invalidation = await sql(`select detail from public.collaboration_audit
    where session_id=$1 and action='archive_recipients_invalidated' order by audit_id desc limit 1`, [ids.sessionStale]);
  assert.equal(invalidation.rows.length, 1, 'stale recipient evidence must remain in the audit ledger');
  assert.equal(invalidation.rows[0].detail.recipient_ledgers.length, 2);
  assert.equal(await maintenanceCommand('claim_purge', {}), null);
  assert.equal(Number(await scalar('select count(*) from public.collaboration_events where session_id=$1', [ids.sessionStale])), 1);
}

async function main() {
  await installMigration();
  await testAuthorizationAndDedup();
  await testSessionAdmissionCap();
  await testRolloutControls();
  await testSharedFileLifecycle();
  await testQuotaReservationsAndRequesterClaims();
  await testClosingRunCanSettle();
  await testArchiveReceiptAndPurgeGates();
  await db.close();
  console.log('collaboration backend SQL validation passed');
}

main().catch(async error => {
  const position = Number(error?.position);
  const query = typeof error?.query === 'string' ? error.query : '';
  const excerpt = query && Number.isFinite(position)
    ? query.slice(Math.max(0, position - 121), Math.min(query.length, position + 120)).replace(/\s+/g, ' ')
    : '';
  console.error([
    error?.migrationName ? `migration: ${error.migrationName}` : null,
    `step: ${currentStep}`,
    `error: ${error?.message ?? String(error)}`,
    error?.code ? `code: ${error.code}` : null,
    Number.isFinite(position) ? `position: ${position}` : null,
    excerpt ? `query excerpt: ${excerpt}` : null,
  ].filter(Boolean).join('\n'));
  try { await db.close(); } catch {}
  process.exitCode = 1;
});
