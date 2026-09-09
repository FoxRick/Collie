#!/usr/bin/env node
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const migrationsDir = resolve(root, 'supabase/migrations');
const testsPath = resolve(root, 'supabase/tests/collaboration_hosted.sql');
const outputPath = resolve(root, '.tmp/hosted-collaboration-validation.sql');
const migrations = (await readdir(migrationsDir))
  .filter(name => /shared.*\.sql$/i.test(name))
  .sort();

if (migrations.length !== 4) {
  throw new Error(`Expected exactly four shared migrations, found ${migrations.length}: ${migrations.join(', ')}`);
}

const migrationSql = await Promise.all(migrations.map(name => readFile(resolve(migrationsDir, name), 'utf8')));
const tests = await readFile(testsPath, 'utf8');
const output = [
  'begin;',
  "set local lock_timeout = '5s';",
  "set local statement_timeout = '90s';",
  ...migrationSql,
  tests,
  'rollback;',
  '',
].join('\n\n');

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, output, 'utf8');
console.log(`built ${outputPath} from ${migrations.length} migrations`);
