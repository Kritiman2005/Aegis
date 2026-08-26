const puppeteer = require('puppeteer');

(async () => {
  try {
    const browser = await puppeteer.launch({
      headless: 'new'
    });
    const page = await browser.newPage();
    
    page.on('console', msg => console.log('PAGE LOG:', msg.text()));
    page.on('pageerror', error => console.error('PAGE ERROR:', error.message));

    await page.goto('file:///Users/kritimantalukdar/Aegis/Aegis/out/index.html', { waitUntil: 'networkidle0' });
    
    await browser.close();
  } catch (err) {
    console.error("Script failed:", err);
  }
})();
