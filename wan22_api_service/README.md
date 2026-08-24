# Wan2.2 I2V API

Lightweight FastAPI wrapper for the official Wan2.2 image-to-video CLI.

## Default paths

- App: this directory
- Model: `/opt/verbascene/models/Wan2.2-I2V-A14B`
- Wan repo: `/opt/verbascene/Wan2.2`
- Data: `/var/lib/verbascene/wan22-api`

Override these values with `WAN_CKPT_DIR`, `WAN_REPO_DIR`, `DATA_ROOT`,
`PYTHON_BIN`, and `TORCHRUN_BIN` in `.env`.

## Start

```bash
cd wan22_api_service
./start.sh
```

## Submit a job

```bash
curl -X POST http://127.0.0.1:7862/v1/i2v \
  -F 'image=@./input.JPG' \
  -F 'prompt=cinematic cyber suspense short drama shot, neon city, intense character emotion' \
  -F 'size=1280*720'
```

## Check status

```bash
curl http://127.0.0.1:7862/v1/jobs/JOB_ID
curl http://127.0.0.1:7862/v1/jobs/JOB_ID/log
curl -o output.mp4 http://127.0.0.1:7862/v1/jobs/JOB_ID/video
```
