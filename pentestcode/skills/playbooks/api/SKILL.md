---
name: playbook-api
description: API security testing — OWASP API Top 10, BOLA/IDOR chains, mass assignment, excessive data exposure, rate limiting. Load when target is a REST/GraphQL/JSON API (incl. mobile backend).
tags: [vuln_assess, exploitation]
---

# API Pentest — OWASP API Top 10

APIs fail differently than web apps: no UI to fuzz, auth is token-based, objects are addressable by ID. Test the OBJECT layer, not just the endpoint.

## #1 BOLA / IDOR — test EVERY object reference
The single most common API finding. For each endpoint returning object data:
1. Grab a valid object ID (your own, from responses)
2. Swap in another user's ID — same token
3. Also try: sequential IDs, UUID prediction, deleted IDs, other ID formats
```
# automate once endpoints are known
for id in $(seq 1 500); do
  curl -s -H "Authorization: Bearer $TOK" https://api.t/v1/orders/$id | head -c 200
done
```
- Horizontal (same role, other user) AND vertical (user token → /admin/*) both ways.
- GraphQL: query nesting abuse — `{ user(id: 1) { friends { ssn } } }`, batching attacks.

## #2 Broken auth on API
- Token: no expiry, no rotation, `alg:none`, JWT key confusion, refresh token reuse detection absent.
- OAuth: redirect_uri manipulation, `state` missing, scope escalation.
- Password reset flows keyed by enumerable identifiers.

## #3 Mass assignment
Send extra fields in the create/update body: `{"role":"admin","verified":true,"balance":9999}`. Frameworks bind blindly (Rails `attr_accessible`, Spring `@ModelAttribute`, Mongoose strict:false). Test on register, profile update, and EVERY PATCH.

## #4 Excessive data exposure
Compare response schemas between users. Common leaks: password hashes, internal IDs, other users' PII in list endpoints, `debug:true` payloads, full object when UI shows 3 fields. Diff admin-vs-user responses field by field.

## #5 Rate limiting & resource abuse
- Login/OTP endpoints without lockout → brute force / SMS bombing (report, don't run to exhaustion).
- Unbounded pagination (`?limit=1000000`), unbounded file upload, unauthenticated expensive queries (ReDoS, GraphQL depth).

## #6 Injection surface
- Params in URL, headers (`X-Forwarded-For`, custom trace headers), and JSON body — all three.
- SSTI/NoSQL/SQLi/SSRF payloads adapted per content-type; `Content-Type` switch (json→xml) can unlock XXE.

## Methodology order
1. Map: `/openapi.json`, `/swagger.json`, `/graphql` introspection, mobile app traffic (pair with `svc-mobile-android`)
2. Enumerate object IDs per resource → BOLA sweep
3. Authz matrix: role × endpoint (collect tokens for each role available)
4. Mass assignment on every write endpoint
5. Response schema diff per role
6. Rate limits, injection, transport (TLS, cert pinning on mobile side)

## Evidence for report
One curl + response pair per finding, showing: victim object belongs to user A, attacker token = user B. Include the exact reproduction steps — API findings retest easily, keep them scriptable.
