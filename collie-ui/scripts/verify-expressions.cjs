// Use ONLY the isolated preview launched by preview-portrait.cjs.
const { mkdirSync, writeFileSync } = require('node:fs')
const { resolve, join } = require('node:path')
const { execFileSync } = require('node:child_process')
const assert = require('node:assert/strict')
const output = resolve(process.env.COLLIE_EXPRESSION_OUTPUT || '../../.expression-review')
const port = Number(process.env.COLLIE_DEBUG_PORT || 9357)
const sleep = ms => new Promise(r => setTimeout(r, ms))
mkdirSync(output, { recursive: true })

async function connect(url) {
  const ws = new WebSocket(url)
  await new Promise((resolve, reject) => { ws.addEventListener('open', resolve, { once: true }); ws.addEventListener('error', reject, { once: true }) })
  let sequence = 0
  const pending = new Map()
  ws.addEventListener('message', e => { const m = JSON.parse(String(e.data)); if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result) } })
  return { ws, send: (method, params = {}) => new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })) }) }
}

function nativeMemory() {
  // Read process working sets and per-process GPU allocations. No global
  // desktop capture, inputs, credentials, or system settings are involved.
  const script = `$all=Get-CimInstance Win32_Process -Filter "name='electron.exe'"; $main=$all | Where-Object {$_.CommandLine -match 'remote-debugging-port=${port}'} | Select-Object -First 1; if(-not $main){throw 'Preview process missing'}; $ids=@($main.ProcessId)+@($all | Where-Object {$_.ParentProcessId -eq $main.ProcessId} | ForEach-Object {$_.ProcessId}); $rows=@($all | Where-Object {$ids -contains $_.ProcessId} | ForEach-Object {$p=Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; [pscustomobject]@{pid=$_.ProcessId;type=if($_.CommandLine -match '--type=([^ ]+)'){$Matches[1]}else{'browser'};workingSetMB=[math]::Round($p.WorkingSet64/1MB,2);privateMB=[math]::Round($p.PrivateMemorySize64/1MB,2)}}); $gpu=@(); try{$gpu=@((Get-Counter '\GPU Process Memory(*)\Dedicated Usage','\GPU Process Memory(*)\Shared Usage' -ErrorAction Stop).CounterSamples | Where-Object {$_.InstanceName -match 'pid_(\\d+)' -and $ids -contains [int]$Matches[1]} | ForEach-Object {[pscustomobject]@{instance=$_.InstanceName;counter=$_.Path;bytes=$_.CookedValue}})}catch{}; @{processes=$rows;gpu=$gpu}|ConvertTo-Json -Depth 5 -Compress`
  return JSON.parse(execFileSync('powershell.exe', ['-NoProfile', '-Command', script], { encoding: 'utf8', windowsHide: true, timeout: 15000 }))
}

async function main() {
  const pages = await (await fetch(`http://127.0.0.1:${port}/json`)).json()
  const page = pages.find(p => p.type === 'page' && p.url.includes('/out/renderer/index.html'))
  assert(page, 'Launch the isolated preview first')
  const client = await connect(page.webSocketDebuggerUrl), { send } = client
  const errors = []
  client.ws.addEventListener('message', e => { const m = JSON.parse(String(e.data)); if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails) })
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails))
    return result.result.value
  }
  const choose = state => evaluate(`(() => { const s=document.querySelector('[aria-label="Preview expression"]');s.value=${JSON.stringify(state)};s.dispatchEvent(new Event('change',{bubbles:true}));return !!s })()`)
  const screenshot = async name => writeFileSync(join(output, name + '.png'), Buffer.from((await send('Page.captureScreenshot', { format: 'png' })).data, 'base64'))
  try {
    await send('Runtime.enable')
    await evaluate(`sessionStorage.setItem('collie.ui-ux-preview','1');sessionStorage.setItem('collie.expression-preview','1')`)
    await send('Page.reload');await sleep(1800)
    assert(await evaluate(`!!document.querySelector('.collie-portrait-stage canvas')`), 'Finish guest onboarding in this test profile, then rerun')
    await send('Page.bringToFront')
    // Native occlusion marks an Electron window hidden when the workbench
    // covers it. Keep this soak visible consistently, then undo in finally.
    await send('Emulation.setFocusEmulationEnabled',{enabled:true})
    await sleep(500)
    await send('Performance.enable')
    const initial = await evaluate(`({canvases:document.querySelectorAll('.collie-portrait-stage canvas').length,staticHidden:document.querySelector('.collie-portrait-stage img').hidden,canvasVisible:!document.querySelector('.collie-portrait-stage canvas').hidden,bridgeRemoved:!('portraitPointer' in window.collie)})`)
    assert(initial.canvases === 1 && initial.staticHidden && initial.canvasVisible && initial.bridgeRemoved, JSON.stringify(initial))
    // One painter, one requestAnimationFrame owner, no texture creation during
    // any state transition. Observe the real app rather than a replacement.
    await evaluate(`(() => {
      const base=document.createElement.bind(document);window.__expressionQA={canvases:0,frames:0,cost:[],intervals:[],last:0};
      document.createElement=function(name,...args){if(name==='canvas')__expressionQA.canvases++;return base(name,...args)};
      const proto=CanvasRenderingContext2D.prototype,draw=proto.drawImage;
      proto.drawImage=function(...args){const start=performance.now();const result=draw.apply(this,args);if(this.canvas.closest?.('.collie-portrait-stage'))__expressionQA.cost.push(performance.now()-start);return result};
      const transform=proto.setTransform;proto.setTransform=function(...args){if(this.canvas.closest?.('.collie-portrait-stage')){const q=__expressionQA,t=performance.now();if(q.last)q.intervals.push(t-q.last);q.last=t;q.frames++}return transform.apply(this,args)};
    })()`)
    const states = ['idle','attentive','thinking','working','review','delegating','listening','writing','waiting','completion','error','sleepy']
    const poses = []
    for (const state of states) {
      await choose(state);await sleep(state==='completion'||state==='delegating'||state==='waiting'?600:700)
      poses.push(await evaluate(`(() => {const c=document.querySelector('.collie-portrait-stage canvas'),r=c.getBoundingClientRect();return {state:c.dataset.expression,mouth:+c.dataset.mouth,lids:+c.dataset.lids,paw:+c.dataset.wave,x:r.x,y:r.y,w:r.width,h:r.height,fallbackHidden:document.querySelector('.collie-portrait-stage img').hidden}})()`))
      await screenshot('app-'+state)
    }
    assert(poses.every((p,i)=>p.state===states[i]&&p.fallbackHidden),JSON.stringify(poses))
    assert(poses.every(p=>p.x===poses[0].x&&p.y===poses[0].y&&p.w===poses[0].w),'Portrait moved or changed scale')
    assert(poses.find(p=>p.state==='sleepy').lids>.99,'Sleep must close both eyes')
    assert(poses.find(p=>p.state==='delegating').paw>.7,'Handoff paw did not rise')
    // Check the canvas/fallback swap on a context loss, then recover.
    const recovery = await evaluate(`(async()=>{const c=document.querySelector('.collie-portrait-stage canvas'),img=document.querySelector('.collie-portrait-stage img');c.dispatchEvent(new Event('contextlost',{cancelable:true}));const fallback=!img.hidden&&c.hidden;c.dispatchEvent(new Event('contextrestored'));await new Promise(r=>setTimeout(r,100));return {fallback,recovered:img.hidden&&!c.hidden}})()`)
    assert(recovery.fallback&&recovery.recovered,JSON.stringify(recovery))
    await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});await choose('sleepy');await sleep(300)
    const reduced = await evaluate(`({clock:document.querySelector('.collie-portrait-stage canvas').dataset.clock,lids:document.querySelector('.collie-portrait-stage canvas').dataset.lids})`)
    await sleep(350)
    assert(await evaluate(`document.querySelector('.collie-portrait-stage canvas').dataset.clock`)===reduced.clock,'Reduced motion continued rendering')
    await choose('error');await sleep(100)
    assert(await evaluate(`document.querySelector('.collie-portrait-stage canvas').dataset.expression`)==='error','Reduced motion state did not update')
    await send('Emulation.setEmulatedMedia',{features:[]})
    const sizes=[]
    for(const scale of [1,1.25,1.5,2]){
      await send('Emulation.setDeviceMetricsOverride',{width:1104,height:740,deviceScaleFactor:scale,mobile:false});await sleep(180)
      sizes.push(await evaluate(`(()=>{const c=document.querySelector('.collie-portrait-stage canvas');return {dpr:devicePixelRatio,width:c.width,css:c.getBoundingClientRect().width}})()`))
      await screenshot('app-dpr-'+scale)
    }
    await send('Emulation.clearDeviceMetricsOverride')
    // Warm-up and GC before a sustained 90-second state-switching sample.
    const memory=[]
    for(let round=0;round<4;round++){
      if(round)for(let i=0;i<120;i++){await choose(states[i%states.length]);await sleep(250)}
      await send('HeapProfiler.collectGarbage')
      memory.push({round,heap:await send('Runtime.getHeapUsage'),dom:await send('Memory.getDOMCounters'),native:nativeMemory()})
      console.log('Memory sample '+round+': '+JSON.stringify(memory.at(-1)))
    }
    const heapGrowth = memory.at(-1).heap.usedSize-memory[0].heap.usedSize
    assert(heapGrowth<5*1024*1024,'Retained JS heap grew by more than 5 MiB: '+heapGrowth)
    assert(memory.at(-1).dom.nodes-memory[0].dom.nodes<100,'DOM nodes accumulated during transitions')
    const timing=await evaluate(`(()=>{const q=__expressionQA,sorted=q.cost.slice().sort((a,b)=>a-b),interval=q.intervals.slice().sort((a,b)=>a-b);return {frames:q.frames,canvasAllocations:q.canvases,drawCalls:sorted.length,drawCallP95:sorted[Math.floor(sorted.length*.95)],frameMedian:interval[Math.floor(interval.length*.5)],frameP95:interval[Math.floor(interval.length*.95)]}})()`)
    // The only canvas reset above was the reduced-motion remount; state
    // switches themselves allocate no canvas or atlas.
    assert(timing.canvasAllocations===0,'A state transition allocated new canvases')
    assert(errors.length===0,JSON.stringify(errors))
    await choose('');await sleep(500);await screenshot('app-final')
    const report={initial,poses,recovery,reduced,sizes,memory,heapGrowth,timing,errors,limits:'90-second state-transition soak. Native GPU counters may be unavailable; process working set is not dedicated VRAM. No native macOS/Linux or long-duration leak claim.'}
    writeFileSync(join(output,'app-results.json'),JSON.stringify(report,null,2))
    console.log(JSON.stringify({heapGrowth,timing,errors:errors.length,output},null,2))
  } finally { await send('Emulation.setEmulatedMedia',{features:[]}).catch(()=>{});await send('Emulation.setFocusEmulationEnabled',{enabled:false}).catch(()=>{});client.ws.close() }
}
main().catch(error=>{console.error(error);process.exitCode=1})
