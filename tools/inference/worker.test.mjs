import test from 'node:test'
import assert from 'node:assert/strict'
import { createWorker, payloadFor } from './worker.mjs'

const env = { FREE_TIER_CONFIRMED:'true', GROQ_API_KEY:'test-supplier',
  SUPABASE_URL:'https://accounts.test', SUPABASE_SERVICE_ROLE_KEY:'test-service' }
const body = { model:'collie-auto', messages:[{role:'user',content:'Hello'}], stream:true }
const request = (value=body) => new Request('https://collie.test/v1/chat/completions',{
  method:'POST',headers:{Authorization:'Bearer test-user','Idempotency-Key':'12345678-1234-1234'},
  body:JSON.stringify(value)
})
const response = value => new Response(JSON.stringify(value))
function fixture({allowed=true, upstreamStatus=200, identityStatus=200}={}) {
  const calls=[]
  const worker=createWorker(async(url,options) => {
    calls.push({url,options})
    if (url.endsWith('/auth/v1/user')) return new Response(JSON.stringify({
      id:'00000000-0000-0000-0000-000000000001',email_confirmed_at:'2026-01-01'
    }),{status:identityStatus})
    if (url.endsWith('collie_inference_reserve')) return response({allowed,reason:'pool'})
    if (url.endsWith('collie_inference_allowance')) return response({available:true,remaining:100,limit:100})
    return new Response(upstreamStatus===200 ? 'data: [DONE]\n\n' : 'private upstream diagnostics',
      {status:upstreamStatus})
  })
  return {worker,calls}
}
test('disabled deployment makes zero network calls',async()=>{
  const {worker,calls}=fixture()
  assert.equal((await worker.fetch(request(),{})).status,503)
  assert.equal(calls.length,0)
})
test('invalid identity and denied allowance never reach supplier',async()=>{
  for (const options of [{identityStatus:401},{allowed:false}]) {
    const {worker,calls}=fixture(options)
    assert.notEqual((await worker.fetch(request(),env)).status,200)
    assert.ok(calls.every(c=>!c.url.includes('api.groq.com')))
  }
})
test('authenticated reservation precedes streaming; credentials stay on their own hosts',async()=>{
  const {worker,calls}=fixture()
  const res=await worker.fetch(request({...body,api_base:'https://evil.test',api_key:'injected'}),env)
  assert.equal(await res.text(),'data: [DONE]\n\n')
  assert.equal(calls.length,3)
  assert.ok(calls[1].url.endsWith('collie_inference_reserve'))
  assert.equal(calls[0].options.headers.Authorization,'Bearer test-user')
  assert.equal(calls[2].options.headers.Authorization,'Bearer test-supplier')
  const sent=JSON.parse(calls[2].options.body)
  assert.equal(sent.model,'openai/gpt-oss-20b')
  assert.equal(sent.api_base,undefined)
  assert.equal(sent.api_key,undefined)
})
test('upstream failure is sanitized and never retried',async()=>{
  const {worker,calls}=fixture({upstreamStatus:429})
  const res=await worker.fetch(request(),env)
  assert.equal(res.status,503)
  assert.ok(!(await res.text()).includes('private'))
  assert.equal(calls.filter(c=>c.url.includes('api.groq.com')).length,1)
})
test('entitlement reads never invoke inference',async()=>{
  const {worker,calls}=fixture()
  const res=await worker.fetch(new Request('https://collie.test/v1/me/entitlements',{
    headers:{Authorization:'Bearer test-user'}
  }),env)
  assert.equal(res.status,200)
  assert.equal(calls.length,2)
})
test('unsupported model, output, image and oversized context fail before admission',async()=>{
  for(const value of [
    {...body,model:'expensive-model'}, {...body,max_tokens:4096},
    {...body,messages:[{role:'user',content:[{type:'image_url'}]}]},
    {...body,messages:[{role:'user',content:'x'.repeat(130000)}]}
  ]) {
    const {worker,calls}=fixture()
    assert.equal((await worker.fetch(request(value),env)).status,400)
    assert.equal(calls.length,1)
  }
  assert.ok(payloadFor(body).units>2048)
})
