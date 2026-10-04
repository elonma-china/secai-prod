"""Bounded file API smoke. Uses only generated, non-sensitive fixture data."""
import io,json,sys,time,urllib.request,urllib.error,uuid,zipfile
base=sys.argv[1]
def request(path,data=None,headers=None):
 try:r=urllib.request.urlopen(urllib.request.Request(base+path,data=data,headers=headers or {}),timeout=10)
 except urllib.error.HTTPError as e:r=e
 return r.status,json.load(r)
def upload(name,content,source='en',target='en',kind='files'):
 boundary=uuid.uuid4().hex
 fields=[('source_lang',source),('target_lang',target)]
 body=b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode() for k,v in fields)
 body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+content+f'\r\n--{boundary}--\r\n'.encode()
 return request('/ai/v2/translate/'+kind,body,{'Content-Type':'multipart/form-data; boundary='+boundary})
def completed(name,content,target='en'):
 status,row=upload(name,content,target=target);assert status==202,(status,row)
 deadline=time.monotonic()+150
 while time.monotonic()<deadline:
  status,row=request(row['status_url'])
  if row['status'] in ('succeeded','failed','cancelled'):break
  time.sleep(3)
 assert row['status']=='succeeded',row
 status,result=request(row['result_url']);assert status==200 and result['translated_text'],result
 print(name,target,'succeeded',repr(result['translated_text']),flush=True)
assert request('/health')[0]==200
status,schema=request('/openapi.json');assert status==200
for path in ['/ai/chat','/ai/translate','/sessions/{sid}/chat','/ai/v2/translate/files']:
 assert path in schema['paths'],path
completed('smoke.txt',b'Hello world. This is a translation test.')
buf=io.BytesIO()
with zipfile.ZipFile(buf,'w') as z:
 z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
 z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
 z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello from DOCX.</w:t></w:r></w:p></w:body></w:document>')
completed('smoke.docx',buf.getvalue())
status,result=upload('invalid.pdf',b'%PDF-1.4');assert status==415,(status,result);print('PDF rejected415',flush=True)
status,result=upload('image.png',b'\x89PNG\r\n\x1a\n',kind='images');assert status==503 and result['error']['code']=='DEPENDENCY_UNAVAILABLE',(status,result);print('OCR unconfigured503',flush=True)
completed('translate.txt',b'Hello world. This is a translation test.',target='vi')
