import {test,expect} from '@playwright/test'
test('Kite connection gate uses a panel-only mobile layout',async({page},info)=>{
 await page.route('**/api/session',r=>r.fulfill({json:{connected:false,kite_pending:true}}))
 await page.goto('/')
 const gate=page.locator('.kite-connect-layout')
 await expect(gate.getByRole('heading',{name:'Connect to Kite',exact:true})).toBeVisible()
 if(info.project.name==='mobile'){
  await expect(gate.locator('.auth-intro')).toBeHidden()
  await expect(gate.getByRole('link',{name:'Connect to Kite'})).toBeInViewport()
  await expect(gate.getByRole('button',{name:'Sign out'})).toBeInViewport()
  await page.screenshot({path:'/tmp/kite-connect-mobile.png',scale:'css'})
  await page.setViewportSize({width:1000,height:900})
  await expect(gate.locator('.auth-intro')).toBeVisible()
  await page.setViewportSize({width:390,height:844})
  await expect(gate.locator('.auth-intro')).toBeHidden()
 }else{
  await expect(gate.locator('.auth-intro')).toBeVisible()
 }
 await expect(gate.getByRole('link',{name:'Connect to Kite'})).toHaveAttribute('href','/api/kite/login')
 await gate.getByRole('button',{name:'Check connection again'}).click()
 await expect(gate).toBeVisible()
})
