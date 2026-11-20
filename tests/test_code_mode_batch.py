"""Portable Code Mode contract: runs on Windows/macOS/Linux without QuickJS."""
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest import mock
# Load this module by path: portable contract tests don't require the whole engine's optional dependencies.
spec=importlib.util.spec_from_file_location('portable_code_mode',Path(__file__).resolve().parents[1]/'flowengine'/'code_mode.py')
code_mode=importlib.util.module_from_spec(spec);spec.loader.exec_module(code_mode)
SPECS=[{'name':'echo','description':'Echo a value','inputSchema':{'type':'object','properties':{'value':{}},'required':['value'],'additionalProperties':False}}, {'name':'denied','description':'Denied','inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]
class PortableBatchTests(unittest.TestCase):
 def setUp(self):self.seen=[]
 def invoke(self,name,args):
  if name=='denied':raise PermissionError('permission-required')
  self.seen.append(args);return args['value']
 def run_batch(self,calls,**kwargs):return code_mode.execute(SPECS,{'calls':calls,**kwargs},self.invoke)
 def test_data_only_composition_references_and_projection(self):
  r=self.run_batch([{'name':'echo','args':{'value':{'secret':'omit','x':[42]}} ,'select':'/x/0'},{'name':'echo','args':{'value':{'$result':0,'path':'/x/0'}}}])
  self.assertTrue(r['ok'],r);self.assertEqual(r['result'],[42,42]);self.assertNotIn('secret',str(r));self.assertEqual(r['sandbox'],'data-only-batch')
 def test_works_on_windows_macos_and_frozen_without_quickjs(self):
  for platform in ('win32','darwin','linux'):
   with mock.patch.object(code_mode.sys,'platform',platform),mock.patch.object(code_mode.sys,'frozen',True,create=True):
    self.assertTrue(self.run_batch([{'name':'echo','args':{'value':'ok'}}])['ok'])
 def test_permissions_partial_failure_and_no_replay(self):
  r=self.run_batch([{'name':'echo','args':{'value':1}},{'name':'denied'},{'name':'echo','args':{'value':2}}])
  self.assertFalse(r['ok']);self.assertEqual(r['result'],[1]);self.assertEqual(len(self.seen),1);self.assertEqual(r['api_calls'],2)
 def test_limits_and_allowlist_preflight(self):
  for kwargs in ({'max_calls':1},{'allowed_tools':['denied']}):
   with self.assertRaises(ValueError):self.run_batch([{'name':'echo','args':{'value':1}}]*2,**kwargs)
  self.assertFalse(self.seen)
 def test_unknown_tool_and_recursion_are_rejected(self):
  for name in ('execute','search','missing'):
   with self.assertRaises(ValueError):self.run_batch([{'name':name}])
  self.assertFalse(self.seen)
 def test_schema_validation(self):
  r=self.run_batch([{'name':'echo','args':{'bad':1}}]);self.assertFalse(r['ok']);self.assertFalse(self.seen)
 def test_no_forward_negative_or_boolean_reference(self):
  for index in (-1,True,0,10):
   r=self.run_batch([{'name':'echo','args':{'value':{'$result':index}}}]);self.assertFalse(r['ok']);self.assertFalse(self.seen)
 def test_invalid_pointer_never_replays_completed_call(self):
  r=self.run_batch([{'name':'echo','args':{'value':1},'select':'/missing'}]);self.assertFalse(r['ok']);self.assertEqual(r['calls'][0]['status'],'complete');self.assertEqual(len(self.seen),1)
 def test_output_is_bounded(self):
  r=self.run_batch([{'name':'echo','args':{'value':'x'*10000}}],max_output_chars=100);self.assertTrue(r['result']['truncated'])
 def test_pre_cancelled_batch(self):
  event=threading.Event();event.set();r=code_mode.execute(SPECS,{'calls':[{'name':'echo','args':{'value':1}}]},self.invoke,event);self.assertFalse(r['ok']);self.assertFalse(self.seen)
 def test_invalid_union_and_input_limits(self):
  for args in ({},{'code':'return 1;','calls':[]},{'calls':[]},{'calls':[{'name':'echo','extra':1}]}):
   with self.assertRaises(Exception):code_mode.execute(SPECS,args,self.invoke)
 def test_search_and_native_schema_are_available(self):
  self.assertEqual(code_mode.search(SPECS,{'query':'echo'})['matches'][0]['inputSchema'],SPECS[0]['inputSchema'])
if __name__=='__main__':unittest.main()
