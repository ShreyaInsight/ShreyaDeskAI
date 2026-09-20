import copy
import unittest
from unittest.mock import patch
from kiteconnect.exceptions import InputException
from backend import autotrade as a, gtt, risk, analytics
from backend.tests import test_autotrade_safety as base


class GTTBroker(base.Broker):
    def __init__(self):
        super().__init__()
        self.gtts={};self.gtt_calls=[];self.deleted=[];self.histories={};self.fail=None;self.race=False
    def place_gtt(self, **request):
        self.gtt_calls.append(request)
        if self.fail=='reject': raise InputException('Rejected')
        gid=str(100+len(self.gtt_calls))
        self.gtts[gid]=dict(id=int(gid),type='two-leg',status='active',condition=dict(exchange='NSE',tradingsymbol=request['tradingsymbol'],trigger_values=request['trigger_values']),orders=[{**o,'result':None} for o in request['orders']],expires_at='2027-09-01 00:00:00')
        if self.fail=='timeout': raise TimeoutError('response lost after acceptance')
        return {'trigger_id':int(gid)}
    def get_gtt(self, gid): return copy.deepcopy(self.gtts[str(gid)])
    def get_gtts(self): return [self.get_gtt(gid) for gid in self.gtts]
    def delete_gtt(self,gid):
        self.deleted.append(gid)
        if self.race:self.trigger(gid)
        else:self.gtts[str(gid)]['status']='deleted'
        return {'trigger_id':int(gid)}
    def trigger(self,gid,leg=0,failed=False):
        row=self.gtts[str(gid)];row['status']='triggered'
        row['orders'][leg]['result']={'timestamp':a.now(),'order_result':{'status':'failed' if failed else 'success','order_id':'' if failed else 'gtt-sell','rejection_reason':'authorization required' if failed else None}}
    def order_history(self,oid): return [self.histories.get(str(oid),self.history)]


class GTTTests(unittest.TestCase):
    setUp = base.SafetyTests.setUp
    tearDown = base.SafetyTests.tearDown
    configure = base.SafetyTests.configure
    def setUp(self):
        base.SafetyTests.setUp(self)
        self.broker=GTTBroker()
        p=patch.object(a,'get_kite',return_value=self.broker);p.start();self.patches.append(p)
        p=patch('backend.market_cache.instruments',return_value=[dict(tradingsymbol='TEST',segment='NSE',tick_size=.05)]);p.start();self.patches.append(p)
        self.configure(sl_pct=10,tp_pct=20)
    def buy(self,qty=2,status='COMPLETE',filled=None):
        self.broker.history={'status':status,'filled_quantity':qty if filled is None else filled,'average_price':100}
        oid=a._record_live_order('TEST','scanner signal','BUY',qty,100)
        conn=a.connection();row=dict(conn.execute('SELECT * FROM autotrade_orders WHERE id=?',(oid,)).fetchone());conn.close()
        return oid,row['position_id']
    def test_actual_fill_quantity_levels_once_and_no_buy_allowance_for_gtt(self):
        oid,pid=self.buy(qty=5,filled=2)
        self.assertEqual(gtt.position(pid)['quantity'],2)
        request=self.broker.gtt_calls[0]
        self.assertEqual(request['trigger_values'],[90,120])
        self.assertEqual([o['quantity'] for o in request['orders']],[2,2])
        self.assertTrue(all(o['order_type']=='LIMIT' and o['transaction_type']=='SELL' for o in request['orders']))
        a.reconcile_order(oid);gtt.ensure(pid,self.broker)
        self.assertEqual(len(self.broker.gtt_calls),1)
        conn=a.connection();self.assertEqual(conn.execute('SELECT count(*) FROM autotrade_buy_reservations').fetchone()[0],1);conn.close()
    def test_open_partial_then_cancel_tracks_and_protects_actual_quantity(self):
        oid,pid=self.buy(qty=5,status='OPEN',filled=2)
        self.assertEqual(gtt.position(pid)['quantity'],2);self.assertEqual(self.broker.gtt_calls,[])
        self.broker.history['status']='CANCELLED';a.reconcile_order(oid)
        self.assertEqual(self.broker.gtt_calls[0]['orders'][0]['quantity'],2)
        self.assertEqual(a.reconcile_order(oid)['status'],'CANCELLED')
    def test_scanner_only_no_gtt_and_monitor_never_submits_price_exit(self):
        self.configure(exit_rule='scanner_exit')
        _,pid=self.buy();self.broker.price=1
        a.monitor_live_positions()
        self.assertEqual(self.broker.gtt_calls,[]);self.assertEqual(len(self.broker.calls),1)
        self.assertEqual(gtt.get(pid)['state'],'NOT_REQUIRED')
    def test_timeout_is_not_retried_and_can_attach_verified_id(self):
        self.broker.fail='timeout';_,pid=self.buy()
        gtt.ensure(pid,self.broker);self.assertEqual(len(self.broker.gtt_calls),1)
        self.assertEqual(gtt.get(pid)['state'],'UNKNOWN')
        with self.assertRaises(ValueError):gtt.resolve(pid,None,'RECONCILE GTT')
        a._record_live_order('TEST','manual close','SELL',2,None,position_id=pid)
        self.assertEqual(len(self.broker.calls),1)
        self.assertEqual(gtt.resolve(pid,101,'RECONCILE GTT')['gtt_status'],'ACTIVE')
    def test_explicit_rejection_retries_three_and_logs_alert(self):
        self.broker.fail='reject';_,pid=self.buy();gtt.ensure(pid,self.broker)
        self.assertEqual(len(self.broker.gtt_calls),3)
        self.assertEqual(gtt.get(pid)['state'],'FAILED')
        report=analytics.snapshot('live')
        self.assertTrue(report['warnings']);self.assertTrue(any(r['action']=='GTT' and r['outcome_type']=='FAILED' for r in report['logs']))
    def test_cancel_before_manual_and_scanner_sell(self):
        for source in ['manual close','scanner exit']:
            with self.subTest(source=source):
                # Separate fixtures are not needed: close/reopen gets a new broker ID.
                base.Clock.value += base.timedelta(seconds=1)
                _,pid=self.buy()
                self.broker.history={'status':'COMPLETE','filled_quantity':2,'average_price':100}
                oid=a._record_live_order('TEST',source,'SELL',2,None,position_id=pid)
                self.assertTrue(self.broker.deleted)
                self.assertEqual(gtt.position(pid)['status'],'CLOSED')
    def test_trigger_during_cancel_blocks_competing_sell(self):
        _,pid=self.buy();self.broker.race=True
        self.broker.histories['gtt-sell']={'status':'OPEN','filled_quantity':0,'average_price':0}
        a._record_live_order('TEST','manual close','SELL',2,None,position_id=pid)
        self.assertEqual(len(self.broker.calls),1)
        self.assertEqual(gtt.position(pid)['status'],'OPEN')
    def test_trigger_reconciles_when_paused_and_fallback_fetches_old_id(self):
        _,pid=self.buy();self.broker.trigger('101',leg=1)
        self.broker.histories['gtt-sell']={'status':'COMPLETE','filled_quantity':2,'average_price':120}
        a.pause()
        with patch.object(self.broker,'get_gtts',return_value=[]):gtt.reconcile_all()
        self.assertEqual(gtt.position(pid)['status'],'CLOSED')
        self.assertEqual(gtt.position(pid)['exit_reason'],'SELL / GTT TP')
        self.assertEqual(gtt.position(pid)['realized_pnl'],40)
        gtt.reconcile_all();self.assertEqual(len(self.broker.calls),1)
    def test_trigger_rejection_not_a_closed_position(self):
        _,pid=self.buy();self.broker.trigger('101',failed=True);gtt.reconcile_all()
        self.assertEqual(gtt.position(pid)['status'],'OPEN')
        self.assertEqual(gtt.get(pid)['state'],'TRIGGER_FAILED')
    def test_partial_sell_realized_and_remaining_unrealized_are_idempotent(self):
        _,pid=self.buy(qty=5)
        self.broker.history={'status':'OPEN','filled_quantity':2,'average_price':90}
        local=a._record_live_order('TEST','manual close','SELL',5,None,position_id=pid)
        self.assertEqual(gtt.position(pid)['quantity'],3)
        self.assertEqual(risk.app_reading(self.broker),-20)
        a.reconcile_order(local);self.assertEqual(risk.app_reading(self.broker),-20)
        self.broker.history['status']='CANCELLED';a.reconcile_order(local)
        self.broker.history={'status':'COMPLETE','filled_quantity':3,'average_price':120}
        a._record_live_order('TEST','manual close','SELL',3,None,position_id=pid)
        self.assertEqual(gtt.position(pid)['quantity'],5)
        self.assertEqual(gtt.position(pid)['realized_pnl'],40)
        self.assertEqual(risk.app_reading(self.broker),40)

    def test_partial_fill_day_history_uses_quantity_at_that_day(self):
        _,pid=self.buy(qty=5)
        base.Clock.value += base.timedelta(days=1)
        self.broker.history={'status':'CANCELLED','filled_quantity':2,'average_price':90}
        a._record_live_order('TEST','manual close','SELL',5,None,position_id=pid)
        from backend.fill_accounting import positions_at_day
        current=gtt.position(pid)
        first=positions_at_day([current],base.Clock.value.date()-base.timedelta(days=1))[0]
        second=positions_at_day([current],base.Clock.value.date())[0]
        self.assertEqual(first['quantity'],5);self.assertEqual(first['realized_pnl'],0)
        self.assertEqual(second['quantity'],3);self.assertEqual(second['realized_pnl'],-20)

    def test_disabled_or_expired_trigger_is_visible_and_does_not_reappear_automatically(self):
        _,pid=self.buy();self.broker.gtts['101']['status']='expired'
        gtt.reconcile_all();gtt.ensure(pid,self.broker)
        self.assertEqual(gtt.get(pid)['state'],'EXPIRED');self.assertEqual(len(self.broker.gtt_calls),1)
        result=gtt.resolve(pid,None,'RECONCILE GTT')
        self.assertEqual(result['gtt_status'],'ACTIVE');self.assertEqual(len(self.broker.gtt_calls),2)

    def test_unverified_cancel_and_modified_broker_gtt_block_sell(self):
        _,pid=self.buy()
        with patch.object(self.broker,'delete_gtt',side_effect=TimeoutError('unconfirmed')):
            a._record_live_order('TEST','manual close','SELL',2,None,position_id=pid)
        self.assertEqual(len(self.broker.calls),1)
        self.broker.gtts['101']['orders'][0]['quantity']=200
        a._record_live_order('TEST','manual close','SELL',2,None,position_id=pid)
        self.assertEqual(len(self.broker.calls),1)

    def test_partial_exit_after_baseline_excludes_earlier_unrealized_loss(self):
        _,pid=self.buy(qty=5)
        self.broker.price=95
        risk.clear(risk.CLEAR_CONFIRMATION)
        self.broker.history={'status':'CANCELLED','filled_quantity':2,'average_price':90}
        a._record_live_order('TEST','manual close','SELL',5,None,position_id=pid)
        self.assertEqual(risk.app_reading(self.broker),-35)
        risk.check(self.broker,block_buy=False)
        self.assertAlmostEqual(risk.public_state()['app_cumulative_pnl'],-10)

    def test_gtt_sell_is_not_subject_to_buy_symbol_cap(self):
        _,pid=self.buy(qty=2)
        self.configure(max_symbol_value=1)
        gtt.ensure(pid,self.broker)
        self.assertEqual(self.broker.gtt_calls[0]['orders'][1]['quantity'],2)
        self.broker.trigger('101',leg=1)
        self.broker.histories['gtt-sell']={'status':'COMPLETE','filled_quantity':2,'average_price':120}
        gtt.reconcile_all()
        self.assertEqual(gtt.position(pid)['status'],'CLOSED')

    def test_existing_protection_survives_switch_to_paper_and_kill_switch(self):
        _,pid=self.buy()
        self.configure(mode='paper',exit_rule='scanner_exit')
        risk.latch(manual=True,kite=self.broker)
        gtt.reconcile_all()
        self.assertEqual(self.broker.deleted,[])
        self.assertEqual(gtt.get(pid)['state'],'ACTIVE')

    def test_all_sell_fills_wait_for_complete_before_closing(self):
        _,pid=self.buy(qty=2)
        self.broker.history={'status':'OPEN','filled_quantity':2,'average_price':90}
        oid=a._record_live_order('TEST','manual close','SELL',2,None,position_id=pid)
        self.assertEqual(gtt.position(pid)['status'],'EXIT_PENDING')
        self.assertEqual(risk.app_reading(self.broker),-20)
        self.broker.history['status']='COMPLETE'
        a.reconcile_order(oid)
        self.assertEqual(gtt.position(pid)['status'],'CLOSED')
        self.assertEqual(risk.app_reading(self.broker),-20)
