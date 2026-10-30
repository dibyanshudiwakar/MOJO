// Render the self-contained HTML to PDF using the local Chrome via puppeteer,
// waiting until client-side mermaid rendering has completed.
const path = require('path');
const puppeteer = require('puppeteer');

(async () => {
  const htmlPath = path.resolve(__dirname, '..', 'DESIGN_DOCUMENT.html');
  const pdfPath = path.resolve(__dirname, '..', 'DESIGN_DOCUMENT.pdf');
  const browser = await puppeteer.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.goto('file:///' + htmlPath.replace(/\\/g, '/'), { waitUntil: 'networkidle0', timeout: 120000 });
  await page.waitForFunction('window.__mermaidDone === true', { timeout: 120000 });
  // settle any late layout
  await new Promise(r => setTimeout(r, 1500));
  await page.pdf({
    path: pdfPath,
    format: 'A4',
    printBackground: true,
    margin: { top: '14mm', bottom: '16mm', left: '0mm', right: '0mm' },
    displayHeaderFooter: true,
    headerTemplate: '<div style="font-size:8px;color:#888;width:100%;padding:0 12mm;text-align:right;">ACE Migration Factory — Design Document</div>',
    footerTemplate: '<div style="font-size:8px;color:#888;width:100%;padding:0 12mm;display:flex;justify-content:space-between;"><span>CVS Health AI Platform — Confidential</span><span>Page <span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
  });
  await browser.close();
  console.log('PDF written:', pdfPath);
})().catch(e => { console.error(e); process.exit(1); });
