"""Persistent, user-owned local Python kernels."""
import ast, contextlib, io, multiprocessing, threading, time, uuid, atexit


def kernel(pipe):
 scope={'__name__':'__notebook__'}
 while True:
  code=pipe.recv();out=io.StringIO();err=io.StringIO();result={}
  try:
   with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
    tree=ast.parse(code)
    if tree.body and isinstance(tree.body[-1],ast.Expr):
     expression=tree.body.pop();exec(compile(tree,'cell','exec'),scope)
     value=eval(compile(ast.Expression(expression.value),'cell','eval'),scope)
     if value is not None:result['value']=repr(value)[:16000]
    else:exec(compile(tree,'cell','exec'),scope)
  except BaseException:
   import traceback
   result['error']=traceback.format_exc()[-16000:]
  result.update(stdout=out.getvalue()[-16000:],stderr=err.getvalue()[-16000:])
  pipe.send(result)

sessions={}
lock=threading.Lock()

def close(key):
 item=sessions.pop(key,None)
 if item:
  item['process'].terminate();item['process'].join(timeout=2);item['pipe'].close()

def execute(owner,session,code,reset=False):
 with lock:
  for key,item in list(sessions.items()):
   if time.monotonic()-item['time']>1800:close(key)
  key=(owner,session)
  if reset:close(key);return {'session':session}
  if key not in sessions:
   if len(sessions)>=16:raise ValueError('Слишком много активных сеансов')
   parent,child=multiprocessing.Pipe();process=multiprocessing.Process(target=kernel,args=(child,),daemon=True);process.start();child.close()
   sessions[key]={'pipe':parent,'process':process,'time':time.monotonic(),'lock':threading.Lock()}
  item=sessions[key];item['time']=time.monotonic()
 with item['lock']:
  item['pipe'].send(code)
  if not item['pipe'].poll(25):
   with lock:close(key)
   return {'error':'Лимит 25 секунд. Сеанс сброшен.'}
  return item['pipe'].recv()

@atexit.register
def cleanup():
 for key in list(sessions):close(key)
