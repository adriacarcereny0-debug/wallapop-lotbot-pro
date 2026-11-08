// Renderiza anuncio.html fotograma a fotograma (1080x1920, 30 fps).
const { chromium } = require('playwright');
const path = require('path'), fs = require('fs');
const [,, outDir = 'frames', fps = 30, dur = 40, only] = process.argv;
(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 540, height: 960 }, deviceScaleFactor: 2 });
  await p.goto('file://' + path.resolve(__dirname, 'anuncio.html') + '?t=0');
  await p.waitForTimeout(500);
  const times = only ? only.split(',').map(Number) : [...Array(fps * dur).keys()].map(i => i / fps);
  for (let i = 0; i < times.length; i++) {
    await p.evaluate(t => render(t), times[i]);
    await p.screenshot({ path: `${outDir}/f${String(i).padStart(5, '0')}.jpg`, type: 'jpeg', quality: 92 });
  }
  await b.close();
})();
