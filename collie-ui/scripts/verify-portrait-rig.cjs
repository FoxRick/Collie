// Run only against a separate local review instance; never the installed app.
// COLLIE_PORTRAIT_QA_OUTPUT must point outside the repository.
const { mkdirSync, writeFileSync } = require('node:fs')
const { resolve, relative } = require('node:path')
const assert = require('node:assert/strict')
const port = Number(process.env.COLLIE_DEBUG_PORT || 9345)
const output = resolve(process.env.COLLIE_PORTRAIT_QA_OUTPUT || '../../.portrait-review/evidence')
const repo = resolve(__dirname, '../..')
assert(relative(repo, output).startsWith('..'), 'Evidence must stay outside the source repository')
mkdirSync(output, { recursive: true })
const sleep = ms => new Promise(r => setTimeout(r, ms))
async function main() {
  const pages = await (await fetch(`http://127.0.0.1:${port}/json`)).json()
  const page = pages.find(p => p.type === 'page' && p.url.includes('/out/renderer/index.html'))
  assert(page, 'Launch the isolated built preview first')
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((r, j) => { ws.addEventListener('open', r, { once: true }); ws.addEventListener('error', j, { once: true }) })
  let sequence = 0
  const pending = new Map()
  ws.addEventListener('message', e => { const m = JSON.parse(String(e.data)); if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result) } })
  const command = (method, params = {}) => new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })) })
  const evaluate = async expression => { const r = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); assert(!r.exceptionDetails, JSON.stringify(r.exceptionDetails)); return r.result.value }
  try {
    await evaluate(`sessionStorage.setItem('collie.ui-ux-preview','1')`)
    await command('Page.bringToFront')
    await command('Emulation.setFocusEmulationEnabled', { enabled:true })
    await command('Page.reload')
    await sleep(2000)
    const initial = await evaluate(`(() => {
      const stage = document.querySelector('.collie-portrait-stage'), canvas = stage?.querySelector('canvas'), img = stage?.querySelector('img')
      if (!canvas) return null
      const r = canvas.getBoundingClientRect()
      return { imgHidden: img.hidden, canvasHidden: canvas.hidden, visibility:document.visibilityState, x:r.x+r.width/2, y:r.y+r.height/2, radius:r.width/2, width:canvas.width, height:canvas.height }
    })()`)
    assert(initial, 'Finish guest onboarding in the isolated preview: ' + await evaluate('document.body.innerText.slice(0,1500)'))
    assert(initial.imgHidden && !initial.canvasHidden, 'Duplicate silhouette: fallback must be hidden ' + JSON.stringify(initial))
    const samples = []
    for (let i = 0; i < 8; i++) {
      const angle = i * Math.PI / 4 - Math.PI / 2
      const x = initial.x + Math.cos(angle) * initial.radius * 1.4, y = initial.y + Math.sin(angle) * initial.radius * 1.4
      await command('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y })
      await sleep(450)
      samples.push(await evaluate(`(() => { const c=document.querySelector('.collie-portrait-stage canvas'), d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; let ax=0,ay=0,sum=0; for(let i=0;i<d.length;i+=4){const a=d[i+3];sum+=a;ax+=(i/4%c.width)*a;ay+=Math.floor(i/4/c.width)*a} return { x:ax/sum,y:ay/sum,state:document.querySelector('.collie-portrait-stage').className } })()`))
      assert(samples[i].state.includes('pointer_look'), 'Tracking must work beyond the ring')
      const shot = await command('Page.captureScreenshot', { format:'png' })
      writeFileSync(resolve(output, `direction-${i}.png`), Buffer.from(shot.data,'base64'))
    }
    assert(samples[0].y < samples[4].y - 1, 'Up must differ from down in rendered pixels')
    assert(samples[2].x > samples[6].x + 1, 'Right must differ from left in rendered pixels')
    const reaction = await evaluate(`(async () => {
      const ring=document.querySelector('.collie-portrait-ring'),c=ring.querySelector('canvas'),ctx=c.getContext('2d'),frames=[];
      ring.click();const start=performance.now();
      while(performance.now()-start<1200){
        const data=ctx.getImageData(0,0,c.width,c.height).data;let ax=0,ay=0,total=0;
        for(let i=0;i<data.length;i+=4){const a=data[i+3];total+=a;ax+=(i/4%c.width)*a;ay+=Math.floor(i/4/c.width)*a}
        frames.push({ms:performance.now()-start,x:ax/total,y:ay/total,alpha:total,state:document.querySelector('.collie-portrait-stage').className,hidden:ring.querySelector('img').hidden});
        await new Promise(r=>setTimeout(r,50));
      }return frames;
    })()`)
    assert(reaction.some(f=>f.state.includes('click_reaction')), 'Click must trigger a reaction')
    assert(reaction.every(f=>f.hidden), 'Click must never reveal a second dog')
    assert(reaction.at(-1).state.includes('pointer_look'), 'Reaction must return to pointer attention')
    const jumps=reaction.slice(1).map((f,i)=>Math.hypot(f.x-reaction[i].x,f.y-reaction[i].y))
    assert(Math.max(...jumps)<4, 'Reaction introduced an abrupt silhouette jump')
    await evaluate(`window.dispatchEvent(new Event('blur'))`)
    await sleep(600)
    assert(!(await evaluate(`document.querySelector('.collie-portrait-stage').className`)).includes('pointer_look'), 'Window blur must release attention')
    const viewport = await evaluate(`({width:innerWidth,height:innerHeight})`)
    for (const deviceScaleFactor of [1,1.25,1.5,2]) {
      await command('Emulation.setDeviceMetricsOverride', {...viewport,deviceScaleFactor,mobile:false})
      await sleep(150)
      const sizing=await evaluate(`(()=>{const c=document.querySelector('.collie-portrait-stage canvas');return {actual:c.width,expected:Math.round(c.getBoundingClientRect().width*Math.min(2,devicePixelRatio))}})()`)
      assert.equal(sizing.actual,sizing.expected,'Canvas must follow display scaling within its DPR budget')
    }
    await command('Emulation.clearDeviceMetricsOverride')
    await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]})
    await sleep(150)
    const frozen=await evaluate(`document.querySelector('.collie-portrait-stage canvas').toDataURL()`)
    await sleep(300)
    assert.equal(await evaluate(`document.querySelector('.collie-portrait-stage canvas').toDataURL()`),frozen,'Reduced motion must freeze the rendered portrait')
    await command('Emulation.setEmulatedMedia',{features:[]})
    // Measure actual draw wall time and frame cadence during unsent typing.
    await evaluate(`(() => {
      const c=document.querySelector('.collie-portrait-stage canvas'), ctx=c.getContext('2d'); window.__portraitQA={cost:[],interval:[],start:0,end:0};
      const clear=ctx.clearRect, restore=ctx.restore;
      ctx.clearRect=function(...args){const q=window.__portraitQA,n=performance.now();if(q.start){q.cost.push(q.end-q.start);q.interval.push(n-q.start)}q.start=n;return clear.apply(this,args)};
      ctx.restore=function(...args){const r=restore.apply(this,args);window.__portraitQA.end=performance.now();return r};
      window.__portraitQACleanup=()=>{ctx.clearRect=clear;ctx.restore=restore};
    })()`)
    const draft = await evaluate(`document.querySelector('[aria-label="Message Collie"]')?.value || ''`)
    await evaluate(`document.querySelector('[aria-label="Message Collie"]')?.focus()`)
    for (const text of ['Review ', 'portrait ', 'motion ', 'without sending.']) { await command('Input.insertText', {text}); await sleep(500) }
    await sleep(2000)
    const metrics = await evaluate(`(() => { const q=window.__portraitQA;window.__portraitQACleanup();delete window.__portraitQACleanup;delete window.__portraitQA; return q })()`)
    await evaluate(`(()=>{const input=document.querySelector('[aria-label="Message Collie"]');input.focus();input.select()})()`)
    await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Backspace',code:'Backspace',windowsVirtualKeyCode:8})
    await command('Input.dispatchKeyEvent',{type:'keyUp',key:'Backspace',code:'Backspace',windowsVirtualKeyCode:8})
    if(draft) await command('Input.insertText',{text:draft})
    const stats = values => { const a=values.sort((a,b)=>a-b); return { n:a.length, median:a[Math.floor(a.length*.5)],p95:a[Math.floor(a.length*.95)],max:a.at(-1) } }
    const report = { initial, samples, reaction, reactionMaxCentroidStep:Math.max(...jumps), drawMs: stats(metrics.cost), frameIntervalMs:stats(metrics.interval), platform:process.platform, note:'Wall time in Canvas mesh draw, not GPU presentation latency. Isolated empty conversation with unsent typing; focus emulated for repeatability.' }
    writeFileSync(resolve(output,'rendered-results.json'),JSON.stringify(report,null,2))
    console.log(JSON.stringify(report,null,2))
  } finally {
    await evaluate(`window.__portraitQACleanup?.()`)
    await command('Emulation.setEmulatedMedia',{features:[]})
    await command('Emulation.clearDeviceMetricsOverride')
    await command('Emulation.setFocusEmulationEnabled', { enabled:false })
    ws.close()
  }
}
main().catch(e=>{console.error(e);process.exitCode=1})

