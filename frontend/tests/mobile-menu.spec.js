import {test,expect} from '@playwright/test'
test('navigation icons and mobile disconnect',async({page},info)=>{
 await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.goto('/')
 if(info.project.name==='desktop'){
  await expect(page.getByRole('button',{name:'Open navigation menu'})).toBeHidden()
  await expect(page.locator('.sidebar').getByRole('button',{name:'Disconnect'})).toBeVisible()
  return
 }
 await page.getByRole('button',{name:'Open navigation menu'}).click()
 const menu=page.locator('.mobile-menu-overlay')
 await expect(menu).toBeVisible()
 await expect(menu.locator('.mobile-menu-item svg')).toHaveCount(8)
 await expect(menu.locator('.mobile-menu-item').first()).toHaveText('Overview')
 expect(await menu.evaluate(el=>getComputedStyle(el).backdropFilter)).toContain('blur')
 await menu.getByRole('button',{name:'Signals',exact:true}).click()
 await expect(menu).toBeHidden()
 await page.getByRole('button',{name:'Open navigation menu'}).click()
 await page.waitForTimeout(500)
 await page.screenshot({path:'/tmp/shreyadesk-mobile-menu.png'})
 await menu.getByRole('button',{name:'Disconnect',exact:true}).click()
 await expect(page.locator('.auth-layout')).toBeVisible()
 await expect(menu).toBeHidden()
})
