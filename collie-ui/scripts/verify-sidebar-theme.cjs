// Chromium layout/color regression test against real React components and Tailwind.
// Run: node scripts/verify-sidebar-theme.cjs
const { join, resolve } = require('path')
const { mkdirSync, writeFileSync } = require('fs')
const output = resolve(process.env.COLLIE_UI_UX_OUTPUT || join(__dirname, '../../.local-runtime-logs/sidebar-theme-check'))

async function build() {
  const { build } = await import('vite')
  const { default: react } = await import('@vitejs/plugin-react')
  const { default: tailwindcss } = await import('@tailwindcss/vite')
  mkdirSync(output, { recursive: true })
  const entry = resolve(__dirname, 'fixtures/sidebar-theme.tsx').replaceAll('\\', '/')
  writeFileSync(join(output, 'index.html'), `<div id="root"></div><script type="module" src="/${entry}"></script>`)
  const source = resolve(__dirname, '../src/renderer').replaceAll('\\', '/')
  const fixtureSources = { name: 'fixture-sources', enforce: 'pre', transform(code, id) {
    if (id.endsWith('/styles/globals.css')) return `${code}\n@source "${source}";`
  } }
  await build({ configFile: false, root: output, base: './', plugins: [fixtureSources, react(), tailwindcss()],
    build: { outDir: join(output, 'dist'), emptyOutDir: true } })
  const { spawnSync } = require('child_process')
  const env = { ...process.env }
  delete env.ELECTRON_RUN_AS_NODE
  const result = spawnSync(require('electron'), [__filename, '--electron'], { env, stdio: 'inherit', windowsHide: true })
  process.exitCode = result.status ?? 1
}

async function verify() {
  const { app, BrowserWindow } = require('electron')
  app.setPath('userData', join(output, 'electron-profile'))
  await app.whenReady()
  const win = new BrowserWindow({ show: false, width: 1200, height: 850, webPreferences: { sandbox: true } })
  const checks = []
  await win.loadFile(join(output, 'dist/index.html'))
  await win.webContents.insertCSS('.sidebar { transition: none !important; }')
  for (const width of [1200, 800]) {
    win.setContentSize(width, 850)
    for (const dark of [false, true]) {
      for (const collapsed of [false, true]) {
        const result = await win.webContents.executeJavaScript(`(async () => {
          document.documentElement.classList.toggle('dark', ${dark});
          const sidebar = document.querySelector('.sidebar');
          if (!sidebar) throw new Error('Fixture did not render');
          sidebar.classList.toggle('is-collapsed', ${collapsed});
          await new Promise(r => setTimeout(r, 220));
          const rail = sidebar.getBoundingClientRect();
          const buttons = [...sidebar.querySelectorAll('.new-chat-button')].map(b => {
            const r = b.getBoundingClientRect();
            return { x: r.x, y: r.y, right: r.right, width: r.width, height: r.height };
          });
          const recap = document.querySelector('[aria-label="Quick recap"]');
          const surface = getComputedStyle(recap);
          const connector = getComputedStyle(document.querySelector('.connector-surface'));
          const rgb = s => s.match(/[\\d.]+/g).slice(0,3).map(Number);
          const luminance = s => rgb(s).map(c => { c /= 255; return c <= .04045 ? c/12.92 : ((c+.055)/1.055)**2.4 }).reduce((v,c,i) => v+c*[.2126,.7152,.0722][i],0);
          const contrast = (a,b) => { const x=luminance(a),y=luminance(b); return (Math.max(x,y)+.05)/(Math.min(x,y)+.05) };
          const text = [...recap.querySelectorAll('h2,li,.takeaway-chip')].map(el => ({text:el.textContent, ratio: contrast(getComputedStyle(el).color, surface.backgroundColor)}));
          return { railWidth: rail.width, buttons, contained: buttons.length===2 && buttons.every(b=>b.x>=rail.x && b.right<=rail.right && b.width>=32 && b.height>=40),
            darkRecap: luminance(surface.backgroundColor)<.1, text,
            connectorRatio: contrast(connector.color,connector.backgroundColor) };
        })()`)
        const ok = result.contained && (!collapsed || result.railWidth === 64) &&
          (!dark || result.darkRecap) && result.text.every(t => t.ratio >= 4.5) && result.connectorRatio >= 4.5
        checks.push({ width, dark, collapsed, ok, ...result })
      }
    }
  }
  writeFileSync(join(output, 'results.json'), JSON.stringify(checks, null, 2))
  console.log(JSON.stringify({ passed: checks.filter(c => c.ok).length, total: checks.length,
    failures: checks.filter(c => !c.ok), evidence: join(output, 'results.json') }, null, 2))
  win.destroy()
  app.exit(checks.every(c => c.ok) ? 0 : 1)
}

(process.argv.includes('--electron') ? verify() : build()).catch(error => {
  console.error(error)
  process.exit(1)
})
