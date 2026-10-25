// Launch the built app in an isolated profile, then enable its existing UI QA mode.
const { spawn } = require('node:child_process')
const { join, resolve } = require('node:path')
const { tmpdir } = require('node:os')
const { mkdirSync } = require('node:fs')
const ui = resolve(__dirname, '..')
const home = resolve(process.env.COLLIE_PORTRAIT_REVIEW_HOME || join(tmpdir(), 'collie-portrait-review'))
const port = Number(process.env.COLLIE_DEBUG_PORT || 9345)
mkdirSync(home, { recursive:true })
const child = spawn(require('electron'), [ui, `--remote-debugging-port=${port}`], {
  cwd:ui, detached:true, stdio:'ignore', windowsHide:false,
  env:{...process.env,COLLIE_HOME:join(home,'core'),COLLIE_ELECTRON_USER_DATA:join(home,'electron')}
})
child.unref()
async function main() {
  let page
  for (let i=0;i<100;i++) {
    try { const pages=await(await fetch(`http://127.0.0.1:${port}/json`)).json();page=pages.find(p=>p.type==='page'&&p.url.includes('/out/renderer/index.html'));if(page)break } catch {}
    await new Promise(r=>setTimeout(r,200))
  }
  if(!page)throw new Error('Preview did not start. Build the app first and choose an unused debug port.')
  const ws=new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((r,j)=>{ws.addEventListener('open',r,{once:true});ws.addEventListener('error',j,{once:true})})
  ws.send(JSON.stringify({id:1,method:'Runtime.evaluate',params:{expression:"sessionStorage.setItem('collie.ui-ux-preview','1')"}}))
  await new Promise(r=>ws.addEventListener('message',r,{once:true}))
  ws.send(JSON.stringify({id:2,method:'Page.reload'}))
  await new Promise(r=>ws.addEventListener('message',r,{once:true}))
  ws.close()
  console.log(`Portrait review opened. Profile: ${home}; debug port: ${port}. No provider is needed; do not send messages.`)
}
main().catch(e=>{console.error(e);process.exitCode=1})
