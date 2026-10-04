"""Apply or roll back the private Docker snapshot, reverting failed readiness."""
import json,pathlib,subprocess,sys,time,urllib.request
root=pathlib.Path(__file__).resolve().parent

def compose(file,*args):
 subprocess.run(['docker','compose','-f',str(root/file),*args],check=True)

def ready(new):
 deadline=time.monotonic()+100
 while time.monotonic()<deadline:
  try:
   with urllib.request.urlopen('http://127.0.0.1:8131/health',timeout=5) as r:assert r.status==200
   with urllib.request.urlopen('http://127.0.0.1:8131/openapi.json',timeout=5) as r:paths=json.load(r)['paths']
   assert ('/ai/v2/translate/files' in paths)==new
   file='release.compose.json' if new else 'rollback.compose.json'
   expected=json.loads((root/file).read_text())['services']['gateway']['image']
   live=current()
   assert live['Image']==expected
   assert live['Config'].get('Labels',{}).get('com.docker.compose.project.config_files')==str(root/file)
   assert '/ai/chat' in paths and '/ai/translate' in paths
   if new:
    worker=json.loads(subprocess.check_output(['docker','inspect','secai-file-ocr-worker']))[0]
    assert worker['Image']==expected
    assert worker['Config']['Labels'].get('com.docker.compose.project.config_files')==str(root/file)
    state=worker['State']
    assert state['Running'] and not state['Restarting']
   return
  except Exception:time.sleep(2)
 raise RuntimeError('Gateway readiness failed')

def current():
 return json.loads(subprocess.check_output(['docker','inspect','secai-docker-gateway']))[0]

def guard_deploy():
 expected=json.loads((root/'container-before.json').read_text())
 live=current()
 if live['Id'] != expected['Id'] or live['Image'] != expected['Image']:
  raise RuntimeError('Live gateway changed since snapshot; deployment refused. Coordinate and prepare a fresh release.')

def rollback():
 live=current()
 owner=live['Config'].get('Labels',{}).get('com.docker.compose.project.config_files','')
 allowed={str(root/'release.compose.json'),str(root/'rollback.compose.json')}
 expected=json.loads((root/'release.compose.json').read_text())['services']['gateway']['image']
 old=json.loads((root/'rollback.compose.json').read_text())['services']['gateway']['image']
 if owner not in allowed or live['Image'] not in {expected,old}:
  raise RuntimeError('Live gateway belongs to another deployment; rollback refused to preserve it.')
 compose('release.compose.json','stop','-t','15','translate-worker')
 compose('release.compose.json','rm','-f','translate-worker')
 compose('rollback.compose.json','up','-d','--no-deps','--no-build','gateway')
 ready(False)
 print('Rollback verified: original image, API contract, environment and command restored.',flush=True)

if sys.argv[1:] == ['rollback']:rollback()
elif sys.argv[1:] == ['deploy']:
 guard_deploy()
 try:
  compose('release.compose.json','up','-d','--no-deps','--no-build','gateway','translate-worker')
  ready(True)
  subprocess.run([sys.executable,str(root/'smoke.py'),'http://127.0.0.1:8131'],check=True,timeout=200)
  ready(True)
  print('Deployment verified.',flush=True)
 except BaseException:
  rollback()
  raise
else:raise SystemExit('Usage: release.py deploy|rollback')
