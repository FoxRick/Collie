// Compile the exact runtime painter into an isolated, offline expression lab.
const { app, BrowserWindow } = require('electron')
const { mkdirSync, writeFileSync, copyFileSync, readFileSync } = require('node:fs')
const { resolve, join } = require('node:path')
const { transformSync } = require('esbuild')
const assert = require('node:assert/strict')
const output = resolve(process.env.COLLIE_EXPRESSION_OUTPUT || '../../.expression-review')
const source = resolve(__dirname, '../src/renderer/src')
mkdirSync(join(output, 'components'), { recursive: true })
mkdirSync(join(output, 'assets/portrait'), { recursive: true })
const code = transformSync(readFileSync(join(source, 'components/collieExpressions.ts'), 'utf8'), { loader: 'ts', format: 'esm' }).code
writeFileSync(join(output, 'components/expressions.mjs'), code)
for (const name of ['mouth', 'eyes', 'paw', 'ears']) copyFileSync(join(source, `assets/portrait/expression-${name}-v2.webp`), join(output, `assets/portrait/expression-${name}-v2.webp`))
const states = ['idle', 'attentive', 'thinking', 'working', 'review', 'delegating', 'listening', 'writing', 'waiting', 'completion', 'error', 'sleepy']
writeFileSync(join(output, 'index.html'), `<!doctype html><meta charset="utf-8"><title>Collei · fixed-pose expression study</title>
<style>*{box-sizing:border-box}body{margin:0;background:#171614;color:#f9f1e7;font:15px Segoe UI,sans-serif;padding:26px}h1{font-size:25px;margin:0 0 6px}p{color:#aaa298;margin:0 0 22px}.grid{display:grid;grid-template-columns:repeat(6,1fr);gap:14px}article{background:#24221f;border:1px solid #3e3932;border-radius:18px;padding:14px;text-align:center}canvas{width:150px;height:150px;border-radius:50%;background:linear-gradient(150deg,#f7be4b,#d88a20);border:3px solid #f6d68d}b{display:block;margin:7px 0 2px;font-size:14px}small{color:#a99f91}button{background:#efab48;border:0;border-radius:8px;padding:9px 14px;cursor:pointer;margin:18px 8px 0 0}#status{color:#c4b9ab}</style>
<h1>Collei · expressions, without moving the head</h1><p>Original website artwork · one painted face · 49 mouth + 33 eyelid positions · local rendering</p>
<div class="grid">${states.map((s,i)=>`<article><canvas width="300" height="300" data-state="${s}"></canvas><b>${s}</b><small>${['Warm and ready','You are typing','Making a plan','On the task','Checking details','A paw for the handoff','Waiting for helpers','Writing a response','Needs your input','A happy finish','A gentle oops','Peacefully asleep'][i]}</small></article>`).join('')}</div>
<button id="play">Pause animation</button><button id="replay">Replay gestures</button><span id="status">Head position and scale stay fixed.</span>
<script type="module">
import {EXPRESSION_ASSETS,drawExpression,expressionTarget,stepExpression} from './components/expressions.mjs';
const images=await Promise.all(Object.values(EXPRESSION_ASSETS).map(src=>new Promise((resolve,reject)=>{const i=new Image;i.onload=()=>resolve(i);i.onerror=reject;i.src=src})));
const cards=[...document.querySelectorAll('canvas')].map(canvas=>({canvas,ctx:canvas.getContext('2d'),state:canvas.dataset.state,pose:{mouth:.65,lids:0,ears:.12,paw:0,time:0}}));
window.lab={images,cards,drawExpression,expressionTarget,stepExpression,ready:true};let playing=true,last=0,entered=0;
function frame(now){const dt=last?Math.min(.05,(now-last)/1000):1/60;last=now;if(playing){entered+=dt;for(const c of cards){c.pose.time+=dt;stepExpression(c.pose,c.state,dt,entered);c.ctx.setTransform(c.canvas.width/384,0,0,c.canvas.height/384,0,0);drawExpression(c.ctx,images,c.pose)}}requestAnimationFrame(frame)}requestAnimationFrame(frame);
document.querySelector('#play').onclick=()=>{playing=!playing;document.querySelector('#play').textContent=playing?'Pause animation':'Resume animation'};
document.querySelector('#replay').onclick=()=>{entered=0};
</script>`)

app.setPath('userData', join(output, 'lab-profile'))
app.whenReady().then(async () => {
  const window = new BrowserWindow({ width: 1280, height: 760, show: false, webPreferences: { backgroundThrottling: false } })
  await window.loadFile(join(output, 'index.html'))
  for (let i = 0; i < 50; i++) {
    if (await window.webContents.executeJavaScript('!!window.lab?.ready')) break
    await new Promise(r => setTimeout(r, 100))
  }
  const result = await window.webContents.executeJavaScript(`(() => {
    const {cards,images,drawExpression,expressionTarget}=lab;
    const timings=[];const c=cards[0];
    for(let i=0;i<1200;i++){
      const state=cards[i%cards.length].state,pose={...expressionTarget(state,i/60,(i%120)/60),time:i/60};
      const start=performance.now();drawExpression(c.ctx,images,pose);timings.push(performance.now()-start);
    }
    timings.sort((a,b)=>a-b);
    // Fixed landmarks remain identical across every mouth position. Compare
    // nose and forehead pixels after drawing, away from expressive features.
    const read=()=>Array.from(c.ctx.getImageData(89,155,35,20).data);
    drawExpression(c.ctx,images,{mouth:0,lids:0,ears:0,paw:0,time:0},true);const before=read();
    let maximumMeanChange=0;
    for(let i=1;i<49;i++){drawExpression(c.ctx,images,{mouth:i/48,lids:0,ears:0,paw:0,time:0},true);const after=read();maximumMeanChange=Math.max(maximumMeanChange,after.reduce((sum,v,k)=>sum+Math.abs(v-before[k]),0)/before.length)}
    return {frames:1200,median:timings[600],p95:timings[1140],max:timings.at(-1),maximumLandmarkPixelChange:maximumMeanChange,dimensions:images.map(i=>[i.naturalWidth,i.naturalHeight])};
  })()`)
  assert(result.maximumLandmarkPixelChange < 3, 'The fixed face moved between mouth frames: ' + JSON.stringify(result))
  writeFileSync(join(output, 'standalone-results.json'), JSON.stringify(result, null, 2))
  await window.webContents.executeJavaScript(`document.querySelector('#play').click();for(const c of lab.cards){Object.assign(c.pose,lab.expressionTarget(c.state,0,.6),{time:0});lab.drawExpression(c.ctx,lab.images,c.pose,true)}`)
  const faces=await window.webContents.executeJavaScript(`Object.fromEntries(lab.cards.map(c=>[c.state,c.canvas.toDataURL('image/png')]))`)
  for(const [name,data] of Object.entries(faces))writeFileSync(join(output,name+'.png'),Buffer.from(data.split(',')[1],'base64'))
  writeFileSync(join(output, 'expression-contact-sheet.png'), (await window.webContents.capturePage()).toPNG())
  console.log(JSON.stringify({ output, ...result }, null, 2))
  window.destroy();app.quit()
}).catch(error=>{console.error(error);app.exit(1)})
