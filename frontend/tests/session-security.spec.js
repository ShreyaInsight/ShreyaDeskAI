import {test,expect} from '@playwright/test'

async function login(page, mobile) {
  await page.goto('/')
  if(mobile)await page.getByRole('button',{name:'Enter dashboard credentials'}).click()
  await page.getByRole('textbox',{name:'Dashboard username'}).fill('fixture-user')
  await page.getByLabel('Dashboard password').fill('fixture-password')
  await page.getByRole('button',{name:'Sign in securely'}).click()
  await expect(page.getByRole('heading',{name:'Overview',exact:true})).toBeVisible()
}

test('second device returns from unfinished Kite login blocked, then completes its own callback', async ({page,browser},info)=>{
  const mobile=info.project.name==='mobile'
  await login(page,mobile)
  const second=await browser.newContext({baseURL:'http://127.0.0.1:8766',userAgent:'Second device test browser',viewport:mobile?{width:390,height:844}:{width:1280,height:900},isMobile:mobile,hasTouch:mobile})
  const other=await second.newPage()
  const errors=[]
  other.on('pageerror',error=>errors.push(error.message))
  await login(other,mobile)
  await other.getByRole('button',{name:'Open User profile'}).filter({visible:true}).click()
  await expect(other.getByRole('heading',{name:'Session activity'})).toBeVisible()
  await expect(other.getByRole('cell',{name:'Second device test browser',exact:true}).first()).toBeVisible()
  await other.screenshot({path:info.outputPath('session-activity.png'),fullPage:true})
  await other.getByRole('link',{name:'Reconnect to Kite'}).click()
  await expect(other.getByRole('heading',{name:/Fixture Kite login/})).toBeVisible()
  const query=new URL(other.url()).searchParams
  const state=new URLSearchParams(query.get('redirect_params')).get('state')
  expect(state).toBeTruthy()
  const callback=`http://127.0.0.1:8766/api/kite/callback?status=success&request_token=fixture&state=${encodeURIComponent(state)}`
  await other.goBack()
  await expect(other.getByText('Kite login is awaiting completion for this dashboard session. Returning with Back does not complete it.')).toBeVisible()
  await expect(other.locator('.app-shell')).toHaveCount(0)
  await other.screenshot({path:info.outputPath('pending-kite.png'),fullPage:true})
  expect(await other.evaluate(async()=> (await fetch('/api/profile',{cache:'no-store'})).status)).toBe(403)
  await page.reload()
  await expect(page.getByRole('heading',{name:'Overview',exact:true})).toBeVisible()
  await other.goto(callback)
  await expect(other.getByRole('heading',{name:'Overview',exact:true})).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading',{name:'Overview',exact:true})).toBeVisible()
  expect(errors).toEqual([])
  await second.close()
})

test('bfcache pageshow clears a stale dashboard when server session is no longer valid',async({page},info)=>{
  await login(page,info.project.name==='mobile')
  let checks=0
  await page.route('**/api/session',route=>{checks++;return route.fulfill({status:401,json:{detail:'Authentication required.'}})})
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})))
  await expect(page.locator('.app-shell')).toHaveCount(0)
  await expect(page.locator('.auth-layout')).toBeVisible()
  expect(checks).toBeGreaterThan(0)
})

test('returning to the tab preserves signal sort without reloading results',async({page},info)=>{
  let resultCalls=0
  await page.route('**/api/scanner/results',route=>{resultCalls++;return route.fulfill({json:{config:{},last_run:null,results:[{symbol:'HIGH',signal_type:'BUY',trigger_date:'2026-09-08',trigger_price:100,current_price:200},{symbol:'LOW',signal_type:'BUY',trigger_date:'2026-09-07',trigger_price:100,current_price:50}]}})})
  await login(page,info.project.name==='mobile')
  if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click()
  await page.getByRole('button',{name:/Signals/}).click()
  await page.getByRole('button',{name:'Current price',exact:true}).click()
  await expect(page.locator('.occ-table tbody tr').first()).toContainText('LOW')
  const before=resultCalls
  await page.evaluate(()=>{
    window.savedSignalTable=document.querySelector('.occ-table')
    Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'})
    document.dispatchEvent(new Event('visibilitychange'))
  })
  const checked=page.waitForResponse(r=>r.url().endsWith('/api/session'))
  await page.evaluate(()=>{
    Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'})
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await checked
  await expect(page.locator('.occ-table')).toBeVisible()
  await expect(page.locator('.occ-table tbody tr').first()).toContainText('LOW')
  expect(await page.evaluate(()=>window.savedSignalTable===document.querySelector('.occ-table'))).toBe(true)
  expect(resultCalls).toBe(before)
})
