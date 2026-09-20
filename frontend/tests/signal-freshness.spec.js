import { test, expect } from '@playwright/test'

test('current-session filter separates candidates from old crossovers and stale snapshots', async ({page}, info) => {
  const today = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
  const base = {occ_mode:'confirmed_3d',signal_type:'BUY',trigger_date:'2026-09-04',trigger_price:300,current_price:305,confirmed_eligible:true,execution_session:today}
  await page.route('**/api/session', r=>r.fulfill({json:{connected:true}}))
  await page.route('**/api/scanner/results', r=>r.fulfill({json:{config:{use_alternate_resolution:true,alternate_mode:'confirmed'},last_run:null,results:[{...base,symbol:'ANGELONE'},{...base,symbol:'OLD',confirmed_eligible:false,confirmed_reason:'Crossover is outside its next-session execution window'},{...base,symbol:'STALE',execution_session:'2020-01-01'}]}}))
  await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.goto('/')
  if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click()
  await page.getByRole('button',{name:/Signals/}).click()
  await expect(page.locator('tbody tr')).toHaveCount(3)
  await expect(page.getByRole('button',{name:'Crossover / confirmation date'})).toBeVisible()
  await page.getByLabel('Current-session candidates only').check()
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(page.locator('tbody tr')).toContainText('ANGELONE')
  await page.getByLabel('Current-session candidates only').uncheck()
  await expect(page.locator('tbody tr')).toHaveCount(3)
})
