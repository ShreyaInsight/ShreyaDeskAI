export function holdingsValue(row, key, signals) {
 const signal = signals[`${row.exchange}:${row.symbol}`]
 return key === 'signal_type' ? (!signal ? 'Loading…' : signal.error ? 'Unavailable' : signal.signal_type || 'No crossover') : key === 'trigger_date' ? signal?.trigger_date : row[key]
}
export function sortHoldings(rows, sort, signals) {
 return [...rows].sort((a,b)=>{
  const left=holdingsValue(a,sort.key,signals),right=holdingsValue(b,sort.key,signals)
  if(left==null)return right==null?0:1
  if(right==null)return -1
  const comparison=typeof left==='number'&&typeof right==='number'?left-right:String(left).localeCompare(String(right))
  return (sort.direction==='asc'?1:-1)*comparison
 })
}
export function holdingsCsv(rows, signals, strategy, mode, updatedAt) {
 const columns=['symbol','exchange','signal_type','trigger_date','average_cost','ltp','quantity','pnl','note']
 const cell=value=>{
  let text=String(value??'')
  if(typeof value==='string' && /^[\s]*[=+\-@]/.test(text))text="'"+text
  return '"'+text.replaceAll('"','""')+'"'
 }
 return '\uFEFF'+[['holding_type','strategy',...columns,'gtt_status','prices_fetched_at'],...rows.map(row=>[mode,strategy,...columns.map(key=>holdingsValue(row,key,signals)),row.gtt_protection?.map(g=>`${g.gtt_status}${g.gtt_id?` #${g.gtt_id}`:''}`).join('; '),updatedAt])].map(row=>row.map(cell).join(',')).join('\r\n')
}
