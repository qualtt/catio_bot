"""Upload recovered photos to the S3 bucket configured in .env, under the keys stored in the database.

For every row in `photos`: find the file in RECOVERY_DIR/photos by sha256, upload it to
S3_BUCKET under `storage_key` (skipped when an object of the same size is already there), and
point `storage_bucket` at S3_BUCKET. Ends with a check that every row has its object.

Usage:
    python -m bot.tools.recovery.upload_s3 --database-url URL [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor

from bot.tools.recovery.common import OUT_DIR, RECONSTRUCTION_PATH


async def run(dry_run: bool) -> None:
    from botocore.exceptions import ClientError
    from sqlalchemy import select, update

    from bot.config import config
    from bot.services.photo_storage import _s3_client
    from db.database import async_session, engine
    from db.models.photo import Photo

    if not config.S3_BUCKET:
        raise SystemExit("S3_BUCKET is not set in .env")
    bucket = config.S3_BUCKET
    paths = {p["sha256"]: OUT_DIR / p["path"] for p in json.loads(RECONSTRUCTION_PATH.read_text())["photos"]}

    async with async_session() as session:
        rows = (await session.execute(select(Photo.id, Photo.sha256, Photo.storage_key, Photo.content_type))).all()

    missing_files = [r for r in rows if r.sha256 not in paths or not paths[r.sha256].exists()]
    if missing_files:
        raise SystemExit(f"{len(missing_files)} photos have no local file, e.g. photo id {missing_files[0].id}")

    client = _s3_client()

    def remote_size(key: str) -> int | None:
        try:
            return client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def upload(row) -> str:
        path = paths[row.sha256]
        if remote_size(row.storage_key) == path.stat().st_size:
            return "exists"
        if dry_run:
            return "would upload"
        client.put_object(
            Bucket=bucket,
            Key=row.storage_key,
            Body=path.read_bytes(),
            ContentType=row.content_type or "image/jpeg",
        )
        return "uploaded"

    results: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, outcome in enumerate(pool.map(upload, rows), start=1):
            results[outcome] = results.get(outcome, 0) + 1
            if i % 200 == 0:
                print(f"{i}/{len(rows)} {results}", flush=True)
    print(f"done: {results}")

    if dry_run:
        await engine.dispose()
        return

    async with async_session() as session:
        await session.execute(update(Photo).where(Photo.storage_bucket != bucket).values(storage_bucket=bucket))
        await session.commit()

    with ThreadPoolExecutor(max_workers=8) as pool:
        absent = [
            r.id
            for r, size in zip(rows, pool.map(lambda r: remote_size(r.storage_key), rows), strict=True)
            if size is None
        ]
    print(f"check: {len(rows) - len(absent)}/{len(rows)} photos present in s3://{bucket}")
    if absent:
        raise SystemExit(f"missing in S3: photo ids {absent[:20]}")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    os.environ["DATABASE_URL"] = args.database_url
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
