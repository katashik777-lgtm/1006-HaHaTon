from spr.notebook import execute, cleanup

def test_state_output_and_reset():
 try:
  assert execute('a','session','x = 40')['stdout']==''
  assert execute('a','session','x + 2')['value']=='42'
  assert 'error' in execute('b','session','x')
  assert 'error' in execute('a','session','1 / 0')
  assert execute('a','session','print(x)')['stdout']=='40\n'
  execute('a','session','',True)
  assert 'error' in execute('a','session','x')
 finally:cleanup()
