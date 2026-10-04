# GPU translation candidate: public DEV result

Date: 2026-10-04. Status: candidate selected for file diagnostics; **not release acceptance**.

The original 540 scheduled FLORES DEV rows completed on one A100 GPU in 711.646 seconds. Semantic assessment by the calibrated AI evaluator produced 498 PASS, 39 FAIL and 3 UNCERTAIN. Uncertain rows remain in the denominator. All 18 directions exceed 70%; the minimum is Korean to Vietnamese at 22/30 (73.3%). This is one public DEV run, not three sealed TEST runs or native-human sign-off.

- Model: `tencent/Hy-MT2-7B-GGUF`, Q4_K_M, revision `ab8472660ac61fac25f1af43fac2599d52a8a775`.
- Frozen raw runtime: context 2048, input 1024/output 512, greedy seed 42, full 33/33 GPU offload, official EOS overrides.
- Median response: 1266.4 ms; p95: 1798.5 ms. Peak owned VRAM: 5200 MiB. Guarded cleanup restored 8772 MiB free with all incumbents preserved.
- DuyTa remains unchanged. No additional GPU was rented.

| Direction | PASS / 30 | FAIL | UNCERTAIN |
|---|---:|---:|---:|
| ar>vi | 29 | 0 | 1 |
| de>vi | 27 | 3 | 0 |
| en>vi | 28 | 2 | 0 |
| es>vi | 28 | 2 | 0 |
| fr>vi | 27 | 3 | 0 |
| ja>vi | 27 | 3 | 0 |
| ko>vi | 22 | 7 | 1 |
| ru>vi | 29 | 1 | 0 |
| vi>ar | 27 | 3 | 0 |
| vi>de | 28 | 2 | 0 |
| vi>en | 29 | 1 | 0 |
| vi>es | 30 | 0 | 0 |
| vi>fr | 30 | 0 | 0 |
| vi>ja | 28 | 2 | 0 |
| vi>ko | 29 | 1 | 0 |
| vi>ru | 28 | 2 | 0 |
| vi>zh-Hans | 28 | 2 | 0 |
| zh-Hans>vi | 24 | 5 | 1 |

## Evidence and limits

Raw SHA-256: `a1d6e00eedb56c0e6637d41743873e1c6ea3c394f435d24a6733749bf0d892c4`.

Detailed immutable raw outputs, evaluator judgments, source bindings and assessment are outside Git at `/home/ict/secai-multilang-benchmark-evidence`. First 18 DEV outputs exactly matched smoke; judgments are reused once with an explicit binding. No sample, reference, threshold or denominator was changed.

The separate real-file diagnostic uses measured context 4096 to reduce chunk fragmentation. Initial vi→en failed at a quantity falsely classified as a software version (`nearly 1.5`); that original failure is retained. Reviewed fix `589d9ff` was followed by successful vi↔en file regressions. The effective file evidence comprises 16 initial successful DOCX files and two postfix files, with 30 source blocks per direction. It is a mixed execution set, not a single frozen rerun.

All 540 actual file outputs are bound to their source blocks and result hashes. Of these, 539 exactly match the raw DEV source/output and reuse the corresponding judgments; the remaining es→vi output received a new blind PASS. Totals remain 498 PASS, 39 FAIL, 3 UNCERTAIN, with all 18 directions above 70%. The separate assessment is `gpu-hymt2-7b-q4/file-diagnostic/semantic/final-file-assessment.json`, SHA-256 `284cb6c1fbe61e711c0e9fcee9c7d27a43e361c75e9dbcc508a84b21bb5c0517` under the evidence root.

## Devspace integration smoke, 2026-10-04

An isolated Devspace preview runs at `http://100.74.146.12:5178/` against gateway port 18181. The existing served Devspace and main branch were not overwritten. Frontend `npm ci` and `npm run build` succeeded. Browser interaction confirmed v3 Vietnamese→Chinese translation and a batch of two TXT files with completed results and download controls.

The same browser then submitted the existing Vietnamese OCR calibration image and a TXT file together, targeting Japanese. Both completed with `修正プログラムはサーバーでテストされました。`. OCR raw text remains available for inspection; automatic OCR correction is disabled and the UI reports that limitation. This small integration smoke does not qualify OCR accuracy on difficult documents.

The existing Qwen OCR service is reused with scheduling patches `10dd5d2` and `d288374`; MT uses the independently reviewed on-demand runtime `58b2f62`. They share an inherited process lock and durable cleanup veto. The OCR stage completed in 12.22 seconds, measured peak 4658 MiB, minimum free 4109 MiB, and restored 8772 MiB free before MT. Each MT request loads and cleans up its own GPU child. DuyTa serving was not changed. No direct OCR broker route was activated.

The combined backend suite completed: 1595 passed, 7 failed, 1 skipped. The seven failures are the existing external Bughunt/PoC fixture and exploit-preview dependencies; the suite is not reported as fully passing. The narrower scheduling and adapter tests passed independent review. Sealed TEST, native-human qualification, difficult-page OCR quality, final deployment and main integration remain outside this completed DEV/browser smoke.
