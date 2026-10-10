const { chromium } = require('@playwright/test');
const fs = require('fs');
(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || (process.platform === 'darwin' ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : undefined), headless: true,
    args: ['--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  });
  const context = await browser.newContext({ viewport: { width: 1300, height: 950 } });
  const page = await context.newPage();
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    if (route.request().url() === 'https://offline.test/') {
      return route.fulfill({ status: 200, contentType: 'text/html', body: fs.readFileSync(process.argv[2] || 'docs/verification/offline-fixture.html', 'utf8') });
    }
    requests.push(route.request().url());
    return route.abort();
  });
  await page.goto('https://offline.test/');
  await page.locator('#verify').click();
  await page.waitForFunction(() => document.querySelector('#integrity').textContent.includes('哈希匹配'));
  await page.locator('#scene-part').selectOption('1');
  await page.locator('#scene-explode').fill('1.5');
  await page.locator('#scene-explode').dispatchEvent('input');
  await page.locator('#scene3d').scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'docs/verification/offline-qa.png', fullPage: false });
  const result = {
    pageErrors: errors, unexpectedRequests: requests,
    flowCount: await page.locator('#flow svg').count(), relationCount: await page.locator('#relations svg').count(),
    lineCount: await page.locator('#charts polyline').count(), tableCount: await page.locator('#charts table').count(),
    renderer: await page.locator('#scene3d').getAttribute('data-renderer'),
    sceneStatus: await page.locator('#scene-status').textContent(),
    selected: await page.locator('#scene-selection').textContent(),
    integrity: await page.locator('#integrity').textContent(),
  };
  fs.writeFileSync('docs/verification/offline-qa-result.json', JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
  if (errors.length || requests.length || result.flowCount !== 2 || result.renderer !== 'webgl-mesh') process.exit(1);
})().catch(error => { console.error(error.message); process.exit(1); });
