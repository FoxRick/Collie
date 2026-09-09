// Run with a local PGlite module directory argument; never connects to a server.
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import assert from 'node:assert/strict'
const { PGlite } = await import(pathToFileURL(resolve(process.argv[2], 'dist/index.js')).href)
const db = new PGlite()
await db.exec(`
  create role anon; create role authenticated; create role service_role;
  create schema auth; create table auth.users(id uuid primary key);
  insert into auth.users values ('00000000-0000-0000-0000-000000000001');
`)
await db.exec(await readFile(new URL('./schema.sql',import.meta.url),'utf8'))
const user='00000000-0000-0000-0000-000000000001'
const reserve=async(id,units=3000)=>(await db.query(
  'select public.collie_inference_reserve($1,$2,$3) as result',[user,id,units])).rows[0].result
assert.equal((await reserve('initial-request-0001')).allowed,false)
await db.query(`insert into collie_inference.allowances(user_id,units,expires_at,enabled)
  values($1,10000,now()+interval '1 day',true)`,[user])
await db.exec('update collie_inference.pool set enabled=true')
assert.equal((await reserve('first-request-00001')).allowed,true)
assert.equal((await reserve('first-request-00001')).reason,'duplicate')
const competing=await Promise.all([
  reserve('competing-request-01'),reserve('competing-request-02')
])
assert.equal(competing.filter(x=>x.allowed).length,1)
assert.equal((await db.query('select spent from collie_inference.allowances')).rows[0].spent,6000)
assert.equal((await reserve(null)).reason,'invalid')
await db.exec("update collie_inference.pool set minute_start=now()-interval '2 minutes'")
assert.equal((await reserve('next-minute-request1')).allowed,true)
assert.equal((await reserve('user-budget-request1')).reason,'allowance')
await db.exec(`update collie_inference.pool set daily_spent=100000;
  update collie_inference.allowances set units=20000`)
assert.equal((await reserve('pool-budget-request1')).reason,'pool')
await db.exec(`update collie_inference.pool set day=current_date-1,
  minute_start=now()-interval '2 minutes'`)
assert.equal((await reserve('new-day-request-0001')).allowed,true)
await db.exec("update collie_inference.allowances set expires_at=now()-interval '1 second'")
assert.equal((await reserve('expired-request-0001')).reason,'allowance')
for (const role of ['anon','authenticated']) {
  await db.exec('set role '+role)
  await assert.rejects(()=>reserve('unauthorized-request1'),/permission denied/)
  await assert.rejects(()=>db.exec('select * from collie_inference.allowances'),/permission denied/)
  await db.exec('reset role')
}
await db.exec('set role service_role')
assert.equal((await reserve('service-expired-0001')).reason,'allowance')
await db.close()
console.log('PASS: schema executes; disabled/duplicate/shared/user/expiry limits and role restrictions hold.')
