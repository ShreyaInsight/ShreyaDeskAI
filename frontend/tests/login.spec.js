import { test, expect } from '@playwright/test'
const reveal = page => page.getByRole('button', { name: 'Enter dashboard credentials' })
const username = page => page.getByRole('textbox', { name: 'Dashboard username' })
const password = page => page.getByLabel('Dashboard password')

test('initial visibility follows viewport; reveal preserves login and errors', async ({ page }, info) => {
  await page.goto('/')
  if (info.project.name === 'mobile') {
    await expect(reveal(page)).toBeVisible()
    await expect(page.locator('.auth-intro')).toBeVisible()
    await expect(page.locator('.auth-panel')).toBeHidden()
    await page.screenshot({ path: info.outputPath('mobile-intro.png'), fullPage: true, scale: 'css' })
    await expect(username(page)).toBeHidden()
    await expect(password(page)).toBeHidden()
    await expect(page.getByRole('button', { name: 'Sign in securely' })).toBeHidden()
    await reveal(page).tap()
    await expect(reveal(page)).toHaveCount(0)
    await expect(username(page)).toBeFocused()
    await expect(page.locator('.auth-intro')).toBeHidden()
    await expect(page.locator('.auth-panel')).toBeVisible()
    await page.screenshot({ path: info.outputPath('mobile-credentials.png'), fullPage: true, scale: 'css' })
  } else {
    await expect(reveal(page)).toBeHidden()
    await expect(page.locator('.auth-intro')).toBeVisible()
    await expect(page.locator('.auth-panel')).toBeVisible()
  }
  await expect(username(page)).toBeVisible()
  await expect(password(page)).toBeVisible()
  await username(page).fill('fixture-user')
  await password(page).fill('wrong-password')
  await page.getByRole('button', { name: 'Sign in securely' }).click()
  await expect(page.getByText('Invalid username or password.')).toBeVisible()
  await password(page).fill('fixture-password')
  const response = page.waitForResponse(r => r.url().endsWith('/api/login') && r.status() === 200)
  await page.getByRole('button', { name: 'Sign in securely' }).click()
  await response
  // Successful dashboard login immediately revalidates the shared broker session.
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible()
})

test('breakpoint responds to resize and a fresh load resets disclosure', async ({ page }) => {
  await page.setViewportSize({ width: 720, height: 900 })
  await page.goto('/')
  await expect(reveal(page)).toBeVisible()
  await expect(username(page)).toBeHidden()
  await page.setViewportSize({ width: 721, height: 900 })
  await expect(reveal(page)).toBeHidden()
  await expect(username(page)).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(username(page)).toBeHidden()
  await reveal(page).click()
  await username(page).fill('draft')
  await page.setViewportSize({ width: 1280, height: 900 })
  await expect(username(page)).toHaveValue('draft')
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(username(page)).toBeVisible()
  await page.reload()
  await expect(reveal(page)).toBeVisible()
  await expect(username(page)).toBeHidden()
})

test('password-manager field values survive reveal and submit without React change events', async ({page},info)=>{
 await page.goto('/')
 await expect(page.locator('#dashboard-username')).toHaveAttribute('autocomplete','username')
 await expect(password(page)).toHaveAttribute('autocomplete','current-password')
 await expect(page.locator('#dashboard-username')).toHaveAttribute('name','username')
 await expect(password(page)).toHaveAttribute('name','password')
 // Simulate a manager writing native values without a React change event.
 await page.locator('#dashboard-credentials').evaluate(form=>{
  form.elements.username.value='fixture-user'
  form.elements.password.value='fixture-password'
 })
 if(info.project.name==='mobile'){
  await reveal(page).tap()
  await expect(page.getByRole('button',{name:'Sign in securely'})).toBeFocused()
 }
 await expect(username(page)).toHaveValue('fixture-user')
 await expect(password(page)).toHaveValue('fixture-password')
 await page.getByRole('button',{name:'Sign in securely'}).click()
 await expect(page.getByRole('heading',{name:'Overview',exact:true})).toBeVisible()
})
