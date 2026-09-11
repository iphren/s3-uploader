#!/usr/bin/env python3
"""Generate a shareable S3 upload link backed by a presigned POST.

The script prints a URL of the form:

    {page_url}?c=<random-id>&b=<bucket>&r=<region>   # direct-to-S3 fetch
    {page_url}?<random-id>                           # with --same-origin

The presigned-POST config (S3 endpoint, form fields, expiry timestamp,
max-size hint, destination key prefix) is stored as JSON at
`links/<random-id>.json` in the bucket itself. The static page at
`page_url` fetches it and POSTs the user's file straight to S3. The
config object must be fetchable by the page: either public GET on
`links/*`, or the bucket served behind the page's own CloudFront
distribution with `--same-origin` (see README).

Credentials are sourced from the standard boto3 chain (AWS_PROFILE,
environment variables, instance role, etc.) — this script never reads
or prints them.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    load_dotenv()  # populate os.environ from .env if present
    p = argparse.ArgumentParser(
        description="Generate a presigned-POST upload link for an S3 bucket. "
                    "The link config is stored at links/<id>.json in the bucket, "
                    "which the page must be able to fetch anonymously (see README).",
    )
    p.add_argument("--bucket", default=os.environ.get("S3_BUCKET"),
                   help="S3 bucket name (env: S3_BUCKET)")
    p.add_argument("--region", default=os.environ.get("AWS_REGION"),
                   help="AWS region of the bucket (env: AWS_REGION)")
    p.add_argument("--page-url", default=os.environ.get("PAGE_URL"),
                   help="Public URL of the static upload page (env: PAGE_URL)")
    p.add_argument("--prefix", default="incoming",
                   help="Top-level key prefix (default: incoming)")
    p.add_argument("--links-prefix", default=os.environ.get("LINKS_PREFIX", "links"),
                   help="Key prefix for stored link configs "
                        "(env: LINKS_PREFIX, default: links)")
    p.add_argument("--same-origin", action="store_true",
                   default=bool(os.environ.get("SAME_ORIGIN_LINKS")),
                   help="Omit bucket/region from the link so the page fetches "
                        "links/<id>.json from its own origin — for buckets served "
                        "behind the same CloudFront distribution as the page "
                        "(env: SAME_ORIGIN_LINKS)")
    p.add_argument("--expires-minutes", type=int, default=720,
                   help="Link lifetime in minutes (default: 720)")
    p.add_argument("--max-mb", type=int, default=50000,
                   help="Maximum file size in megabytes (default: 50000)")
    p.add_argument("--content-type", default=None,
                   help="Restrict uploads to this Content-Type (optional)")
    args = p.parse_args()

    missing = [name for name, val in (
        ("--bucket / S3_BUCKET", args.bucket),
        ("--region / AWS_REGION", args.region),
        ("--page-url / PAGE_URL", args.page_url),
    ) if not val]
    if missing:
        p.error("missing required value(s): " + ", ".join(missing))
    return args


def build_config(s3, args: argparse.Namespace) -> dict:
    max_bytes = args.max_mb * 1024 * 1024
    expires_in = args.expires_minutes * 60
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    key_prefix = f"{args.prefix.strip('/')}/{date.today().isoformat()}/{uuid.uuid4()}/"
    key_template = key_prefix + "${filename}"

    fields: dict = {}
    conditions: list = [
        ["starts-with", "$key", key_prefix],
        ["content-length-range", 1, max_bytes],
    ]
    if args.content_type:
        fields["Content-Type"] = args.content_type
        conditions.append({"Content-Type": args.content_type})

    try:
        presigned = s3.generate_presigned_post(
            Bucket=args.bucket,
            Key=key_template,
            Fields=fields or None,
            Conditions=conditions,
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError) as e:
        sys.stderr.write(f"Failed to generate presigned POST: {e}\n")
        sys.exit(1)

    return {
        "config": {
            "url": presigned["url"],
            "fields": presigned["fields"],
            "expiresAt": expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "maxBytes": max_bytes,
            "keyPrefix": key_prefix,
        },
        "expires_at": expires_at,
        "key_prefix": key_prefix,
    }


def store_config(s3, bucket: str, links_prefix: str, cfg: dict) -> str:
    """Upload the config JSON to <links_prefix>/<id>.json; the id is the bearer secret."""
    # 64 bits: unguessable over HTTP, and short (11 chars) for tidy links.
    link_id = secrets.token_urlsafe(8)
    body = json.dumps(cfg, separators=(",", ":")).encode("utf-8")
    try:
        s3.put_object(
            Bucket=bucket,
            Key=f"{links_prefix.strip('/')}/{link_id}.json",
            Body=body,
            ContentType="application/json",
            CacheControl="no-store",
        )
    except (BotoCoreError, ClientError) as e:
        sys.stderr.write(f"Failed to store link config in bucket: {e}\n")
        sys.exit(1)
    return link_id


def main() -> None:
    args = parse_args()
    s3 = boto3.client("s3", region_name=args.region)
    built = build_config(s3, args)
    link_id = store_config(s3, args.bucket, args.links_prefix, built["config"])
    page = args.page_url.rstrip("/")
    if args.same_origin:
        url = f"{page}/?{link_id}"
    else:
        url = f"{page}/?c={link_id}&b={args.bucket}&r={args.region}"

    print("Upload link:")
    print(f"  {url}")
    print()
    print(f"Expires at:  {built['expires_at'].isoformat()}")
    print(f"Destination: s3://{args.bucket}/{built['key_prefix']}")
    print(f"Max size:    {args.max_mb} MB")
    if args.content_type:
        print(f"Content-Type restricted to: {args.content_type}")


if __name__ == "__main__":
    main()
