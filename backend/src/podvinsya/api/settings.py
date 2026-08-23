"""Configuration the API needs on top of what the migrate step needs.

A subclass, not extra fields on `Settings`: `db/migrations/env.py` builds
`Settings()` directly, and `podvinsya migrate` must not start demanding a
signing key it never uses. Only `build_app` constructs this type.
"""

from podvinsya.config import Settings


class ApiSettings(Settings):
    # No defaults, for the same reason `database_url` has none: an unset
    # signing key that quietly became a constant would make every session
    # cookie in every deployment forgeable by anyone who read the source.
    secret_key: str
    host_password: str

    session_ttl_hours: int = 12
    # §8 calls the threshold «настраиваемый». 40 rather than
    # `IMAGE_PACK_SIZE`: ruling 1 makes a shorter pack legal, so this fires
    # where an operator would want to top a category up, not wherever the
    # pack size happens to sit.
    thin_image_threshold: int = 40

    # §10's «S3-совместимое хранилище медиа». No default for the endpoint
    # or the credentials, for the same reason `database_url` has none: a
    # process that quietly pointed at somebody's scratch bucket would be
    # worse than one that refused to start.
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str = "podvinsya-media"
    s3_region: str = "us-east-1"

    # Twenty megabytes. A photograph off a phone is two to five; a scan of
    # a poster is rarely past fifteen. Past this an upload is a mistake —
    # a video, or the wrong file — and refusing it is cheaper than storing
    # it and discovering the mistake on the big screen.
    max_upload_bytes: int = 20 * 1024 * 1024
    # Per subscriber. Small on purpose: §7.2 makes every frame complete, so
    # a slow reader losing intermediate frames costs narration, not state,
    # and a deep queue would only delay the moment it catches up.
    frame_queue_capacity: int = 32
