import {test,expect} from '@playwright/test'
test('Live and Demo forms save only their own entry and exit rules',async({page},info)=>{
  let cfg={mode:'live',enabled:false,paused:true,live_sl_pct:2,live_tp_pct:5,live_exit_rule:'scanner_exit',paper_sl_pct:9,paper_tp_pct:12,paper_exit_rule:'risk_levels_only',max_symbol_value:1000,max_concurrent_positions:20,max_daily_order_count:5,max_cumulative_loss:2500,per_trade_pct:2,paper_max_positions:10,paper_max_deployed_pct:50}
  let saved
  await page.route('**/api/autotrade/config',r=>{if(r.request().method()==='POST'){saved=r.request().postDataJSON();cfg={...cfg,...saved}}return r.fulfill({json:cfg})})
  await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
  await page.goto('/')
  async function nav(label){if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click();await page.getByRole('button',{name:new RegExp(label)}).click()}
  await nav('Live Trading')
  await expect(page.getByLabel('Live stop loss %')).toHaveValue('2')
  await page.getByLabel('Live exit rule').selectOption('whichever_first')
  await page.getByRole('button',{name:'Save limits and pause'}).click()
  await expect.poll(()=>saved?.live_exit_rule).toBe('whichever_first')
  expect(saved.settings_mode).toBe('live');expect(saved).not.toHaveProperty('paper_sl_pct')
  await nav('Demo Trading')
  await expect(page.getByLabel('Stop loss %',{exact:true})).toHaveValue('9')
  await page.getByLabel('Stop loss %',{exact:true}).fill('8')
  await page.getByRole('button',{name:'Save demo settings'}).click()
  await expect.poll(()=>saved?.paper_sl_pct).toBe(8)
  expect(saved.settings_mode).toBe('paper');expect(saved).not.toHaveProperty('live_exit_rule')
})
