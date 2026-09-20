import {test, expect} from '@playwright/test'
import fs from 'node:fs/promises'

test('GTT protection status, failure logs, and trade timeline are visible', async ({page}, info) => {
  const stamp='2026-09-07T10:00:00+05:30'
  const events=[{id:1,event:'GTT Set successfully',message:'ACTIVE',timestamp:stamp},{id:2,event:'GTT TP triggered',message:'Broker SELL order 888; awaiting fill confirmation',timestamp:stamp}]
  await page.route('**/api/session',r=>r.fulfill({json:{connected:true}}))
  await page.route('**/api/analytics?mode=*',r=>r.fulfill({json:{mode:'live',orders:[{id:1,symbol:'TEST',action:'BUY',requested_qty:2,filled_quantity:2,mode:'live',timestamp:stamp,gtt_message:'GTT Set successfully',position_id:1}],trades:[{id:1,symbol:'TEST',mode:'live',status:'OPEN',entry_time:stamp,timestamp:stamp,entry_price:100,quantity:2,gtt_timeline:events}],logs:[{id:'gtt-3',symbol:'FAILED',mode:'live',action:'GTT',outcome_type:'FAILED',outcome:'Unprotected position: GTT rejected',timestamp:stamp}],warnings:['FAILED: Unprotected position: GTT rejected'],updated_at:stamp}}))
  await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.goto('/')
  if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click()
  await page.getByRole('button',{name:/Analytics/}).click()
  await expect(page.getByRole('cell',{name:'GTT Set successfully',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Trade History',exact:true}).click()
  await page.getByText('GTT events (2)',{exact:true}).click()
  await expect(page.getByText('GTT TP triggered',{exact:true})).toBeVisible()
  const waiting=page.waitForEvent('download')
  await page.getByRole('button',{name:'Export CSV'}).click()
  const file=await waiting
  expect(await fs.readFile(await file.path(),'utf8')).toContain('GTT TP triggered')
  await page.getByRole('button',{name:'Order Logs',exact:true}).click()
  await expect(page.getByRole('cell',{name:'Unprotected position: GTT rejected',exact:true})).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})

test('live protection recovery requires typed confirmation and the broker ID for an uncertain placement', async ({page},info) => {
  let state='UNKNOWN', requests=[]
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  const config={mode:'live',enabled:false,paused:true,kill_switch:false,max_symbol_value:1000,max_concurrent_positions:20,max_daily_order_count:5,max_cumulative_loss:2500,sl_pct:2,tp_pct:5,exit_rule:'whichever_first'}
  await page.route('**/api/session',r=>r.fulfill({json:{connected:true}}))
  await page.route('**/api/autotrade/config',r=>r.fulfill({json:config}))
  await page.route('**/api/autotrade/positions',r=>r.fulfill({json:{positions:[{id:1,symbol:'TEST',mode:'live',status:'OPEN',quantity:2,entry_price:100,current_sl:98,current_tp:105,gtt_status:state,gtt_id:state==='ACTIVE'?'101':null,gtt_message:state==='ACTIVE'?'GTT Set successfully':'GTT acceptance unknown: verify in Kite'}]}}))
  for(const path of ['orders','open-orders'])await page.route(`**/api/autotrade/${path}`,r=>r.fulfill({json:{orders:[]}}))
  await page.route('**/api/autotrade/stats',r=>r.fulfill({json:{}}))
  await page.route('**/api/scanner/results',r=>r.fulfill({json:{results:[]}}))
  await page.route('**/api/autotrade/positions/1/gtt/reconcile',r=>{requests.push(r.request().postDataJSON());state='ACTIVE';return r.fulfill({json:{gtt_status:state}})})
  await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.goto('/')
  if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click()
  await page.getByRole('button',{name:/Live Trading/}).click()
  await expect(page.getByRole('alert').filter({hasText:'GTT acceptance unknown'})).toBeVisible()
  await page.getByText('Reconcile protection',{exact:true}).click()
  await expect(page.getByRole('button',{name:'Reconcile GTT',exact:true})).toBeDisabled()
  await page.getByLabel('GTT confirmation for TEST').fill('RECONCILE GTT')
  await expect(page.getByRole('button',{name:'Reconcile GTT',exact:true})).toBeDisabled()
  await page.getByLabel('Broker GTT ID for TEST').fill('101')
  await page.getByRole('button',{name:'Reconcile GTT',exact:true}).click()
  await expect(page.getByText('GTT Set successfully',{exact:true})).toBeVisible()
  expect(requests).toEqual([{confirm_phrase:'RECONCILE GTT',trigger_id:101}])
  await page.screenshot({path:`/tmp/gtt-live-${info.project.name}.jpg`,type:'jpeg',quality:60,scale:'css'})
  expect(errors).toEqual([])
})
