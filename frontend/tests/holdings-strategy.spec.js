import {test,expect} from '@playwright/test'
for (const strategy of ['occ','supertrend']) {
  test(`Holdings displays ${strategy} signals in both ownership modes`,async({page},info)=>{
    const label=strategy==='occ'?'OCC':'Supertrend'
    await page.route('**/api/scanner/config',r=>r.fulfill({json:{strategy}}))
    const row={symbol:'TEST',exchange:'NSE',quantity:2,average_cost:100,ltp:110,pnl:20}
    await page.route('**/api/holdings',r=>r.fulfill({json:{algo:[row],personal:[row],warnings:[]}}))
    await page.route('**/api/holdings/signal?*',r=>r.fulfill({json:{strategy,signal_type:'BUY',trigger_date:'2026-09-09'}}))
    await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
    await page.goto('/')
    if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click()
    await page.getByRole('button',{name:/Holdings/}).click()
    await expect(page.locator('.holdings-page th').filter({hasText:`${label} signal`})).toBeVisible()
    await expect(page.locator('.holdings-page .signal-badge')).toHaveText('BUY')
    await page.getByRole('button',{name:/Personal holdings/}).click()
    await expect(page.locator('.holdings-page .signal-badge')).toHaveText('BUY')
  })
}
