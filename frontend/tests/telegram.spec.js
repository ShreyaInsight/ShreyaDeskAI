import {test,expect} from '@playwright/test'
test('User Telegram settings and test delivery status',async({page})=>{
 let settings={enabled:true,orders:true,safety:true,connection:true,service:true,capacity:true,scanner:false,paper:true},tested=false
 const data=()=>({settings,configured:true,worker_running:true,queued:0,sent:0,failed:0,dropped:0,last_success:null,last_error:null,recent:[]})
 await page.route('**/api/telegram',r=>r.fulfill({json:data()}))
 await page.route('**/api/telegram/settings',r=>{settings=r.request().postDataJSON();return r.fulfill({json:data()})})
 await page.route('**/api/telegram/test',r=>{tested=true;return r.fulfill({json:{queued:true}})})
 await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.goto('/')
 await page.getByRole('button',{name:'Open User profile'}).click()
 await expect(page.getByRole('heading',{name:'Telegram notifications'})).toBeVisible()
 await page.getByLabel('Scanner completion summaries').check()
 await page.getByRole('button',{name:'Save Telegram settings'}).click()
 await expect(page.getByText('Notification settings saved.')).toBeVisible();expect(settings.scanner).toBe(true)
 await page.getByRole('button',{name:'Send test notification'}).click()
 await expect(page.getByText('Test queued. Check delivery status below.')).toBeVisible();expect(tested).toBe(true)
})
