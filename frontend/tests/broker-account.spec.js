import {test,expect} from '@playwright/test'
test('account change retains the old account and blocks unsafe switching',async({page})=>{
 let pending=true
 await page.route('**/api/session',r=>r.fulfill({json:{authenticated:true,connected:!pending,account_change:pending?{from_account:'OWNER',to_account:'OTHER',legacy:0,blockers:{positions:1},expired:false}:null}}))
 await page.route('**/api/broker-account',r=>r.fulfill({json:{account_id:'OWNER',legacy:{total:0},error:null}}))
 await page.route('**/api/broker-account/switch',r=>{expect(r.request().postDataJSON().action).toBe('keep');pending=false;return r.fulfill({json:{resolved:true}})})
 await page.goto('/')
 await expect(page.getByRole('heading',{name:'Broker account changed'})).toBeVisible()
 await expect(page.getByRole('button',{name:'Confirm account switch'})).toBeDisabled()
 await page.getByRole('button',{name:'Keep original account'}).click()
 await expect(page.getByRole('heading',{name:'Broker account changed'})).toBeHidden()
})
test('legacy owner confirmation lists records and requires the exact phrase',async({page})=>{
 let total=1,submitted=false
 await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.route('**/api/broker-account',r=>r.fulfill({json:{account_id:'OWNER',error:null,legacy:{total},legacy_records:[{kind:'Position',id:17,symbol:'TEST',quantity:6,broker_ref:'verified-order',status:'OPEN'}]}}))
 await page.route('**/api/broker-account/bind-legacy',r=>{expect(r.request().postDataJSON()).toEqual({account_id:'OWNER',confirm_phrase:'BIND LEGACY RECORDS TO OWNER'});total=0;submitted=true;return r.fulfill({json:{account_id:'OWNER',error:null,legacy:{total:0}}})})
 await page.goto('/')
 const notice=page.getByRole('region',{name:'Broker account safety'})
 await expect(notice).toBeVisible()
 await notice.getByText('Review legacy records before confirming').click()
 await expect(notice.getByText('Position #17')).toBeVisible()
 await expect(notice.getByRole('button',{name:'Confirm legacy ownership'})).toBeDisabled()
 await notice.getByLabel('Legacy ownership confirmation').fill('BIND LEGACY RECORDS TO OWNER')
 await notice.getByRole('button',{name:'Confirm legacy ownership'}).click()
 await expect(notice).toBeHidden();expect(submitted).toBe(true)
})
test('empty workspace switch requires an exact typed confirmation',async({page})=>{
 let submitted=false
 await page.route('**/api/session',r=>r.fulfill({json:{authenticated:true,connected:false,account_change:{from_account:'OWNER',to_account:'OTHER',legacy:0,blockers:{positions:0,orders:0,gtts:0,modifications:0},expired:false}}}))
 await page.route('**/api/broker-account/switch',r=>{
  expect(r.request().postDataJSON()).toEqual({action:'switch',confirm_phrase:'SWITCH BROKER ACCOUNT TO OTHER'})
  submitted=true
  return r.fulfill({json:{resolved:true,paused:true}})
 })
 await page.goto('/')
 const button=page.getByRole('button',{name:'Confirm account switch'})
 await expect(button).toBeDisabled()
 await page.getByLabel('Account switch confirmation').fill('yes')
 await expect(button).toBeDisabled()
 await page.getByLabel('Account switch confirmation').fill('SWITCH BROKER ACCOUNT TO OTHER')
 await expect(button).toBeEnabled()
 await button.click()
 await expect.poll(()=>submitted).toBe(true)
})
test('User section keeps ownership settings visible after confirmation',async({page})=>{
 await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.route('**/api/broker-account',r=>r.fulfill({json:{account_id:'OWNER',error:null,legacy:{total:0},legacy_records:[]}}))
 await page.goto('/')
 await page.getByRole('button',{name:'Open User profile'}).filter({visible:true}).click()
 const panel=page.getByRole('region',{name:'Broker account safety'})
 await expect(panel.getByText('Broker account ownership',{exact:true})).toBeVisible()
 await expect(panel.getByText('Recorded account: OWNER')).toBeVisible()
 await expect(panel.getByText('No legacy records await ownership confirmation.')).toBeVisible()
})
test('User shows remaining risk failures even with verified ownership and refreshes them',async({page})=>{
 let riskError='Quotes unavailable'
 await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.route('**/api/broker-account',r=>r.fulfill({json:{account_id:'OWNER',error:null,legacy:{total:0},legacy_records:[],risk_history_error:riskError,risk_checked_at:'2026-09-14T10:00:00+05:30'}}))
 await page.goto('/')
 await page.getByRole('button',{name:'Open User profile'}).filter({visible:true}).click()
 const panel=page.getByRole('region',{name:'Broker account safety'})
 await expect(panel.getByText('No legacy records await ownership confirmation.')).toBeVisible()
 await expect(panel.getByRole('alert')).toHaveText('BUYs blocked: Quotes unavailable')
 riskError=null
 await panel.getByRole('button',{name:'Refresh account status'}).click()
 await expect(panel.getByRole('alert')).toHaveCount(0)
})
