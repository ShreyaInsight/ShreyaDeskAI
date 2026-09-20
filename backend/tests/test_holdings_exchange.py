import unittest
from backend.holdings import normalize_holdings, split_inventory

class ExchangeOwnershipTests(unittest.TestCase):
    def test_bse_delivery_matches_nse_app_by_isin(self):
        app=[dict(id=16,symbol='TATAPOWER',quantity=1,entry_price=366.3),dict(id=17,symbol='EXCELSOFT',quantity=6,entry_price=80.65)]
        holdings=[dict(exchange='BSE',tradingsymbol=p['symbol'],isin='ISIN'+p['symbol'],quantity=p['quantity'],average_price=p['entry_price']) for p in app]
        official=[{'SYMBOL':p['symbol'],'SERIES':'EQ','ISIN NUMBER':'ISIN'+p['symbol']} for p in app]
        instruments=[dict(tradingsymbol=p['symbol'],exchange='NSE',segment='NSE',instrument_type='EQ',instrument_token=p['id']) for p in app]
        normalized=normalize_holdings(holdings,app,official,instruments)
        result=split_inventory(normalized,[],app,{})
        self.assertEqual(result['warnings'],[])
        self.assertEqual(result['personal'],[])
        self.assertTrue(all(h['exchange']=='BSE' for h in holdings))
        holdings[0]['quantity']=0
        result=split_inventory(normalize_holdings(holdings,app,official,instruments),[],app,{})
        self.assertEqual(len(result['warnings']),1)
        self.assertIn('TATAPOWER',result['warnings'][0])
        holdings[0]['quantity']=1;holdings[0]['isin']='WRONG'
        result=split_inventory(normalize_holdings(holdings,app,official,instruments),[],app,{})
        self.assertEqual(len(result['warnings']),1)
