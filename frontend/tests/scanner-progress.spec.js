import { test, expect } from '@playwright/test'
test('scan shows progress and releases loading after a broker failure', async ({ page }, info) => {
  await page.route('**/api/session', r => r.fulfill({json:{connected:true}}))
  await page.route('**/api/scanner/results', r => r.fulfill({json:{results:[],config:{},last_run:null}}))
  await page.route('**/api/scanner/run', r => r.fulfill({status:202,json:{job_id:'fixture'}}))
  let polls=0
  await page.route('**/api/scanner/run/fixture', r => r.fulfill({json: ++polls === 1 ? {status:'running',stage:'Loading candle history',processed:10,total:50} : {status:'failed',error:'Kite candle requests made no progress; previous results preserved.'}}))
  await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.goto('/')
  if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click()
  await page.getByRole('button',{name:/Signals/}).click()
  await page.getByRole('button',{name:/Run scan now/}).click()
  await expect(page.getByRole('status')).toContainText('10 / 50 stocks processed')
  await expect(page.getByText('Kite candle requests made no progress; previous results preserved.')).toBeVisible()
  await expect(page.getByRole('button',{name:/Run scan now/})).toBeEnabled()
})

test('saved scan displays invalid quote exclusions', async ({ page }, info) => {
  await page.route('**/api/session', r => r.fulfill({json:{connected:true}}))
  await page.route('**/api/scanner/results', r => r.fulfill({json:{results:[],config:{},last_run:{ran_at:'2026-09-07T11:00:00+05:30',skipped_quotes:[{symbol:'AMANTA-BE',reason:'Invalid live quote field: open'}]}}}))
  await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.goto('/')
  if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click()
  await page.getByRole('button',{name:/Signals/}).click()
  await page.getByText('1 symbols excluded: unavailable or invalid live quotes').click()
  await expect(page.getByText('AMANTA-BE: Invalid live quote field: open')).toBeVisible()
})
