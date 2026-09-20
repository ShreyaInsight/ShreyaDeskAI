import {test,expect} from '@playwright/test'
test('existing protection action requires confirmation and displays per-position results',async({page},info)=>{
 let submitted
 await page.route('**/api/autotrade/config',r=>r.fulfill({json:{mode:'live',enabled:false,paused:true,live_sl_pct:5,live_tp_pct:15,live_exit_rule:'whichever_first'}}))
 await page.route('**/api/autotrade/apply-existing-protection',r=>{submitted=r.request().postDataJSON();return r.fulfill({json:{results:[{position_id:1,symbol:'APP',status:'UPDATED',sl:95,tp:115},{position_id:2,symbol:'OTHER',status:'FAILED',reason:'Outstanding order: reconcile before changing protection'}]}})})
 await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.goto('/')
 if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click()
 await page.getByRole('button',{name:/Live Trading/}).click()
 const button=page.getByRole('button',{name:'Apply to existing positions',exact:true})
 await expect(button).toBeDisabled()
 await page.getByLabel('Apply existing protection confirmation').fill('APPLY LIVE SL TP')
 await button.click()
 await expect(page.getByText('Updated — SL ₹95, TP ₹115',{exact:false})).toBeVisible()
 await expect(page.getByText('Outstanding order: reconcile before changing protection',{exact:false})).toBeVisible()
 expect(submitted).toEqual({confirm_phrase:'APPLY LIVE SL TP'})
 await expect(button).toBeDisabled()
})
