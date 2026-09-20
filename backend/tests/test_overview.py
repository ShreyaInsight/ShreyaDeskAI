import unittest
from unittest.mock import Mock, patch
from backend import autotrade as a
from backend.overview import snapshot
from backend.tests import test_risk_api as base

class OverviewTests(unittest.TestCase):
    setUp=base.RiskApiTests.setUp
    tearDown=base.RiskApiTests.tearDown
    login=base.RiskApiTests.login

    def seed(self):
        conn=a.connection()
        for symbol,mode,status,pnl in [('APP','live','OPEN',5),('WIN','live','CLOSED',20),('LOSS','live','CLOSED',-10),('PERSONAL_DEMO','paper','OPEN',None)]:
            conn.execute('INSERT INTO autotrade_positions(symbol,mode,status,entry_time,entry_price,quantity,current_sl,current_tp,realized_pnl) VALUES (?,?,?,?,100,2,90,120,?)',(symbol,mode,status,a.now(),pnl))
        for mode,status in [('live','OPEN'),('live','COMPLETE'),('live','BLOCKED'),('paper','FILLED')]:
            conn.execute("INSERT INTO autotrade_orders(timestamp,symbol,signal_source,mode,order_type,requested_qty,status) VALUES (?,'APP','BUY',?,'LIMIT',2,?)",(a.now(),mode,status))
        conn.commit();conn.close()

    def test_quotes_without_scanner_and_mode_isolation(self):
        self.seed()
        broker=Mock();broker.quote.return_value={'NSE:APP':{'last_price':110,'ohlc':{'close':100}}}
        with patch.object(a,'get_config',return_value={'mode':'live','enabled':False,'paused':True}),patch.object(a,'get_kite',return_value=broker):
            result=snapshot()
        self.assertEqual(result['metrics']['currentPnl'],20)
        self.assertEqual(result['metrics']['cumulativePnl'],35)
        self.assertEqual(result['metrics']['winRate'],50)
        self.assertEqual(result['metrics']['openOrders'],1)
        self.assertEqual(result['metrics']['closedTrades'],2)
        self.assertTrue(all(o['mode']=='live' for o in result['orders']))
        broker.quote.assert_called_once_with(['NSE:APP'])

    def test_missing_quotes_not_zero_and_empty_account_zero(self):
        broker=Mock();broker.quote.side_effect=RuntimeError('offline')
        with patch.object(a,'get_config',return_value={'mode':'live','enabled':False,'paused':True}),patch.object(a,'get_kite',return_value=broker):
            self.assertEqual(snapshot()['metrics']['currentPnl'],0)
            self.seed()
            result=snapshot()
        self.assertIsNone(result['metrics']['currentPnl'])
        self.assertIsNone(result['metrics']['cumulativePnl'])
        self.assertEqual(result['metrics']['winRate'],50)
        self.assertTrue(result['warnings'])

    def test_endpoint_requires_auth(self):
        self.assertEqual(self.client.get('/api/overview').status_code,401)
        self.login()
        self.assertEqual(self.client.get('/api/overview').status_code,200)
