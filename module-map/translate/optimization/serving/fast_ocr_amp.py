"""OCR service dùng vLLM (Qwen3-VL-2B) sau cùng hợp đồng POST /v1/ocr của dịch vụ OCR hiện tại:
  body {model, image_base64, calibration_prompt}, Bearer token -> {"model", "blocks":[{"text","bbox":null,"confidence":null}]}.
Khác bản cũ: nhận nhiều request đồng thời (vLLM batching), ngân sách pixel lớn hơn (mặc định 3 Mpx) để
đọc đúng chuỗi ký tự ngẫu nhiên (mật khẩu/khóa); prompt lấy NGUYÊN VĂN từ module OCR đã duyệt."""
import ast, asyncio, base64, difflib, hmac, io, math, os, re, time
from pathlib import Path
import httpx, uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from PIL import Image

SRC = Path("/workspace/secai-specialist-services/source/evals/translation_ocr/real_quality/qwen_server_real_pages_bounded.py")
tree = ast.parse(SRC.read_text(encoding="utf-8"))
PROMPTS = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "PROMPTS" for t in n.targets))
MODEL_ID = "Qwen/Qwen3-VL-2B-Instruct"
VLLM = os.environ.get("OCR_VLLM_URL", "http://127.0.0.1:18771")
MAX_PIXELS = int(float(os.environ.get("OCR_MAX_PIXELS", "3000000")))
MAX_EDGE = int(os.environ.get("OCR_MAX_EDGE", "3072"))
MAX_TOKENS = 4096
STRIP_PX = float(os.environ.get("OCR_STRIP_PIXELS", "300000"))     # trang lớn hơn mức này -> cắt dải
WIDE_STRIPS = os.environ.get("OCR_WIDE_STRIPS", "1") == "1"  # ảnh ngang một cột cũng cắt dải để OCR song song
STRIP_MAX = int(os.environ.get("OCR_STRIP_MAX", "12"))
UPSCALE_TARGET = float(os.environ.get("OCR_UPSCALE_TARGET_PIXELS", "4500000"))  # phóng dải nhỏ lên ~mức này (chữ nhỏ đọc sai ký tự)
UPSCALE_MAX = float(os.environ.get("OCR_UPSCALE_MAX", "2.5"))
TOKEN = Path("/workspace/secai-specialist-services/ocr.token").read_text().strip()
sem = asyncio.Semaphore(int(os.environ.get("OCR_CONCURRENCY", "16")))
client = httpx.AsyncClient(base_url=VLLM, trust_env=False, timeout=150,
                           limits=httpx.Limits(max_connections=128))
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class Req(BaseModel):
    model: str
    image_base64: str = Field(max_length=15_000_000)
    calibration_prompt: Literal["natural", "arabic", "verbatim", "universal", "conditional"] = "conditional"


def is_blank(raw: bytes) -> bool:
    """Ảnh gần như đồng nhất (trang trắng): không gọi model — model dễ BỊA nội dung (vd. một mã CVE) khi không có chữ."""
    from PIL import ImageStat
    im = Image.open(io.BytesIO(raw)).convert("L")
    im.thumbnail((256, 256))
    return ImageStat.Stat(im).stddev[0] < 4.0


async def has_text(data_url: str) -> bool:
    """Cổng kiểm 'ảnh có chữ đọc được không' (1 token). Dùng khi kết quả OCR ngắn/lặp: trang nhiễu, trang trắng có vệt."""
    body = {"model": "ocr", "temperature": 0, "max_tokens": 3, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": "Does this image contain any readable printed or handwritten text? Answer with only Yes or No."}]}]}
    try:
        r = await client.post("/v1/chat/completions", json=body)
        return r.status_code == 200 and r.json()["choices"][0]["message"]["content"].strip().lower().startswith("y")
    except (httpx.HTTPError, KeyError, ValueError):
        return True  # không chắc -> không tự bỏ nội dung


def _fit(im: Image.Image) -> Image.Image:
    """Phóng dải nhỏ (tối đa UPSCALE_MAX, tới ~UPSCALE_TARGET pixel) hoặc thu nhỏ nếu vượt ngân sách."""
    w, h = im.size
    px = w * h
    if px < UPSCALE_TARGET:
        r = min(UPSCALE_MAX, math.sqrt(UPSCALE_TARGET / px), MAX_EDGE / max(w, h))
    else:
        r = min(1.0, MAX_EDGE / max(w, h), math.sqrt(MAX_PIXELS / px))
    if abs(r - 1) > 0.05:
        im = im.resize((max(1, int(w * r)), max(1, int(h * r))), Image.Resampling.LANCZOS)
    return im


def split_strips(im: Image.Image) -> list[Image.Image]:
    """Trang cao, một cột -> cắt ở KHOẢNG TRẮNG giữa các dòng thành k dải (OCR song song, mỗi dải phóng riêng).
    Trang 2 cột (có rãnh dọc trống) hoặc nhỏ -> giữ nguyên 1 ảnh để không đảo thứ tự đọc."""
    import numpy as np
    w, h = im.size
    if w * h < STRIP_PX or (h < 1.1 * w and not WIDE_STRIPS):
        return [im]
    g = np.asarray(im.convert("L"), dtype=np.float32)
    rowstd = g.std(axis=1)
    band = g[:, int(w * 0.35):int(w * 0.65)]
    colstd = band.std(axis=0)
    quiet = colstd < 6
    run = best = 0
    for q in quiet:
        run = run + 1 if q else 0
        best = max(best, run)
    if best >= max(6, int(w * 0.012)):
        return [im]  # có rãnh giữa -> nhiều cột
    k = min(STRIP_MAX, max(2, math.ceil(w * h / STRIP_PX)))
    cuts = [0]
    for i in range(1, k):
        target = int(h * i / k); lo, hi = max(cuts[-1] + 40, target - int(h * 0.08)), min(h - 40, target + int(h * 0.08))
        if hi <= lo:
            continue
        seg = rowstd[lo:hi]
        cuts.append(lo + int(np.argmin(seg)))
    cuts.append(h)
    return [im.crop((0, a, w, b)) for a, b in zip(cuts, cuts[1:]) if b - a > 20]


def prep(raw: bytes) -> bytes:
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("image limit")
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    if im.width * im.height > 20_000_000 or max(im.size) > 10_000:
        raise ValueError("pixel limit")
    r = min(1.0, MAX_EDGE / max(im.size), math.sqrt(MAX_PIXELS / (im.width * im.height)))
    if r < 1:
        im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.Resampling.LANCZOS)
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return buf.getvalue()


@app.get("/health")
async def health():
    try:
        ok = (await client.get("/health", timeout=3)).status_code == 200
    except httpx.HTTPError:
        ok = False
    return {"ready": ok, "busy": False, "state": "ready" if ok else "failed", "model": MODEL_ID, "engine": "vllm"}


import re
_REPEAT = re.compile(r"(.{20,200}?)\1{3,}", re.S)


def trim_repeat(text: str) -> str:
    """Model quay vòng: giữ 1 lần đoạn lặp, bỏ phần lặp thừa."""
    return _REPEAT.sub(r"\1", text)


async def ocr_one(png: bytes, prompt: str, max_tokens: int = MAX_TOKENS) -> str:
    """OCR một ảnh/dải. Trả "" nếu không có chữ (cổng kiểm chữ); 503 nếu có chữ nhưng model lặp/tràn."""
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    body = {"model": "ocr", "temperature": 0, "max_tokens": max_tokens, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": prompt}]}]}
    async def call(extra: dict) -> tuple[str, bool]:
        r = None
        for attempt in range(2):  # vLLM có thể từ chối thoáng qua khi nhiều dải đến cùng lúc
            async with sem:
                try:
                    r = await client.post("/v1/chat/completions", json={**body, **extra})
                except httpx.HTTPError:
                    r = None
            if r is not None and r.status_code == 200:
                break
            await asyncio.sleep(0.5)
        if r is None or r.status_code != 200:
            raise HTTPException(503, "OCR worker unavailable")
        ch = r.json()["choices"][0]
        return (ch["message"].get("content") or "").strip(), ch.get("finish_reason") == "length"

    text, capped = await call({})
    if capped:
        # model lặp vô hạn trên dải này: thử lại 1 lần với phạt lặp và trần token thấp hơn
        text2, capped2 = await call({"repetition_penalty": 1.12, "max_tokens": min(1800, max_tokens)})
        text, capped = (text2 or text), capped2
        text = trim_repeat(text)
    if capped or len(text) < 60:
        async with sem:
            readable = await has_text(url)
        if not readable:
            return ""  # không có chữ: tránh bịa (CVE/IP) và lặp vô hạn trên ảnh nhiễu
        # có chữ nhưng vẫn lặp: trả phần đã đọc (không làm hỏng cả job); đuôi lặp đã bị trần token chặn
    return text


# ---------------------------------------------------------------------------------------------
# Đọc lại có chọn lọc chuỗi đặc biệt (mật khẩu/khóa). Model 2B hay làm rơi/đổi ký tự trong chuỗi
# nhiều ký tự `!@#$%^&*()_+` khi dải chứa cả đoạn văn dài. Sau lượt đọc dải, token nghi ngờ (nhiều ký
# tự đặc biệt) được định vị trên ảnh, cắt riêng từng dòng, phóng to, đọc lại từng ký tự và bỏ phiếu.
# Không có chuỗi đặc biệt -> không tốn thêm lệnh gọi nào.
SPECIALS = set("!@#$%^&*()_+~|\\<>{}[]")
REREAD = os.environ.get("OCR_REREAD", "1") == "1"
REREAD_MAX_TOKENS = int(os.environ.get("OCR_REREAD_MAX_TOKENS", "4"))
LINE_PROMPT = ("Copy the text on this single line exactly, character by character, left to right. "
               "Keep every symbol such as ! @ # $ % ^ & * ( ) _ + and do not skip or merge any. Output only the text.")
LINE_PROMPT2 = ("Transcribe this one line of text verbatim, one character at a time. Do not correct, translate, "
                "normalize or drop any character, including punctuation and symbols. Output only the line.")
_RTL = re.compile(r"[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]")


def _nspec(t: str) -> int:
    return sum(c in SPECIALS for c in t)


_MIRROR = str.maketrans("()[]{}<>", ")(][}{><")


def _join_special(text: str) -> str:
    """Dòng RTL: model tách phần đuôi trôi lên đầu ('+_( )*&^P@..'); nối các mảnh toàn ký tự đặc biệt với từ liền sau."""
    return re.sub(r"(?<!\S)([^\w\s]|_){1,4}\s+(?=[^\s\u0590-\u08FF])",
                  lambda m: m.group(0).replace(" ", ""), text) if _RTL.search(text) else text


def _bidi_logical(tok: str) -> str:
    """Thứ tự hiển thị -> thứ tự logic: ký tự trung tính ở ĐẦU token (trôi sang trái trong đoạn RTL) trả về cuối, đảo thứ tự và lật ngoặc."""
    m = re.match(r"^([^\w$%@#]|_)+", tok)
    lead = m.group(0) if m else ""
    if len(lead) < 2 or len(lead) == len(tok):
        return tok
    return tok[len(lead):] + lead[::-1].translate(_MIRROR)


def _clean(c: str, tok: str) -> str:
    """Bỏ ký tự CHÈN THÊM (so với token gốc) không phải ký tự đặc biệt/chữ-số — vd gạch '-' do model bịa khi đọc lại."""
    if c == tok:
        return c
    out = []
    for op, a0, a1, b0, b1 in difflib.SequenceMatcher(None, tok, c, autojunk=False).get_opcodes():
        seg = c[b0:b1]
        if op == "insert":
            seg = "".join(ch for ch in seg if ch.isalnum() or ch in SPECIALS)
        out.append(seg)
    return "".join(out)


def _is_suspect(tok: str) -> bool:
    return len(tok) >= 8 and _nspec(tok) >= 4 and any(c.isalnum() for c in tok)


def _core(tok: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)([.,;:]*)$", tok, re.S)
    return m.group(1), m.group(2)


def _line_boxes(im: Image.Image) -> list[tuple[int, int, int, int]]:
    """(y0, y1, x0, x1) của từng dòng chữ theo chiếu ngang (nền = trung vị)."""
    import numpy as np
    g = np.asarray(im.convert("L"), dtype=np.float32)
    ink = np.abs(g - np.median(g)) > 40
    rows = ink.sum(axis=1) > max(2, 0.002 * g.shape[1])
    out, s, gap = [], None, 0
    for i, r in enumerate(rows):
        if r:
            s = i if s is None else s
            gap = 0
        elif s is not None:
            gap += 1
            if gap >= 4:
                out.append((s, i - gap + 1)); s = None
    if s is not None:
        out.append((s, len(rows)))
    boxes = []
    for a, b in out:
        if b - a < 10:
            continue
        cols = np.where(ink[a:b].any(axis=0))[0]
        if len(cols) < 8:
            continue
        boxes.append((a, b, int(cols[0]), int(cols[-1]) + 1))
    return boxes


def _tw(t: str) -> int:
    return sum(2 if ord(c) > 0x2E80 else 1 for c in t if not c.isspace())


async def _read_line(crop: Image.Image, scale: float, prompt: str) -> str:
    w, h = crop.size
    r = min(scale, 3000 / w)
    if r < 1.2:
        r = 1.0
    c = crop.resize((int(w * r), int(h * r)), Image.Resampling.LANCZOS) if r != 1.0 else crop
    buf = io.BytesIO(); c.save(buf, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    body = {"model": "ocr", "temperature": 0, "max_tokens": 160, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": prompt}]}]}
    try:
        async with sem:
            r_ = await client.post("/v1/chat/completions", json=body)
        if r_.status_code != 200:
            return ""
        return (r_.json()["choices"][0]["message"].get("content") or "").strip()
    except (httpx.HTTPError, KeyError, ValueError):
        return ""


def _best_tok(text: str, tok: str) -> tuple[str, float]:
    best, bs = "", 0.0
    for c in text.split():
        cc = _core(c)[0]
        sc = difflib.SequenceMatcher(None, tok, cc, autojunk=False).ratio()
        if sc > bs:
            best, bs = cc, sc
    return best, bs


async def refine_special(part: Image.Image, text: str) -> str:
    """Đọc lại các token nghi ngờ trong `text` (kết quả OCR của dải `part`)."""
    if not REREAD or not text:
        return text
    text = _join_special(text)
    sus = []
    for m in re.finditer(r"\S+", text):
        full, _ = _core(m.group(0))
        # Nhãn CJK/RTL dính liền giá trị ("密码:P@ss!...") là một "từ" theo khoảng trắng: chỉ xử lý phần lõi ASCII, GIỮ nguyên
        # nhãn đứng trước (và chữ không-ASCII đứng sau) — nếu thay cả cụm sẽ làm mất nhãn.
        pm = re.match(r"^(?:[^\x00-\x7f]|[:：])+(?=\S)", full)
        off = pm.end() if pm else 0
        core = full[off:]
        sm = re.search(r"[^\x00-\x7f]+$", core)
        if sm:
            core = core[:sm.start()]
        if _is_suspect(core):
            sus.append((m.start() + off, core, bool(_RTL.search(text[max(0, m.start() - 60):m.end() + 60]))))
    if not sus:
        return text
    sus = sus[:REREAD_MAX_TOKENS]
    boxes = await asyncio.to_thread(_line_boxes, part)
    if not boxes:
        return text
    wid = [b[3] - b[2] for b in boxes]
    tot_w, tot_t = sum(wid) or 1, _tw(text) or 1
    cache: dict[int, str] = {}

    async def rd(i: int) -> str:
        if i not in cache:
            y0, y1, x0, x1 = boxes[i]
            crop = part.crop((max(0, x0 - 12), max(0, y0 - 8), min(part.width, x1 + 12), min(part.height, y1 + 8)))
            cache[i] = _join_special(await _read_line(crop, 2.0, LINE_PROMPT))
        return cache[i]

    repl = []
    for pos, tok, rtl in sus:
        f = _tw(text[:pos]) / tot_t
        acc, ci = 0.0, len(boxes) - 1
        for i, w in enumerate(wid):
            acc += w / tot_w
            if acc >= f:
                ci = i; break
        best_i, best_t, best_s = -1, "", 0.0
        for rad in (2, 6, 14):
            idx = [i for i in range(max(0, ci - rad), min(len(boxes), ci + rad + 1))]
            reads = await asyncio.gather(*(rd(i) for i in idx))
            for i, rt in zip(idx, reads):
                t2, sc = _best_tok(rt, tok)
                if sc > best_s:
                    best_i, best_t, best_s = i, t2, sc
            if best_s >= 0.75:
                break
        if best_i < 0 or best_s < 0.6:
            continue
        y0, y1, x0, x1 = boxes[best_i]
        crop = part.crop((max(0, x0 - 12), max(0, y0 - 8), min(part.width, x1 + 12), min(part.height, y1 + 8)))
        # Luôn bỏ phiếu giữa nhiều lượt đọc ĐA DẠNG (mức phóng/prompt khác nhau, song song): hai lượt đọc "khớp nhau" vẫn có thể
        # cùng sai theo một thiên lệch của model (đã gặp 0->O, &* -> *&). Chỉ chạy cho token nghi ngờ nên chi phí nhỏ.
        extra = [(3.0, LINE_PROMPT2), (1.5, LINE_PROMPT), (3.5, LINE_PROMPT2), (2.5, LINE_PROMPT)] + ([(2.5, LINE_PROMPT2), (3.0, LINE_PROMPT)] if rtl else [])
        reads3 = await asyncio.gather(*(_read_line(crop, sc_, pr_) for sc_, pr_ in extra))
        cands = [tok, _clean(best_t, tok)]
        for r3 in reads3:
            t3, s3 = _best_tok(_join_special(r3), tok)
            if s3 >= 0.6:
                cands.append(_clean(t3, tok))
        if not rtl:
            # LTR: một lượt đọc lại KHÔNG được làm mất ký tự đặc biệt so với lượt đọc đầu (đã gặp: dòng terminal tối đọc
            # đầu đủ "...&*()_+", hai lượt đọc dòng cắt cùng bỏ "()_+" và thắng phiếu). Chỉ chấp nhận ứng viên không ít hơn.
            cands = [c for c in cands if c == tok or _nspec(c) >= _nspec(tok)]
        votes: dict[str, int] = {}
        for c in cands:
            votes[c] = votes.get(c, 0) + 1
        # nhiều phiếu nhất; hòa -> nhiều ký tự đặc biệt hơn; hòa nữa -> giữ token gốc
        win = max(cands, key=lambda c: (votes[c], _nspec(c), c == tok))
        if rtl:
            win = _bidi_logical(win)
            tok_l = _bidi_logical(tok)
        else:
            tok_l = tok
        if win != tok_l or win != tok:
            repl.append((pos, tok, win))
    for pos, tok, win in sorted(repl, reverse=True):
        if text[pos:pos + len(tok)] == tok:
            text = text[:pos] + win + text[pos + len(tok):]
    return text


def _split(raw: bytes) -> list[Image.Image]:
    """Cắt dải + bỏ dải trắng; CHƯA mã hóa (phóng + PNG là phần tốn CPU, làm song song từng dải trong _read_strip)."""
    from PIL import ImageStat
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("image limit")
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    if im.width * im.height > 20_000_000 or max(im.size) > 10_000:
        raise ValueError("pixel limit")
    out = []
    for part in split_strips(im):
        t = part.convert("L"); t.thumbnail((256, 256))
        if ImageStat.Stat(t).stddev[0] < 4.0:
            continue  # dải trắng hoàn toàn: bỏ qua, không gọi model
        out.append(part)
    return out


def _encode(part: Image.Image) -> bytes:
    buf = io.BytesIO(); part.thumbnail((10_000, 10_000))
    _fit(part).save(buf, format="PNG")
    return buf.getvalue()


@app.post("/v1/ocr")
async def ocr(req: Req, authorization: str = Header(default="")):
    if not hmac.compare_digest(authorization.encode(), f"Bearer {TOKEN}".encode()):
        raise HTTPException(401, "Unauthorized")
    if req.model != MODEL_ID:
        raise HTTPException(422, "Unsupported model")
    try:
        raw = base64.b64decode(req.image_base64, validate=True)
        parts = await asyncio.to_thread(_split, raw)
    except Exception as exc:
        raise HTTPException(422, "Invalid image") from exc
    if not parts:
        return {"model": MODEL_ID, "blocks": []}  # trang trắng
    cap = 1800 if len(parts) > 1 else MAX_TOKENS  # mỗi dải chỉ chứa ~1/k trang
    async def read(part: Image.Image) -> str:
        p = await asyncio.to_thread(_encode, part)  # dải đầu không phải đợi các dải sau được mã hóa
        t = await ocr_one(p, PROMPTS[req.calibration_prompt], cap)
        try:
            return await refine_special(part, t)
        except Exception:  # lượt đọc lại chỉ là cải thiện; lỗi -> giữ kết quả gốc
            return t
    texts = await asyncio.gather(*(read(part) for part in parts))
    text = "\n".join(t for t in texts if t).strip()
    return {"model": MODEL_ID, "blocks": [] if not text else [{"text": text, "bbox": None, "confidence": None}]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("OCR_PORT", "18083")), log_level="warning", backlog=1024)
