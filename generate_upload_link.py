#!/usr/bin/env python3
"""Generate a shareable S3 upload link backed by a presigned POST.

The script prints a URL of the form:

    {page_url}?config=<base64url-encoded JSON>

The JSON carries the S3 endpoint, the presigned form fields, an expiry
timestamp, a max-size hint, and the destination key prefix. The static
page at `page_url` decodes it and POSTs the user's file straight to S3.

Credentials are sourced from the standard boto3 chain (AWS_PROFILE,
environment variables, instance role, etc.) — this script never reads
or prints them.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    load_dotenv()  # populate os.environ from .env if present
    p = argparse.ArgumentParser(
        description="Generate a presigned-POST upload link for an S3 bucket.",
    )
    p.add_argument("--bucket", default=os.environ.get("S3_BUCKET"),
                   help="S3 bucket name (env: S3_BUCKET)")
    p.add_argument("--region", default=os.environ.get("AWS_REGION"),
                   help="AWS region of the bucket (env: AWS_REGION)")
    p.add_argument("--page-url", default=os.environ.get("PAGE_URL"),
                   help="Public URL of the static upload page (env: PAGE_URL)")
    p.add_argument("--prefix", default="incoming",
                   help="Top-level key prefix (default: incoming)")
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


def build_config(args: argparse.Namespace) -> dict:
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

    s3 = boto3.client("s3", region_name=args.region)
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


def encode_config(cfg: dict) -> str:
    raw = json.dumps(cfg, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def main() -> None:
    args = parse_args()
    built = build_config(args)
    token = encode_config(built["config"])
    page = args.page_url.rstrip("/")
    url = f"{page}/?config={token}"

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
