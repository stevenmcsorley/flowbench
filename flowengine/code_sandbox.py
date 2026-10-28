"""Unprivileged JS process. No host callbacks, modules, file/network API or ambient env.
Linux seccomp restricts the *process* in addition to QuickJS memory/CPU limits.
The trusted parent validates every JSON-RPC bridge message independently.
"""
import ctypes
import errno
import json
import os
import resource
import sys
import time
import quickjs

MAX_MESSAGE=2*1024*1024


def restrict_process():
    if sys.platform!='linux':raise RuntimeError('Local Code Mode requires Linux with libseccomp')
    libc=ctypes.CDLL(None,use_errno=True);sec=ctypes.CDLL('libseccomp.so.2',use_errno=True)
    sec.seccomp_init.argtypes=[ctypes.c_uint32];sec.seccomp_init.restype=ctypes.c_void_p
    sec.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
    sec.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p];sec.seccomp_syscall_resolve_name.restype=ctypes.c_int
    sec.seccomp_load.argtypes=[ctypes.c_void_p];sec.seccomp_release.argtypes=[ctypes.c_void_p]
    if libc.prctl(38,1,0,0,0)!=0:raise RuntimeError('Cannot set no_new_privs')
    policy=sec.seccomp_init(0x00050000|errno.EPERM) # default deny
    if not policy:raise RuntimeError('Cannot allocate seccomp policy')
    allowed=('read','write','close','fstat','fstat64','lseek','mmap','mmap2','mprotect','munmap','mremap','brk','madvise','rt_sigaction','rt_sigprocmask','rt_sigreturn','sigaltstack','clock_gettime','clock_gettime64','gettimeofday','getrusage','getrandom','futex','futex_time64','sched_yield','getpid','gettid','exit','exit_group','restart_syscall')
    try:
        for name in allowed:
            number=sec.seccomp_syscall_resolve_name(name.encode())
            if number>=0 and sec.seccomp_rule_add(policy,0x7fff0000,number,0)!=0:raise RuntimeError('Cannot configure seccomp rule')
        if sec.seccomp_load(policy)!=0:raise RuntimeError('Cannot install seccomp sandbox')
    finally:sec.seccomp_release(policy)
    return libc


def emit(body):
    text=json.dumps(body,separators=(',',':'),ensure_ascii=True)
    if len(text)>MAX_MESSAGE:raise RuntimeError('Sandbox message exceeds 2 MB')
    sys.stdout.write(text+'\n');sys.stdout.flush()


def read():
    line=sys.stdin.buffer.readline(MAX_MESSAGE+1)
    if not line or len(line)>MAX_MESSAGE:raise RuntimeError('Invalid sandbox input')
    return json.loads(line)


BOOTSTRAP=r'''
(() => {
  const queue=[], pending=new Map(), logs=[];
  let sequence=0, completed=false, result=null, failure=null, logChars=0;
  const serialize=v=>JSON.stringify(v,(_,value)=>typeof value==='bigint'?String(value):value);
  const report=e=>({message:String(e?.message||e).slice(0,2000),stack:String(e?.stack||'').slice(0,3000)});
  const call=(name,args={})=>new Promise((resolve,reject)=>{
    const encoded=serialize(args);
    if(typeof name!=='string'||encoded===undefined||encoded.length>131072){reject(new Error('Invalid or oversized API arguments'));return;}
    const id=++sequence;pending.set(id,{resolve,reject});queue.push({id,name,args:JSON.parse(encoded)});
  });
  const api=Object.create(null);
  Object.defineProperty(api,'call',{value:call});
  for(const name of __config.operations)Object.defineProperty(api,name,{value:args=>call(name,args)});
  Object.defineProperty(globalThis,'tools',{value:Object.freeze(api),writable:false});
  Object.defineProperty(globalThis,'input',{value:__config.input,writable:false});
  globalThis.console=Object.freeze(Object.fromEntries(['log','info','warn','error'].map(level=>[level,(...args)=>{
    if(logs.length>=40||logChars>=4096)return;
    let text;try{text=args.map(v=>typeof v==='string'?v:serialize(v)).join(' ')}catch{text='[unserializable log]'}
    text=text.slice(0,Math.max(0,4096-logChars));logChars+=text.length;logs.push({level,text});
  }])));
  globalThis.__bridge=Object.freeze({
    drain(){return serialize(queue.splice(0));},
    settle(message){const p=pending.get(message.id);if(!p)return;pending.delete(message.id);message.ok?p.resolve(message.value):p.reject(new Error(message.error));},
    finish(value){try{result=JSON.parse(serialize(value===undefined?null:value));completed=true;}catch(e){failure=report(e);completed=true;}},
    fail(e){failure=report(e);completed=true;},
    state(){return serialize({completed,result,failure,logs,pending:pending.size});}
  });
})();
'''


def main():
    config=read()
    resource.setrlimit(resource.RLIMIT_CORE,(0,0));resource.setrlimit(resource.RLIMIT_AS,(256*1024**2,256*1024**2));resource.setrlimit(resource.RLIMIT_CPU,(3,4))
    ctx=quickjs.Context();ctx.set_memory_limit(32*1024**2);ctx.set_max_stack_size(512*1024);ctx.set_time_limit(.25)
    # All imports and runtime loading finish before the deny-by-default syscall filter.
    libc=restrict_process()
    if config.get('probe'):
        ctypes.set_errno(0);fd=libc.open(b'/etc/passwd',0);file_errno=ctypes.get_errno()
        ctypes.set_errno(0);sock=libc.socket(2,1,0);socket_errno=ctypes.get_errno()
        emit({'file_open':fd,'file_errno':file_errno,'socket':sock,'socket_errno':socket_errno,'sandbox':'seccomp'});return
    emit({'type':'ready','sandbox':'quickjs-ng+seccomp'})
    ctx.eval('globalThis.__config='+json.dumps({'operations':config['operations'],'input':config.get('input',{})})+';')
    ctx.eval(BOOTSTRAP)
    code=config['code'];expression=('('+code+'\n)(input)' if config.get('function') else '(async()=>{\n'+code+'\n})()')
    try:
        ctx.eval('Promise.resolve('+expression+').then(__bridge.finish,__bridge.fail);')
        for _ in range(10000):
            steps=0
            while ctx.execute_pending_job():
                steps+=1
                if steps>1000:raise RuntimeError('Too many promise steps; return background job IDs instead of polling')
            requests=json.loads(ctx.eval('__bridge.drain()'))
            if requests:
                emit({'type':'calls','calls':requests})
                reply=read()
                if reply.get('type')!='results':raise RuntimeError('Invalid bridge response')
                for response in reply['results']:ctx.eval('__bridge.settle('+json.dumps(response)+');')
                continue
            state=json.loads(ctx.eval('__bridge.state()'))
            if state['completed']:emit({'type':'done',**state});return
            raise RuntimeError('Unresolved promise: await tool calls; timers and external I/O are unavailable')
        raise RuntimeError('Too many execution steps')
    except Exception as e:
        emit({'type':'error','error':str(e)[:3000]})


if __name__=='__main__':
    try:main()
    except Exception as e:
        try:emit({'type':'error','error':str(e)[:3000]})
        except Exception:pass
        sys.exit(1)
