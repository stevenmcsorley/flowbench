import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import pytest
from flowengine import code_mode

pytestmark = pytest.mark.skipif(sys.platform != "linux" or importlib.util.find_spec("quickjs") is None, reason="Optional Linux Code Mode runtime is not installed")

SPECS=[{'name':'echo','description':'Return text for a test','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}}, {'name':'deny','description':'Denied operation','inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]
def invoke(name,args):
    if name=='deny':raise PermissionError('Permission required')
    return {'text':args['text']}
def run(code,**kwargs):return code_mode.execute(SPECS,{'code':code,**kwargs},invoke)

def test_discovery_without_running():
    assert [s['name'] for s in code_mode.tool_specs()]==['search','execute']
    assert code_mode.search(SPECS,{'query':'echo'})['matches'][0]['inputSchema']==SPECS[0]['inputSchema']
    assert 'inputSchema' not in code_mode.search(SPECS,{'query':''})['matches'][0]
    assert code_mode.search(SPECS,{'query':'zzzzzz'})['total']==0

@pytest.mark.parametrize('args',[{}, {'code':'x','timeout_ms':True}, {'code':'x','max_calls':101}, {'code':'x','unknown':True}])
def test_invalid_arguments(args):
    with pytest.raises(ValueError):code_mode.execute(SPECS,args,invoke)

def test_composition_and_projection():
    r=run("const a=await tools.echo({text:input.text});const b=await tools.call('echo',{text:a.text+'!'});return b.text;",input={'text':'hi'})
    assert r['ok'],r
    assert r['result']=='hi!' and r['api_calls']==2

def test_caught_error_and_native_permission():
    r=run("try{await tools.deny({});}catch(e){return e.message;}")
    assert r['ok'] and 'Permission required' in r['result']
    assert r['calls'][0]['status']=='failed'

def test_schema_validation_before_native_dispatch():
    called=[]
    r=code_mode.execute(SPECS,{'code':"return await tools.echo({text:23});"},lambda *a:called.append(a))
    assert not r['ok'] and not called

def test_allowlist_and_no_recursive_tools():
    assert not run("return await tools.call('deny',{});",allowed_tools=['echo'])['ok']
    assert not run("return await tools.call('execute',{});")['ok']

def test_calls_partial_failure_and_no_retry():
    r=run("await tools.echo({text:'committed'});await tools.deny({});await tools.echo({text:'never'});")
    assert not r['ok'] and r['api_calls']==2 and r['calls'][0]['status']=='complete'
    r=run("await tools.echo({text:'a'});await tools.echo({text:'b'});",max_calls=1)
    assert not r['ok'] and r['api_calls']==1

def test_output_limits_cpu_and_unresolved_promises():
    r=run("return 'x'.repeat(10000);",max_output_chars=100)
    assert r['ok'] and r['result']['truncated']
    assert not run('while(true){}')['ok']
    assert not run('return await new Promise(()=>{});')['ok']

def test_cancel_and_deadline():
    event=threading.Event();event.set()
    r=code_mode.execute(SPECS,{'code':'return 1;'},invoke,event)
    assert not r['ok'] and r['api_calls']==0
    def slow(name,args):
        import time
        time.sleep(.15)
        return {}
    r=code_mode.execute(SPECS,{'code':"await tools.echo({text:'x'});await tools.echo({text:'y'});",'timeout_ms':100},slow)
    assert not r['ok'] and r['api_calls']<=1

def test_js_has_no_ambient_host_access():
    r=run("return [typeof fetch,typeof process,typeof require,typeof std,typeof os];")
    assert r['ok'] and r['result']==['undefined']*5

def test_process_level_file_and_socket_denial():
    child=Path(code_mode.__file__).with_name('code_sandbox.py')
    p=subprocess.run([sys.executable,'-I',str(child)],input=json.dumps({'probe':True})+'\n',text=True,capture_output=True,timeout=10)
    value=json.loads(p.stdout)
    assert value['file_open']==-1 and value['file_errno']==errno.EPERM
    assert value['socket']==-1 and value['socket_errno']==errno.EPERM

def test_flowbench_compact_full_and_existing_permission_gates(tmp_path):
    from flowengine.mcp import Server
    s=Server(tmp_path,mcp_mode='code')
    assert [x['name'] for x in s.tool_specs()]==['search','execute']
    assert len(s.tool_specs(full=True))>10
    def call(name,args):return s.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})['result']
    found=call('search',{'query':'workflow_format'})['structuredContent']
    assert found['matches'][0]['name']=='workflow_format'
    r=call('execute',{'code':'return await tools.workflow_format({});'})
    assert not r['isError'],r
    assert r['structuredContent']['result']['document']['format']=='flowengine.workflow'
    r=call('execute',{'code':"return await tools.write_text_file({path:'blocked.txt',text:'hello'});"})
    assert r['isError'] and not (tmp_path/'blocked.txt').exists()
    # Direct native calls remain compatible even with compact discovery.
    assert not call('workflow_format',{})['isError']

def test_flowbench_authorized_file_write_and_workspace_boundary(tmp_path):
    from flowengine.mcp import Server
    s=Server(tmp_path,permissions=('file_writes',),mcp_mode='code')
    def run(code):return s.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'execute','arguments':{'code':code}}})['result']
    result=run("await tools.write_text_file({path:'notes.txt',text:'composed'}); return (await tools.list_files({})).entries.length;")
    assert not result['isError'],result
    assert (tmp_path/'notes.txt').read_text()=='composed'
    result=run("return await tools.write_text_file({path:'../escaped.txt',text:'bad'});")
    assert result['isError'] and not (tmp_path.parent/'escaped.txt').exists()
