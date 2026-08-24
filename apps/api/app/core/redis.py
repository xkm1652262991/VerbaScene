from redis import Redis

from app.core.config import settings


def get_redis_client() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def check_redis() -> bool:
    client = get_redis_client()
    try:
        return bool(client.ping())
    finally:
        client.close()
