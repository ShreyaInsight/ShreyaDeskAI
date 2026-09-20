import {test,expect} from '@playwright/test'
import {readFile} from 'node:fs/promises'
import {holdingsCsv,sortHoldings} from '../src/holdingsExport.js'
test('CSV escaping, numeric sorting and missing values',()=>{
 const rows=[{symbol:'B',pnl:null},{symbol:'A',pnl:-10},{symbol:'C',pnl:2}]
 expect(sortHoldings(rows,{key:'pnl',direction:'desc'},{}).map(r=>r.symbol)).toEqual(['C','A','B'])
 const csv=holdingsCsv([{symbol:'=formula',note:'a,"quote"\nnext',pnl:-10}],{},'occ','algo','today')
 expect(csv).toContain('"\'=formula"');expect(csv).toContain('"a,""quote""\nnext"');expect(csv).toContain('"-10"')
})
test('holdings headers sort and each tab exports its own data',async({page},info)=>{
 const row=(symbol,quantity)=>({symbol,exchange:'NSE',quantity,average_cost:10,ltp:12,pnl:quantity*2})
 await page.route('**/api/holdings',r=>r.fulfill({json:{algo:[row('AAA',10),row('BBB',2)],personal:[row('PERSONAL',3)],warnings:[],updated_at:'2026-09-13T10:00:00Z'}}))
 await page.route('**/api/holdings/signal?*',r=>r.fulfill({json:{signal_type:'BUY',trigger_date:'2026-09-11'}}))
 await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.goto('/')
 if(info.project.name==='mobile')await page.getByRole('button',{name:'Open navigation menu'}).click()
 await page.getByRole('button',{name:'Holdings',exact:true}).click()
 await page.getByRole('button',{name:'Quantity',exact:true}).click()
 const rows=page.locator('.holdings-page tbody tr')
 await expect(rows.first()).toContainText('BBB')
 await page.getByRole('button',{name:'Quantity',exact:true}).click()
 await expect(rows.first()).toContainText('AAA')
 for(const mode of ['algo','personal']){
  if(mode==='personal')await page.getByRole('button',{name:/Personal holdings/}).click()
  const waiting=page.waitForEvent('download')
  await page.getByRole('button',{name:'Export as CSV',exact:true}).click()
  const download=await waiting
  expect(download.suggestedFilename()).toMatch(new RegExp(`^${mode}-holdings-`))
  const csv=await readFile(await download.path(),'utf8')
  expect(csv).toContain(mode==='algo'?'AAA':'PERSONAL')
  expect(csv).not.toContain(mode==='algo'?'PERSONAL':'AAA')
 }
})
