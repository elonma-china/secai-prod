"""Dịch vụ chuyển .doc -> .docx (LibreOffice headless) chạy RIÊNG: worker/gateway gọi HTTP, không cần docker.sock.
POST /convert  (body = byte .doc, header Authorization: Bearer <token>) -> 200 .docx | 4xx/5xx JSON lỗi.
Mỗi yêu cầu: thư mục + profile LibreOffice riêng (chạy song song được), timeout, macro tắt, không mạng (đặt ở tầng container)."""
import asyncio, hmac, os, shutil, tempfile
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request, Response
import uvicorn

TOKEN = os.environ.get("CONVERTER_TOKEN", "")
MAX_BYTES = int(os.environ.get("CONVERTER_MAX_BYTES", str(25 * 1024 * 1024)))
TIMEOUT = float(os.environ.get("CONVERTER_TIMEOUT", "60"))
sem = asyncio.Semaphore(int(os.environ.get("CONVERTER_CONCURRENCY", "4")))
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
MACRO = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
    <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
  </item>
</oor:items>"""


@app.get("/health")
async def health():
    return {"ready": shutil.which("soffice") is not None}


@app.post("/convert")
async def convert(request: Request, authorization: str = Header(default="")):
    if not TOKEN or not hmac.compare_digest(authorization.encode(), f"Bearer {TOKEN}".encode()):
        raise HTTPException(401, "Unauthorized")
    data = await request.body()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413 if data else 422, "Invalid size")
    if not data.startswith(b"\xd0\xcf\x11\xe0"):
        raise HTTPException(422, "Not an OLE .doc")
    async with sem:
        work = Path(tempfile.mkdtemp(prefix="conv-"))
        try:
            (work / "profile/user").mkdir(parents=True)
            (work / "profile/user/registrymodifications.xcu").write_text(MACRO)
            (work / "in").mkdir(); (work / "out").mkdir()
            (work / "in/document.doc").write_bytes(data)
            proc = await asyncio.create_subprocess_exec(
                "soffice", f"-env:UserInstallation=file://{work}/profile", "--headless", "--nologo", "--nodefault",
                "--nofirststartwizard", "--convert-to", "docx", "--outdir", str(work / "out"), str(work / "in/document.doc"),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(proc.wait(), TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill(); await proc.wait()
                raise HTTPException(504, "Conversion timed out")
            out = work / "out/document.docx"
            if proc.returncode != 0 or not out.is_file():
                raise HTTPException(422, "Conversion failed")
            return Response(out.read_bytes(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("CONVERTER_HOST", "0.0.0.0"), port=int(os.environ.get("CONVERTER_PORT", "18090")), log_level="warning")
