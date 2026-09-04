import { chromium } from 'playwright'

const consoleUrl = process.env.CONSOLE_URL?.replace(/\/$/, '')
const password = process.env.ACM_NONE_PASS || 'Acm39327!'
const idpName = process.env.ACM_IDP_NAME || 'acm39327-htpasswd'
const users = (process.env.ACM_NONE_USERS || '01')
  .trim()
  .split(/\s+/)
  .map((i) => (i.includes('acm-none-') ? i : `acm-none-${i.padStart(2, '0')}`))
const inventoryPath = '/multicloud/infrastructure/environments'
if (!consoleUrl) throw new Error('Set CONSOLE_URL')

async function selectHtpasswdIdp(page) {
  await page.getByText(/Log in with/i).first().waitFor({ state: 'visible', timeout: 120000 })
  const idp = page
    .locator('a, button, [role="link"], [role="button"]')
    .filter({
      hasText: new RegExp(`^\\s*${idpName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*$`, 'i'),
    })
    .first()
  if (await idp.isVisible({ timeout: 15000 }).catch(() => false)) {
    await idp.click()
  } else {
    await page.getByText(idpName, { exact: true }).first().click({ timeout: 15000 })
  }
  await page.locator('input[name="username"], input#inputUsername').first().waitFor({
    state: 'visible',
    timeout: 60000,
  })
}

async function fillUserPass(page, username) {
  const user = page.locator('input[name="username"], input#inputUsername').first()
  const pass = page.locator('input[name="password"], input#inputPassword').first()
  await user.waitFor({ state: 'visible', timeout: 60000 })
  await user.fill(username)
  await pass.fill(password)
  await page.getByRole('button', { name: /Log in|Log In|Sign in/i }).first().click()
}

async function loginAndOpenInventory(browser, username) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true })
  const page = await context.newPage()
  await page.goto(consoleUrl, { waitUntil: 'domcontentloaded', timeout: 120000 })
  await selectHtpasswdIdp(page)
  await fillUserPass(page, username)
  await page.waitForURL(/console-openshift-console|multicloud|dashboards/i, { timeout: 120000 }).catch(() => {})
  await page.goto(`${consoleUrl}${inventoryPath}`, { waitUntil: 'domcontentloaded', timeout: 120000 })
  console.log(`[${new Date().toISOString()}] ${username} → ${inventoryPath}`)
  return { context, page }
}

const browser = await chromium.launch({ headless: false })
for (const user of users) {
  await loginAndOpenInventory(browser, user)
}
console.log(`Opened ${users.length} session(s). Ctrl+C when measurement is done.`)
await new Promise(() => {})
