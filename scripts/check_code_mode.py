"""Exercise compact discovery and a data-only batch through a real packaged MCP process."""
import json,os,subprocess,sys,tempfile,threading
from pathlib import Path
from queue import Queue
ROOT=Path(__file__).resolve().parents[1]
def check(command,environment):
    proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',env=environment,cwd=ROOT)
    responses=Queue()
    def read():
        for line in proc.stdout:responses.put(json.loads(line))
    reader=threading.Thread(target=read,daemon=True);reader.start()
    def call(rid,method,params=None):
        proc.stdin.write(json.dumps({'jsonrpc':'2.0','id':rid,'method':method,'params':params or {}})+'\n');proc.stdin.flush()
        response=responses.get(timeout=60);assert response.get('id')==rid,response;assert 'error' not in response,response;return response['result']
    try:
        call(1,'initialize',{'protocolVersion':'2025-06-18'})
        listing=call(2,'tools/list');assert [x['name'] for x in listing['tools']]==['search','execute'],listing
        operation='workflow_format'
        discovered=call(3,'tools/call',{'name':'search','arguments':{'query':operation}});assert not discovered['isError'],discovered
        result=call(4,'tools/call',{'name':'execute','arguments':{'calls':[{'name':operation},{'name':operation}],'max_calls':2}})
        assert not result['isError'],result
        value=result['structuredContent'];assert value['ok'] and value['api_calls']==2 and value['sandbox']=='data-only-batch',value
        print('PASS: packaged compact MCP discovery and two-call portable batch')
    finally:
        proc.stdin.close();proc.wait(timeout=60);proc.stdout.close();stderr=proc.stderr.read();proc.stderr.close();reader.join(timeout=5)
        assert proc.returncode==0,stderr
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='compact-mcp-') as directory:
        p=Path(directory)
        pass
        check([sys.argv[1], '-c', 'import sys; sys.path.insert(0, '+repr(str(ROOT))+'); from flowengine.__main__ import main; raise SystemExit(main())', 'mcp', '--workspace', str(p)],{**os.environ,'FLOWENGINE_HOME':str(p/'home')})
