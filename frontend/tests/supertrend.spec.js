import {test,expect} from '@playwright/test'

test('switching strategies uses the same Signals tab with isolated results and chart',async({page},info)=>{
  let config={strategy:'occ',index_name:'NIFTY500',universe_mode:'index',ma_type:'SMMA',length:5,delay:0,offset_sigma:6,alma_offset:.85,pe_filter_operator:'none',st_atr_length:10,st_factor:3}
  const common={signal_type:'BUY',trigger_date:'2026-09-08',trigger_price:100,current_price:102}
  await page.route('**/api/scanner/config',r=>{if(r.request().method()==='POST')config=r.request().postDataJSON();return r.fulfill({json:config})})
  await page.route('**/api/scanner/results',r=>r.fulfill({json:{config,last_run:null,results:config.strategy==='occ'?[{...common,symbol:'OCC_ONLY'}]:[{...common,symbol:'ST_ONLY',strategy:'supertrend',direction:-1,supertrend_value:95}]}}))
  const bars=[{date:'2026-09-04',open:100,close:101,high:102,low:99,supertrend_value:103,direction:1,bullFlip:false,bearFlip:false},{date:'2026-09-07',open:103,close:105,high:106,low:102,supertrend_value:100,direction:-1,bullFlip:true,bearFlip:false},{date:'2026-09-08',open:100,close:98,high:101,low:97,supertrend_value:102,direction:1,bullFlip:false,bearFlip:true}]
  await page.route('**/api/scanner/chart/ST_ONLY?*',r=>r.fulfill({json:{symbol:'ST_ONLY',atr_length:10,factor:3,as_of:'2026-09-08',bars}}))
  await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
  await page.goto('/')
  async function navigate(label){if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click();await page.getByRole('button',{name:new RegExp(label)}).click()}
  await navigate('Signals');await expect(page.locator('.occ-table')).toContainText('OCC_ONLY')
  await navigate('Settings')
  await page.getByLabel('Scanner strategy').selectOption('supertrend')
  await expect(page.locator('details.settings-disclosure')).toHaveCount(6)
  await expect(page.locator('details.settings-disclosure[open]')).toHaveCount(0)
  for (const section of ['Risk management','Fundamental filters','RSI filters','Volume filters','Filters and data']) {
    await page.locator('summary').filter({hasText:section}).click()
  }
  await page.getByLabel('Stop loss %',{exact:true}).fill('3')
  await page.getByLabel('P/E value',{exact:true}).fill('25')
  await page.getByLabel('Exclude RSI below').fill('40')
  await page.getByLabel('Exclude volume below').fill('1000')
  await page.getByLabel('Lookback days').fill('365')
  await page.locator('summary').filter({hasText:'Supertrend settings'}).click()
  await expect(page.getByLabel('ATR length',{exact:true})).toHaveValue('10')
  await expect(page.getByLabel('Factor',{exact:true})).toHaveValue('3')
  await expect(page.getByLabel('MA period')).toHaveCount(0)
  await page.getByRole('button',{name:'Save settings'}).click()
  await expect(page.getByText('Settings saved')).toBeVisible()
  expect(config).toMatchObject({sl_pct:3,pe_filter_value:25,rsi_min:40,volume_min:1000,lookback_days:365})
  await navigate('Signals');await expect(page.getByRole('heading',{name:'Latest Supertrend flips.'})).toBeVisible()
  await expect(page.locator('.occ-table')).not.toContainText('OCC_ONLY')
  await expect(page.locator('.occ-table')).toContainText('ST_ONLY')
  await expect(page.getByRole('button',{name:'Stop loss',exact:true})).toHaveCount(1)
  await page.getByRole('button',{name:'Chart ST_ONLY'}).click()
  await expect(page.getByRole('img',{name:/daily candles with Supertrend/})).toBeVisible()
  await expect(page.getByTestId('bull-flip')).toHaveCount(1)
  await expect(page.getByTestId('bear-flip')).toHaveCount(1)
  await page.screenshot({path:info.outputPath('supertrend.png'),fullPage:true})
  await navigate('Settings');await page.getByLabel('Scanner strategy').selectOption('occ')
  await page.locator('summary').filter({hasText:'Moving average logic'}).click()
  await expect(page.getByLabel('MA period')).toHaveValue('5')
  await page.getByRole('button',{name:'Save settings'}).click();await expect(page.getByText('Settings saved')).toBeVisible()
  await navigate('Signals');await expect(page.locator('.occ-table')).toContainText('OCC_ONLY');await expect(page.locator('.occ-table')).not.toContainText('ST_ONLY')
})
