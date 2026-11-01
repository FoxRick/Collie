// Offline only. Reuses bundled website paintings; no model, upload or network.
const { app, BrowserWindow } = require('electron')
const { readFileSync, writeFileSync } = require('node:fs')
const { resolve } = require('node:path')
const { createHash } = require('node:crypto')
const folder = resolve(__dirname, '../src/renderer/src/assets/portrait')
const files = ['happy.webp', 'idle.webp', 'idle-sheet.webp', 'click-reaction-sheet.webp']
app.whenReady().then(async () => {
  const win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } })
  await win.loadURL('about:blank')
  const sources = files.map(name => 'data:image/webp;base64,' + readFileSync(resolve(folder, name)).toString('base64'))
  const result = await win.webContents.executeJavaScript(`(${bake.toString()})(${JSON.stringify(sources)})`)
  for (const [name, data] of Object.entries(result)) writeFileSync(resolve(folder, name), Buffer.from(data.split(',')[1], 'base64'))
  const provenance = {
    method: 'Offline Canvas registration and geometric in-betweens of existing website artwork. No AI generation or uploads.',
    sources: files.map(name => ({ name, sha256: createHash('sha256').update(readFileSync(resolve(folder, name))).digest('hex') })),
    mouth: { frames: 49, columns: 7, cell: 256 },
    eyelids: { frames: 33, columns: 11, cellWidth: 156, cellHeight: 74, x: 77, y: 126 },
    ears: { frames: 33, columns: 11, cellWidth: 256, cellHeight: 80 },
    decodedBytes: 1792 * 1792 * 4 + 1716 * 222 * 4 + 384 * 384 * 4 + 2816 * 240 * 4,
    invariant: 'One fixed painted head; mouth pixels replace the mouth region; eyelids replace eye regions; no whole-head crossfade.'
  }
  writeFileSync(resolve(folder, 'expressions-v2.json'), JSON.stringify(provenance, null, 2) + '\n')
  console.log(JSON.stringify(provenance, null, 2))
  win.destroy()
  app.quit()
}).catch(error => { console.error(error); app.exit(1) })

async function bake(sources) {
  const images = await Promise.all(sources.map(src => new Promise((resolve, reject) => {
    const image = new Image(); image.onload = () => resolve(image); image.onerror = reject; image.src = src
  })))
  const canvas = (w = 384, h = 384) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c }
  const crop = (image, cols = 1, rows = 1, index = 0) => {
    const c = canvas(), g = c.getContext('2d'), w = image.naturalWidth / cols, h = image.naturalHeight / rows
    g.drawImage(image, index % cols * w, Math.floor(index / cols) * h, w, h, 0, 0, 384, 384)
    return c
  }
  const happy = crop(images[0]), neutral = crop(images[1]), closed = crop(images[2], 3, 2, 3)
  const mix = (a, b, t) => a + (b - a) * t
  const smooth = t => { t = Math.max(0, Math.min(1, t)); return t * t * (3 - 2 * t) }
  // Mouth contours share the fixed nose. Only the lips, tongue and chin change.
  const rows = (x, opening) => {
    const influence = smooth((x - 65) / 32) * (1 - smooth((x - 210) / 40))
    const lip = 264 - (x - 110) * .21
    const lower = Math.max(lip + 9, 310 - ((x - 137) / 92) ** 2 * 44)
    return [222, lip, mix(lip + 4, lower, opening * influence), mix(290, 327, opening * influence), 350]
  }
  const triangle = (g, image, src, dst) => {
    const [s0, s1, s2] = src, [a, b, c] = dst
    const det = (s1.x-s0.x)*(s2.y-s0.y)-(s2.x-s0.x)*(s1.y-s0.y)
    if (Math.abs(det)<.001) return
    const a11=((b.x-a.x)*(s2.y-s0.y)-(c.x-a.x)*(s1.y-s0.y))/det
    const a12=((b.y-a.y)*(s2.y-s0.y)-(c.y-a.y)*(s1.y-s0.y))/det
    const a21=((c.x-a.x)*(s1.x-s0.x)-(b.x-a.x)*(s2.x-s0.x))/det
    const a22=((c.y-a.y)*(s1.x-s0.x)-(b.y-a.y)*(s2.x-s0.x))/det
    g.save(); g.beginPath()
    const cx=(a.x+b.x+c.x)/3,cy=(a.y+b.y+c.y)/3
    dst.forEach((p,i)=>{const dx=p.x-cx,dy=p.y-cy,l=Math.max(.1,Math.hypot(dx,dy)); const x=p.x+dx/l*.6,y=p.y+dy/l*.6; i?g.lineTo(x,y):g.moveTo(x,y)})
    g.closePath(); g.clip(); g.transform(a11,a12,a21,a22,a.x-a11*s0.x-a21*s0.y,a.y-a12*s0.x-a22*s0.y); g.drawImage(image,0,0); g.restore()
  }
  const warpMouth = (image, from, to) => {
    const c=canvas(),g=c.getContext('2d');g.drawImage(image,0,0);g.clearRect(64,222,188,128)
    const points = opening => Array.from({length:25},(_,i)=>{const x=64+i*188/24;return rows(x,opening).map(y=>({x,y}))})
    const s=points(from), d=points(to)
    for(let x=0;x<24;x++) for(let y=0;y<4;y++) {
      triangle(g,image,[s[x][y],s[x+1][y],s[x][y+1]],[d[x][y],d[x+1][y],d[x][y+1]])
      triangle(g,image,[s[x+1][y],s[x+1][y+1],s[x][y+1]],[d[x+1][y],d[x+1][y+1],d[x][y+1]])
    }
    return c
  }
  const atlas=canvas(1792,1792),ag=atlas.getContext('2d')
  let fallback
  for(let i=0;i<49;i++) {
    // Deform ONE painted mouth. Never blend the open mouth over a closed
    // mouth (or vice versa): that was the source of the old ghost teeth.
    const t=i/48, frame=warpMouth(happy,1,t)
    ag.drawImage(frame,0,0,384,384,i%7*256,Math.floor(i/7)*256,256,256)
    if(i===31){
      // Match the runtime's fixed framing, including the extended chest.
      const still=canvas(),sg=still.getContext('2d');sg.translate(28,22);sg.scale(.9,.9)
      sg.drawImage(frame,0,0,384,280,0,0,384,280)
      sg.drawImage(frame,0,280,384,104,0,280,384,130)
      fallback=still.toDataURL('image/webp',1)
    }
  }
  // A local eye patch, not another portrait. Vertically register the painted
  // closed eyelids to the open-eye centers before baking the 33 intermediates.
  const eyes=canvas(1716,222),eg=eyes.getContext('2d')
  const eyeCenters=[[112,153,120,164],[205,168,205,177]]
  for(let i=0;i<33;i++) {
    const t=i/32,patch=canvas(156,74),pg=patch.getContext('2d')
    pg.drawImage(happy,77,126,156,74,0,0,156,74)
    for(const [x,y,sx,sy] of eyeCenters) {
      const local=canvas(66,54),lg=local.getContext('2d')
      // Compress the open eye toward its lid line, then replace with the
      // original painted eyelid; no second visible pupil under a closing lid.
      lg.drawImage(happy,x-33,y-27,66,54,0,0,66,54)
      const close=canvas(66,54),cg=close.getContext('2d');cg.drawImage(closed,sx-33,sy-27,66,54,0,0,66,54)
      const open=canvas(66,54),og=open.getContext('2d')
      og.drawImage(local,0,0,66,54,0,t*19,66,54-t*38)
      lg.clearRect(0,0,66,54)
      if(t===0)lg.drawImage(open,0,0)
      else {lg.drawImage(close,0,0);lg.drawImage(open,0,0);lg.globalAlpha=t**8;lg.drawImage(close,0,0);lg.globalAlpha=1}
      lg.globalCompositeOperation='destination-in'
      const mask=lg.createRadialGradient(33,27,12,33,27,29);mask.addColorStop(0,'white');mask.addColorStop(.7,'white');mask.addColorStop(1,'transparent');lg.fillStyle=mask;lg.fillRect(0,0,66,54)
      pg.drawImage(local,x-33-77,y-27-126)
    }
    eg.drawImage(patch,i%11*156,Math.floor(i/11)*74)
  }
  const paw=crop(images[3],2,2,2),pg=paw.getContext('2d');pg.globalCompositeOperation='destination-in';pg.beginPath();pg.moveTo(31,384);pg.lineTo(31,277);pg.quadraticCurveTo(20,237,40,224);pg.quadraticCurveTo(65,207,86,232);pg.quadraticCurveTo(104,252,96,281);pg.lineTo(105,384);pg.closePath();pg.fill()
  // Bake ear deformation with inverse bilinear sampling. Drawing moving
  // scanline strips at runtime creates hairline gaps on fractional DPI.
  const ears=canvas(2816,240),earg=ears.getContext('2d'),source=happy.getContext('2d').getImageData(0,0,384,384).data
  for(let frame=0;frame<33;frame++){
    const amount=-.7+frame/32*1.85,local=canvas(384,120),lg=local.getContext('2d'),pixels=lg.createImageData(384,120)
    for(let y=0;y<120;y++)for(let x=0;x<384;x++){
      let sy=y,sx=x
      for(let pass=0;pass<4;pass++){
        const weight=1-smooth(sy/120),side=x>=192
        sy=y+weight*amount*9*(side?.72:1)
        sx=x-weight*amount*(side?2.2:-2.2)
      }
      if(sx<0||sx>=383||sy<0||sy>=383)continue
      const ix=Math.floor(sx),iy=Math.floor(sy),fx=sx-ix,fy=sy-iy,index=(y*384+x)*4
      // Interpolate premultiplied colors so transparent fur edges stay clean.
      let alpha=0;const rgb=[0,0,0]
      for(const [dx,dy,w] of [[0,0,(1-fx)*(1-fy)],[1,0,fx*(1-fy)],[0,1,(1-fx)*fy],[1,1,fx*fy]]){
        const k=((iy+dy)*384+ix+dx)*4,a=source[k+3]/255*w;alpha+=a
        for(let c=0;c<3;c++)rgb[c]+=source[k+c]*a
      }
      pixels.data[index+3]=alpha*255
      if(alpha)for(let c=0;c<3;c++)pixels.data[index+c]=rgb[c]/alpha
    }
    lg.putImageData(pixels,0,0);earg.drawImage(local,0,0,384,120,frame%11*256,Math.floor(frame/11)*80,256,80)
  }
  return {'expression-mouth-v2.webp':atlas.toDataURL('image/webp',1),'expression-eyes-v2.webp':eyes.toDataURL('image/webp',1),'expression-ears-v2.webp':ears.toDataURL('image/webp',1),'expression-paw-v2.webp':paw.toDataURL('image/webp',1),'expression-ready-v2.webp':fallback}
}
