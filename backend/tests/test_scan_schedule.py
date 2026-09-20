import unittest
from unittest.mock import patch
from backend import main,scanner,supertrend_scan

class ScheduleTests(unittest.TestCase):
 def test_cron_schedule_and_old_jobs_removed(self):
  jobs={j.id:j for j in scanner.scheduler.get_jobs()}
  self.assertNotIn('occ-daily',jobs);self.assertNotIn('occ-confirmed-next-session',jobs)
  for name,hour in [('occ-history-cache','9'),('scanner-noon','12'),('scanner-afternoon','15')]:
   self.assertEqual(str(jobs[name].trigger.timezone),'Asia/Kolkata')
   fields={f.name:str(f) for f in jobs[name].trigger.fields}
   self.assertEqual(fields['hour'],hour);self.assertEqual(fields['minute'],'0')
  self.assertFalse(jobs['scanner-noon'].kwargs['execute'])
  self.assertTrue(jobs['scanner-afternoon'].kwargs['execute'])
 def test_noon_job_never_requests_execution_and_manual_does(self):
  for source,expected in [('scheduled-1200',False),('manual',True),('scheduled-1500',False)]:
   with patch.object(main,'scanner_jobs',{'test':{'source':source,'day':'2000-01-01'}}),patch.object(scanner,'run_scan',return_value={}) as run:
    main._run_scanner_job('test')
    self.assertEqual(run.call_args.kwargs['execute'],expected)
 def test_manual_request_does_not_reuse_noon_job(self):
  with patch.object(main,'scanner_jobs',{'noon':{'job_id':'noon','status':'running','source':'scheduled-1200'}}),patch.object(main.scanner_executor,'submit'):
   job=main.run_scanner()
   self.assertNotEqual(job['job_id'],'noon')
 def test_supertrend_dispatch_carries_no_execution_flag(self):
  with patch.object(scanner,'get_config',return_value={'strategy':'supertrend'}),patch.object(supertrend_scan,'run',return_value={}) as run:
   scanner.run_scan(execute=False)
   self.assertFalse(run.call_args.kwargs['execute'])
 def test_occ_noon_scan_does_not_call_order_processing(self):
  import tempfile
  from pathlib import Path
  from contextlib import ExitStack
  from backend import market_cache,autotrade
  with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
   stack.enter_context(patch.object(scanner,'DB_PATH',Path(tmp)/'scanner.db'))
   for obj,name,value in [(scanner,'get_config',{**scanner.DEFAULT_CONFIG,'strategy':'occ','use_alternate_resolution':False}),(scanner,'get_kite',None),(scanner,'scan_universe',({},{},[])),(market_cache,'warm_full_universe',None),(market_cache,'refresh_history',None),(market_cache,'read_histories',{}),(market_cache,'live_quotes',{}),(scanner.fundamentals_executor,'submit',None)]:
    stack.enter_context(patch.object(obj,name,return_value=value))
   process=stack.enter_context(patch.object(autotrade,'process_signals'))
   result=scanner.run_scan(execute=False)
   self.assertEqual(result['results'],[]);process.assert_not_called()
