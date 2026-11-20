"""Small MCP surface; isolated JavaScript composes existing permission-checked operations.

No direct JS files/network/process access. Native operations retain their own timeouts;
wall deadlines are checked between calls and while waiting on the JS child.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
import uuid

SCHEMAS = {
    'search': {'type':'object','properties':{'query':{'type':'string','maxLength':200},'limit':{'type':'integer','minimum':1,'maximum':20}},'required':['query'],'additionalProperties':False},
    'execute': {'type':'object','properties':{'code':{'type':'string','minLength':1,'maxLength':32000},'calls':{'type':'array','minItems':1,'maxItems':100,'items':{'type':'object','properties':{'name':{'type':'string'},'args':{'type':'object'},'select':{'type':'string','maxLength':1000}},'required':['name'],'additionalProperties':False}},'input':{'type':'object'},'allowed_tools':{'type':'array','items':{'type':'string'},'maxItems':200},'timeout_ms':{'type':'integer','minimum':100,'maximum':120000},'max_calls':{'type':'integer','minimum':1,'maximum':100},'max_output_chars':{'type':'integer','minimum':100,'maximum':65536}},'oneOf':[{'required':['code']},{'required':['calls']}],'additionalProperties':False},
}
DESCRIPTIONS = {
    'search':'Discover native operations by task or exact name. Returns matching JSON schemas. Search before execute; do not guess arguments. Empty query browses names. No execution.',
    'execute':'All platforms: calls:[{name,args?,select?}] runs a bounded sequential batch; select is a JSON pointer. Args can reference earlier full results as {$result:0,path:"/field"}. Stops at first error; prior effects persist. Linux source also supports code: a JavaScript async-function body. Compose operations in an isolated JavaScript async-function body: await tools.operation(args), or tools.call(name,args). Return only needed fields. input is optional JSON. Existing permission gates apply. No direct file/network/process access. Defaults: 30s, 40 calls, 24000 output chars. Deadline/cancellation checked between native calls; native calls keep their own timeouts. Earlier effects persist on failure; inspect returned call trace, never auto-replay. Linux + optional requirements-code-mode.txt required.'
}
def tool_specs():
    return [{'name':name,'description':DESCRIPTIONS[name],'inputSchema':schema,'annotations':{'readOnlyHint':name=='search','destructiveHint':name=='execute','openWorldHint':name=='execute'}} for name,schema in SCHEMAS.items()]

def _validate(name,args):
    # Discovery stays usable without optional dependencies.
    schema=SCHEMAS[name]
    if not isinstance(args,dict) or set(args)-set(schema['properties']) or any(k not in args for k in schema.get('required',[])):
        raise ValueError('Missing required or unknown Code Mode arguments')
    if name=='execute' and (('code' in args)==('calls' in args)):raise ValueError('Supply exactly one of code or calls')
    for key,value in args.items():
        s=schema['properties'][key];kind=s['type']
        valid={'string':isinstance(value,str),'integer':isinstance(value,int) and not isinstance(value,bool),'object':isinstance(value,dict),'array':isinstance(value,list)}[kind]
        if not valid:raise ValueError('Invalid Code Mode argument: '+key)
        if kind=='string' and not s.get('minLength',0)<=len(value)<=s.get('maxLength',100000):raise ValueError('Invalid length: '+key)
        if kind=='integer' and not s['minimum']<=value<=s['maximum']:raise ValueError('Out of range: '+key)
        if kind=='array' and (not s.get('minItems',0)<=len(value)<=s.get('maxItems',200) or (key=='allowed_tools' and any(not isinstance(v,str) for v in value))):raise ValueError('Invalid array: '+key)

def search(specs,args):
    _validate('search',args)
    query=args['query'].strip().lower();words=re.findall(r'[\w]+',query)
    ranked=[]
    for spec in specs:
        name=spec['name'];text=(name+' '+spec.get('description','')).lower()
        score=1000 if name.lower()==query else sum(1 for w in words if w in text)
        if not words or score:ranked.append((score,spec))
    ranked.sort(key=lambda pair:(-pair[0],pair[1]['name']))
    limit=args.get('limit',6)
    # Browse names without loading every schema. Nonempty searches return complete selected schemas.
    entries=[{'name':s['name'],'description':s.get('description','')[:160]} if not query else s for _,s in ranked[:limit]]
    return {'matches':entries,'total':len(ranked),'returned':len(entries),'usage':'await tools.call(name, args); return only needed fields. Search exact names for full schemas.'}

def execute(specs,args,invoke,cancel_event=None):
    _validate('execute',args)
    if 'calls' in args:return execute_batch(specs,args,invoke,cancel_event)
    if sys.platform!='linux' or getattr(sys,'frozen',False):raise ValueError('Code Mode execution requires source Python on Linux; use execute calls for portable batches or --mcp-mode full on other builds.')
    try:
        import importlib.util
        import ctypes
        if importlib.util.find_spec('quickjs') is None:raise ImportError()
        import jsonschema
        ctypes.CDLL('libseccomp.so.2')
    except (ImportError,OSError):raise ValueError('Install requirements-code-mode.txt into this Python environment and Linux libseccomp2; execution fails closed.') from None
    by_name={s['name']:s for s in specs};allowed=args.get('allowed_tools',list(by_name))
    if any(name not in by_name for name in allowed):raise ValueError('Unknown operation in allowed_tools; discover it first.')
    allowed=set(allowed);calls=[];run_id=uuid.uuid4().hex;deadline=time.monotonic()+args.get('timeout_ms',30000)/1000
    process=None;selector=selectors.DefaultSelector();logs=[]
    def check():
        if cancel_event is not None and cancel_event.is_set():raise ValueError('Code Mode cancelled; earlier operations remain applied.')
        if time.monotonic()>=deadline:raise ValueError('Code Mode deadline exceeded; earlier operations remain applied.')
    def send(value):
        data=(json.dumps(value,ensure_ascii=True,allow_nan=False,separators=(',',':'))+'\n').encode()
        if len(data)>2*1024**2:raise ValueError('Bridge response exceeds 2 MB; use paginated native operations.')
        # Nonblocking writes prevent a script that never reads from hanging the host.
        view=memoryview(data)
        while view:
            check()
            try:view=view[os.write(process.stdin.fileno(),view):]
            except BlockingIOError:time.sleep(.005)
    def outcome(ok,result=None,error=None):
        out={'run_id':run_id,'ok':ok,'result':result,'calls':calls,'api_calls':len(calls),'logs':logs,'sandbox':'quickjs-ng+seccomp','note':'Not atomic; earlier operations remain applied. No automatic retry.'}
        if error:out['error']=str(error)[:2000]
        return out
    try:
        check()
        process=subprocess.Popen([sys.executable,'-I',str(Path(__file__).with_name('code_sandbox.py'))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONNOUSERSITE':'1'},start_new_session=True,close_fds=True)
        os.set_blocking(process.stdin.fileno(),False)
        selector.register(process.stdout,selectors.EVENT_READ)
        function=bool(re.match(r'^(?:async\s+)?(?:function\b|(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>)',args['code'].lstrip()))
        send({'operations':sorted(allowed),'input':args.get('input',{}),'code':args['code'],'function':function})
        buffer=b'';ready=False
        while True:
            check()
            if not selector.select(min(.05,max(.001,deadline-time.monotonic()))):
                if process.poll() is not None:raise ValueError('Sandbox exited: runtime/resource limit failure.')
                continue
            chunk=os.read(process.stdout.fileno(),65536)
            if not chunk:raise ValueError('Sandbox exited before returning a result (runtime or resource limit).')
            buffer+=chunk
            if len(buffer)>2*1024**2:raise ValueError('Sandbox output exceeds 2 MB.')
            while b'\n' in buffer:
                line,buffer=buffer.split(b'\n',1);message=json.loads(line)
                if message.get('type')=='ready':ready=True;continue
                if message.get('type')=='error':raise ValueError(message.get('error','Script failed'))
                if not ready:raise ValueError('Sandbox did not initialize securely.')
                if message.get('type')=='calls':
                    requests=message.get('calls');responses=[]
                    if not isinstance(requests,list) or not requests:raise ValueError('Invalid bridge requests')
                    for req in requests:
                        check()
                        if len(calls)>=args.get('max_calls',40):raise ValueError('API call limit exceeded.')
                        name=req.get('name');entry={'tool':str(name)[:120],'status':'started'};calls.append(entry)
                        try:
                            if name not in allowed:raise ValueError('Operation is not allowed; use search and allowed_tools.')
                            arguments=req.get('args')
                            jsonschema.validate(arguments,by_name[name]['inputSchema'])
                            value=invoke(name,arguments)
                            encoded=json.dumps(value,default=str,allow_nan=False)
                            if len(encoded)>1500000:raise ValueError('Native result exceeds 1.5 MB; paginate or filter.')
                            entry['status']='complete';responses.append({'id':req['id'],'ok':True,'value':json.loads(encoded)})
                        except Exception as exc:
                            entry['status']='failed'
                            responses.append({'id':req['id'],'ok':False,'error':str(exc)[:2000]})
                    send({'type':'results','results':responses});continue
                if message.get('type')=='done':
                    logs=message.get('logs',[])
                    if message.get('failure'):raise ValueError(message['failure']['message'])
                    value=message.get('result');encoded=json.dumps(value,ensure_ascii=True);limit=args.get('max_output_chars',24000)
                    if len(encoded)>limit:value={'truncated':True,'characters':len(encoded),'preview':encoded[:limit]}
                    return outcome(True,value)
                raise ValueError('Invalid sandbox message')
    except Exception as exc:return outcome(False,error=exc)
    finally:
        selector.close()
        if process:
            process.kill();process.wait();process.stdin.close();process.stdout.close()


def _pointer(value,path):
    if not isinstance(path,str) or (path and not path.startswith('/')):raise ValueError('Use an RFC 6901 JSON pointer, such as /field')
    for token in path.split('/')[1:] if path else []:
        token=token.replace('~1','/').replace('~0','~')
        if isinstance(value,list):
            if not re.fullmatch(r'0|[1-9][0-9]*',token):raise ValueError('Invalid array index in result pointer')
            value=value[int(token)]
        elif isinstance(value,dict):value=value[token]
        else:raise ValueError('Result pointer does not resolve')
    return value


def execute_batch(specs,args,invoke,cancel_event=None):
    """Portable data-only composition; never interprets or evaluates user code."""
    import jsonschema
    jsonschema.validate(args,SCHEMAS['execute'])
    by_name={s['name']:s for s in specs};allowed=set(args.get('allowed_tools',by_name))
    if allowed-set(by_name):raise ValueError('Unknown operation in allowed_tools')
    batch=args['calls'];max_calls=args.get('max_calls',40)
    if len(batch)>max_calls:raise ValueError('Batch exceeds max_calls; no operations ran')
    if any(call['name'] not in allowed for call in batch):raise ValueError('Batch contains an operation outside allowed_tools; no operations ran')
    if len(json.dumps(args,ensure_ascii=True))>256000:raise ValueError('Batch exceeds 256,000 characters; no operations ran')
    deadline=time.monotonic()+args.get('timeout_ms',30000)/1000
    full_results=[];results=[];trace=[];error=None
    def resolve(value,depth=0):
        if depth>40:raise ValueError('Result references are nested too deeply')
        if isinstance(value,dict):
            if '$result' in value:
                if set(value)-{'$result','path'}:raise ValueError('Result reference contains unknown keys')
                index=value['$result']
                if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(full_results):raise ValueError('Result references must point to an earlier successful call')
                return _pointer(full_results[index],value.get('path',''))
            return {k:resolve(v,depth+1) for k,v in value.items()}
        if isinstance(value,list):return [resolve(v,depth+1) for v in value]
        return value
    for call in batch:
        if cancel_event is not None and cancel_event.is_set():error='Batch cancelled; earlier operations remain applied';break
        if time.monotonic()>=deadline:error='Batch deadline exceeded; earlier operations remain applied';break
        entry={'tool':call['name'],'status':'started'};trace.append(entry)
        try:
            arguments=resolve(call.get('args',{}))
            if len(json.dumps(arguments,ensure_ascii=True))>131072:raise ValueError('Resolved arguments exceed 128 KiB')
            jsonschema.validate(arguments,by_name[call['name']]['inputSchema'])
            value=invoke(call['name'],arguments)
            entry['status']='complete'
            encoded=json.dumps(value,default=str,allow_nan=False)
            if len(encoded)>1500000:raise ValueError('Native result exceeds 1.5 MB; paginate or filter')
            value=json.loads(encoded);full_results.append(value)
            results.append(_pointer(value,call.get('select','')))
            if time.monotonic()>=deadline:raise ValueError('Batch deadline exceeded; last native call completed; earlier operations remain applied')
        except Exception as exc:
            if entry['status']=='started':entry['status']='failed'
            error=str(exc)[:2000];break
    limit=args.get('max_output_chars',24000);encoded=json.dumps(results,ensure_ascii=True)
    result=results if len(encoded)<=limit else {'truncated':True,'characters':len(encoded),'preview':encoded[:limit]}
    out={'run_id':uuid.uuid4().hex,'ok':error is None,'result':result,'calls':trace,'api_calls':len(trace),'logs':[], 'sandbox':'data-only-batch','note':'Not atomic; earlier operations remain applied. No automatic retry.'}
    if error:out['error']=error
    return out
