# OCR supervisor evidence, 2026-10-04

Candidate branch `test/translate-real-quality`, runtime commits
`5de0e75ec407651d809d5c70e86f7d83a10f96e9`,
`6763d8c6de9a1144887e6d914e21856535a62d4a`, and
`2ffc930681e5c0ec223c0d6203165897a70b3870`. The active Vast private
service at `127.0.0.1:18083` was launched from copied, hash-matched files:

| File | SHA-256 |
| --- | --- |
| `qwen_ocr_supervisor.py` | `c262557061740c02806bf83a002b43be0e9917197f38b71ffa4105def05d19a5` |
| `qwen_ocr_child.py` | `a5f60b38383de43c7111c67778042269213b4f1804a52f1f9ff11dded1a344b5` |
| `qwen_server_real_pages_bounded.py` | `9308887a3939f08d000b833fa92190b1f826e0b273a86b7a89271ed88098d1aa` |
| `start_real_pages_bounded.sh` | `17d7bc627f38f50310a340a788b96b4f8bd87be98a878eff5caf1d1d82c9329e` |
| `probe_disconnect.py` | `1772927a47bf0dbb4a99daa5b4ef26b0a7445359b7eaee7cf4354f7a3f01c4b8` |
| `test_ocr_supervisor.py` | `fb2990caba7455125530a451f8805cb8ba0cc9c0984ca1b65656cc21c7670e80` |
| `test_bounded_runtime.py` | `b7717d62a04faa24e3c6680ba104511ac4130d36b1c554f48b4647d34ed737cc` |
| required tracked `deploy/ocr/server.py` | `d8edaaf7a9dae4d784b9cef1d361bb855216922dcd0dbb3622f62cdfb8289543` |

The model is `Qwen/Qwen3-VL-2B-Instruct` at revision
`89644892e4d85e24eaac8bacfd4f463576704203`; its
`model.safetensors` SHA-256 is checked by the launcher against
`7de1838c87a5349b016c26a1c3f7d2bc400a3d485f95ef39a7059ffd734977a0`.
Prompt `conditional` and 4096 output-token cap remained fixed. Existing
Vast instance 53846446, SSH `ssh9.vast.ai:10812`; three incumbent GPU host
PIDs were 3203145, 3257875, 3276685 before and after. The active parent
supervisor PID was 48987. It retained no CUDA allocation after each child:
measured free GPU memory returned to 8772 MiB, with only those incumbents.

Runtime dependencies enforced by `start_real_pages_bounded.sh` on the owned
Vast virtual environment: torch `2.11.0+cu130`, Transformers `5.13.1`,
FastAPI `0.136.3`, Pydantic `2.13.4`, Pillow `12.3.0`, Uvicorn `0.51.0`,
huggingface-hub `1.23.0`, safetensors `0.8.0`, accelerate `1.14.0`, plus
`nvidia-smi`. The script refuses mismatches and installs no packages into
incumbent environments. It binds one worker to loopback. The bearer token
is stored separately and is not included in this report.

Focused checks ran in the remote owned virtual environment:

```text
python test_ocr_supervisor.py
  supervisor hang, disconnect, resource, trace and recovery paths passed
python test_bounded_runtime.py
  resource, repetition, exception, disconnect and lock paths passed
```

The supervisor test uses fake subprocesses stalled under load/prefill labels to exercise
disconnection, midflight quota failure, an unwritable trace path, a retained
lock while memory reserve is unavailable, and subsequent admission after
cleanup. `python3 -m compileall` on changed Python files, `sh -n` on the
launcher, and `git diff --check` passed. These CPU tests do not reproduce a
stalled CUDA driver call; the process-boundary kill is the actual hard-stop
mechanism. The HTTP response timeout is 110 seconds, while the child wall
deadline is 100 seconds. If a killed child cannot exit or reserve cannot
return, the parent answers 503 by 110 seconds and holds admission closed.

The exact frozen runtime was then exercised with a unique live request ID
`disconnect-44621bf7b1fafa96`. Its fresh private trace recorded one generated
token before client disconnection; the child ended and the lock released in
11.537 seconds. The parent took 31 GPU samples, verified the child host PID,
measured peak owned 4828 MiB and minimum free 3939 MiB. A subsequent real
`mdp-ja-01` OCR request returned HTTP 200 in 66.736 seconds with 1811 text
characters (SHA-256 `cdae5ac156ad9634ddbb0fb4cc09881550228480724acbbc578dc51beacd1ab8`).
It used 199 GPU samples, verified the child host PID, measured peak owned
4922 MiB and minimum free 3845 MiB. The private trace is
`/workspace/secai-translation-ocr/runtime/ocr-supervisor-frozen-trace.jsonl`;
no token or OCR content is in it. The public-image input was the frozen
`mdp-ja-01.jpg`, not user data. The test was run by
`probe_disconnect.py <image> <token-file> <trace-file>` with all arguments
private to Vast.

Real-document quality is **not accepted**. Frozen public corpus source
`images.json` SHA-256
`451eb2a5e622fbfda623b3477a26bde57d4a1c31dabd9e79c56db666cbc13138`;
prepared manifest SHA-256
`7732f33592282090ad89d991f32a48c6e135034d20f37789cdd55674ed567055`.
Historical checkpoint SHA-256
`3835127cd24d8d971f0325d2972910a32dd654d2e1d57f5417ec623dc2c1f667`
contains 18/120 attempts: 12 HTTP 200, 6 deadlines and 102 not run. The
manual-only final recovery hypothesis produced `mdp-zh-03` CER 43.36% after
rotation/crop, and `mdp-ar-03` still timed out at 100.025 seconds after
perspective rectification. The diagnostic JSON SHA-256 values are
`6b9654fbfe8214e1cf8fa9cb31e7249f029ef38400228c088cf8f387862023b7`
and `a08f5a3873c2a7ac393a92a8b27fe135c70a0f26a41dcbef3d9e7e92b7267ea3`,
respectively. These manual transforms were never adopted in production.
FS-07's required six failed pages plus controls across three real pipeline
rounds remain unexecuted and **blocked**. The observed 66.736-second control
latency also exceeds the proposed 30-second p95 goal; it cannot establish
latency acceptance. The tiny PNG/JPEG gateway smoke belongs to separate
backend evidence and does not replace real-document quality evaluation.
