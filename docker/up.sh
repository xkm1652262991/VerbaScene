#!/bin/sh

set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"

docker compose config --quiet
docker build --file docker/api.Dockerfile --tag verbascene-api:local .
docker build --file docker/web.Dockerfile --tag verbascene-web:local .
docker compose up --detach --no-build
docker compose ps
