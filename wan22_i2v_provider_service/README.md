# Wan2.2 I2V Provider Service

Authenticated, single-GPU, single-job-queue API for the original
`Wan-AI/Wan2.2-I2V-A14B` model.

The service implements the existing project's `wan2_i2v_api` `official`
contract:

- `GET /health`
- `POST /v1/i2v/jobs` with `X-API-Key` and multipart fields
- `GET /v1/i2v/jobs/{job_id}` with `X-API-Key`
- `GET /v1/i2v/jobs/{job_id}/result?variant=native` with `X-API-Key`

The two DiT experts, T5, and VAE are loaded directly onto GPU 0 and remain
resident between jobs. The queue executes one generation at a time.

Project configuration:

```env
VIDEO_PROVIDER=wan2_i2v_api
VIDEO_MODEL=Wan-AI/Wan2.2-I2V-A14B
WAN_I2V_API_BASE_URL=http://127.0.0.1:18083
WAN_I2V_API_PROTOCOL=official
WAN_I2V_API_KEY=<provider-api-key>
WAN_I2V_IMAGE_TRANSPORT=upload
```

Example request:

```bash
curl -X POST http://127.0.0.1:18083/v1/i2v/jobs \
  -H "X-API-Key: $WAN_I2V_API_KEY" \
  -F "image=@input.png" \
  -F "prompt=cinematic cyber suspense shot" \
  -F "size=832*480" \
  -F "frames=5" \
  -F "steps=2" \
  -F "fps=16"
```
