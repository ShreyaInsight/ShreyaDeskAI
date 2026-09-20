from unittest.mock import patch
from kiteconnect.exceptions import InputException
from backend import autotrade as a, gtt
from backend.tests.test_gtt import GTTTests

class ModificationTests(GTTTests):
    def prepare_change(self):
        _,pid=self.buy()
        a.save_config({'settings_mode':'live','live_sl_pct':5,'live_tp_pct':15,'live_exit_rule':'whichever_first'})
        return pid
    def modify(self,gid,**request):
        self.assertEqual(gtt.position(self.pid)['current_sl'],90)
        row=self.broker.gtts[str(gid)]
        row['condition']['trigger_values']=request['trigger_values']
        row['orders']=[{**o,'result':None} for o in request['orders']]
        return {'trigger_id':int(gid)}
    def test_verified_update_then_local_commit(self):
        self.pid=self.prepare_change()
        with patch.object(self.broker,'modify_gtt',side_effect=self.modify,create=True) as modify:
            result=gtt.apply_existing('APPLY LIVE SL TP')
        self.assertEqual(result['results'][0]['status'],'UPDATED')
        self.assertEqual(gtt.position(self.pid)['current_sl'],95)
        self.assertEqual(gtt.position(self.pid)['current_tp'],115)
        self.assertIsNone(gtt.modification(self.pid));modify.assert_called_once()
    def test_rejection_preserves_original_levels(self):
        self.pid=self.prepare_change()
        with patch.object(self.broker,'modify_gtt',side_effect=InputException('Rejected'),create=True):
            result=gtt.apply_existing('APPLY LIVE SL TP')
        self.assertEqual(result['results'][0]['status'],'FAILED')
        self.assertEqual(gtt.position(self.pid)['current_sl'],90)
        self.assertIsNone(gtt.modification(self.pid))
        self.assertEqual(gtt.get(self.pid)['state'],'ACTIVE')
    def test_uncertain_update_is_visible_and_blocks_sell_until_reconciled(self):
        self.pid=self.prepare_change()
        def uncertain(gid,**request):
            self.modify(gid,**request)
            raise TimeoutError('Lost acknowledgement')
        original=self.broker.get_gtt
        calls=0
        def read(gid):
            nonlocal calls
            calls+=1
            if calls>1:raise TimeoutError('Read unavailable')
            return original(gid)
        with patch.object(self.broker,'modify_gtt',side_effect=uncertain,create=True) as modify,patch.object(self.broker,'get_gtt',side_effect=read):
            result=gtt.apply_existing('APPLY LIVE SL TP')
        self.assertEqual(result['results'][0]['status'],'FAILED')
        self.assertEqual(gtt.position(self.pid)['current_sl'],90)
        self.assertEqual(gtt.public(self.pid)['gtt_status'],'MODIFYING')
        with self.assertRaises(ValueError):gtt.cancel_before_exit(self.pid,'manual',self.broker)
        gtt.reconcile_all()
        self.assertEqual(gtt.position(self.pid)['current_sl'],95)
        self.assertIsNone(gtt.modification(self.pid));modify.assert_called_once()
    def test_confirmation_required(self):
        with self.assertRaises(ValueError):gtt.apply_existing('')
    def test_original_trigger_race_reconciles_without_rewriting_levels(self):
        self.pid=self.prepare_change()
        def raced(gid,**request):
            self.broker.trigger(gid)
            raise TimeoutError('Trigger raced with update')
        with patch.object(self.broker,'modify_gtt',side_effect=raced,create=True):
            result=gtt.apply_existing('APPLY LIVE SL TP')
        self.assertEqual(result['results'][0]['status'],'FAILED')
        self.assertEqual(gtt.position(self.pid)['current_sl'],90)
        self.assertIsNone(gtt.modification(self.pid))
    def test_invalid_prices_do_not_modify(self):
        self.pid=self.prepare_change();self.broker.price=80
        with patch.object(self.broker,'modify_gtt',create=True) as modify:
            result=gtt.apply_existing('APPLY LIVE SL TP')
        self.assertEqual(result['results'][0]['status'],'FAILED')
        modify.assert_not_called();self.assertEqual(gtt.position(self.pid)['current_sl'],90)
