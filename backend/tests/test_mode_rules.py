import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import autotrade as a

class ModeRulesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.p=patch.object(a,'DB_PATH',Path(self.temp.name)/'rules.db');self.p.start()
        self.r=patch('backend.risk.public_state',return_value={});self.r.start()
        c=a.connection()
        c.execute('INSERT INTO autotrade_config VALUES (1,?,?)',(json.dumps({'limits_version':2,'mode':'live','sl_pct':2,'tp_pct':5,'exit_rule':'scanner_exit'}),a.now()));c.commit();c.close()
    def tearDown(self):self.r.stop();self.p.stop();self.temp.cleanup()
    def test_migration_and_bidirectional_save_isolation(self):
        cfg=a.get_config()
        self.assertEqual(cfg['paper_exit_rule'], 'scanner_exit')
        self.assertEqual(cfg['live_sl_pct'],2)
        cfg=a.save_config({'settings_mode':'paper','paper_sl_pct':9,'paper_exit_rule':'risk_levels_only','max_symbol_value':1,'live_tp_pct':99})
        self.assertEqual(cfg['mode'],'live');self.assertEqual(cfg['sl_pct'],2)
        self.assertEqual(cfg['live_tp_pct'],5);self.assertEqual(cfg['max_symbol_value'],1000)
        cfg=a.save_config({'settings_mode':'live','live_exit_rule':'whichever_first','live_tp_pct':12,'paper_sl_pct':1,'per_trade_pct':99})
        self.assertEqual(cfg['paper_sl_pct'],9);self.assertEqual(cfg['per_trade_pct'],2)
        self.assertEqual(cfg['exit_rule'],'whichever_first')
        cfg=a.save_config({**cfg,'mode':'paper'},internal=True)
        self.assertEqual(cfg['sl_pct'],9)
        self.assertEqual(a.config_for_mode(cfg,'live')['tp_pct'],12)
        self.assertTrue(cfg['paused']);self.assertFalse(cfg['enabled'])
    def test_inactive_mode_validation(self):
        with self.assertRaises(ValueError):a.save_config({'settings_mode':'paper','paper_sl_pct':float('nan')})
