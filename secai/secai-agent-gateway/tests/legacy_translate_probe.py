import asyncio
import json
import httpx
from linch.events import PartialAssistantEvent
from secai_gateway.app import create_app

class Runner:
    async def complete(self, profile_name, prompt, session_id=None):
        return 'Translated output.', {'run_id':'run-test','profile':profile_name}
    async def stream(self, profile_name, prompt, session_id=None):
        yield PartialAssistantEvent(delta={'kind':'text','text':'Translated output.'})

async def main():
    app=create_app(Runner())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
        cases=[('json_ok',{'text':'Xin chào CVE-2024-12345','source_lang':'vi','target_lang':'en'}),
               ('json_bad_target',{'text':'Xin chào','target_lang':'zz'}),
               ('json_empty',{'text':'','target_lang':'en'})]
        result={}
        for name,payload in cases:
            response=await client.post('/ai/translate',json=payload)
            result[name]={'status':response.status_code,'body':response.json(),'content_type':response.headers.get('content-type')}
        response=await client.post('/ai/translate/stream',json=cases[0][1])
        result['sse']={'status':response.status_code,'text':response.text,'content_type':response.headers.get('content-type')}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
asyncio.run(main())
