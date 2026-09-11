# s3-uploader

A tiny "share a link, get a file" uploader. A Python script generates a
presigned S3 POST, stores the signed policy as a small JSON object in the
bucket, and prints a short shareable URL carrying only a random ID; a
static HTML/CSS/JS page fetches the policy and uploads the chosen file
straight to S3 from the user's browser. There is **no upload backend**.

## How it works

```
  ┌──────────────────────┐    1. presign POST +      ┌────────────────┐
  │ generate_upload_link │ ────────────────────────► │ S3 (presigned) │
  └─────────┬────────────┘    put links/<id>.json    └────────┬───────┘
            │                                                 │
            │  2. print short link                            │
            ▼                                                 │
   https://upload.example.com/?c=<id>&b=<bucket>&r=<region>   │
            │                                                 │
            │  3. share with the recipient                    │
            ▼                                                 │
       ┌─────────┐    4. GET links/<id>.json,                 │
       │ browser │       then POST file directly              │
       └─────────┘ ──────────────────────────────────────────►│
```

The static page never touches AWS credentials — everything sensitive is
contained in the signed policy stored at `links/<id>.json`, and the
unguessable ID in the link is the only thing that locates it. (Old-style
long `?config=` links, where the policy is base64url-encoded into the URL
itself, still work.)

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env
```

## AWS credentials

The script uses the standard boto3 credential chain. Any of the following
will work:

- `AWS_PROFILE` env var pointing at a profile in `~/.aws/credentials`
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars
- An EC2 / ECS / Lambda instance role

The script itself never reads or prints secret values.

## Configure S3 bucket CORS

The bucket must allow `GET` (to fetch `links/<id>.json`) and `POST` (to
upload) from whatever origin serves the static page. Apply this CORS
configuration to the bucket (replace the origin to match where you host
`index.html`):

```json
[
  {
    "AllowedOrigins": ["https://upload.example.com"],
    "AllowedMethods": ["GET", "POST"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```

The bucket should **not** have a public-write policy. The presigned POST
is the only thing authorising the upload.

## Make link configs readable

The page fetches `links/<id>.json` anonymously, so the bucket needs a
read-only policy scoped to that prefix (replace `BUCKET`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::BUCKET/links/*"
    }
  ]
}
```

For this policy to attach and take effect, the bucket's Block Public
Access settings `BlockPublicPolicy` and `RestrictPublicBuckets` must be
disabled (the other two settings can stay on). This exposes **only**
objects under `links/` — each is an unguessable, short-lived upload
policy, no more sensitive than the link itself.

Alternatively, if the bucket sits behind the same CloudFront distribution
that serves the page, no public policy is needed: store the configs under
a prefix CloudFront already serves and pass `--same-origin` so the
printed link is just `?c=<id>` — the page then fetches `links/<id>.json`
from its own origin. For example, with the page served from an origin
path of `/web`, put this in `.env`:

```bash
LINKS_PREFIX=web/links    # stored where CloudFront serves /links/*
SAME_ORIGIN_LINKS=1
```

## Generate a link

```bash
python generate_upload_link.py \
  --bucket my-upload-bucket \
  --region eu-west-2 \
  --page-url https://upload.example.com \
  --prefix incoming \
  --expires-minutes 30 \
  --max-mb 100
```

If you populated `.env`, the short form works too:

```bash
python generate_upload_link.py --expires-minutes 30 --max-mb 100
```

The script prints a single URL. Send that URL to the person who needs to
upload — opening it gives them a file picker and a single Upload button.

## Host the static files

Any static host works because the page is just three files:

- An S3 bucket configured for static website hosting (often fronted by
  CloudFront)
- GitHub Pages, Netlify, Cloudflare Pages, etc.
- For local testing: `python -m http.server 8000` from this directory,
  then open `http://localhost:8000/?config=…`

Whichever you choose, make sure the bucket's CORS `AllowedOrigins` lists
the page's origin exactly (scheme + host + port).

For the S3 + CloudFront setup there is a deploy script:

```bash
./deploy.sh
```

It copies `index.html`, `app.js`, `styles.css` (plus `favicon.ico` if
you've dropped one next to them — it isn't tracked in git) to
`s3://$S3_BUCKET/$WEB_PREFIX/` (default prefix: `web`) and, when
`CLOUDFRONT_DISTRIBUTION_ID` is set in `.env`, invalidates the page
paths and waits for the invalidation to finish.

## Security notes

- **Links are bearer tokens.** The random ID in the link is the only
  thing locating the signed policy, so anyone holding the URL can upload
  until it expires. Treat links like a password — don't post them in
  public channels, and prefer short-lived links.
- **Keep `--expires-minutes` short.** The default (720 = 12 hours) is a
  ceiling, not a recommendation. Use 15–60 minutes when you can.
- **Each link writes to a unique random prefix.** Two recipients can't
  overwrite each other's uploads, and a leaked link can't read or
  overwrite anyone else's files.
- **The bucket must not allow public writes.** All authority comes from
  the signed POST policy, which scopes the write to one prefix and a
  size range.
- **Add lifecycle rules** to expire objects under `incoming/` after a
  few days so the bucket doesn't accumulate forever, and under `links/`
  after ~1 day. A stale `links/` object is harmless — the presigned
  policy inside it expires on its own — but there's no reason to keep it.
- **Untrusted uploads.** If the senders are not fully trusted, run the
  uploaded objects through an antivirus / malware scan (S3 + Lambda or
  GuardDuty Malware Protection) before exposing them downstream.
