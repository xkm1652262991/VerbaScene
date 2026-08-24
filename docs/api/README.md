# API Contract

`openapi.json` is the checked-in frontend/backend contract for the VerbaScene API.

The current contract intentionally contains breaking backend task changes. The
web application is frozen for this upgrade and requires a separate follow-up
adaptation before its consumer-side check is expected to pass again.

Whenever backend routes, request schemas, response schemas, error payloads, or pagination models change, regenerate the contract before updating frontend code.

```bash
cd apps/api
.venv/bin/python scripts/export_openapi.py
.venv/bin/python scripts/check_openapi_contract.py
```

Contract rules enforced by `check_openapi_contract.py`:

- The committed `docs/api/openapi.json` must match the current FastAPI app.
- Common schema components must include `ApiError`, `ErrorResponse`, and `PageMeta`.
- Every operation must declare the unified error response shape for `400`, `404`, `409`, `422`, and `500`.

The web app also has a consumer-side check for the API paths and paginated responses it depends on:

```bash
cd apps/web
npm run check:api-contract
```

Runtime errors are returned as:

```json
{
  "success": false,
  "error": {
    "code": "not_found",
    "message": "没有找到对应资源。",
    "detail": null
  }
}
```

Paginated endpoints should use the `PageResponse[T]` shape:

```json
{
  "success": true,
  "items": [],
  "meta": {
    "total": 0,
    "offset": 0,
    "limit": 100
  }
}
```
