# s3-uploader

A tiny "share a link, get a file" uploader. A Python script generates a
presigned S3 POST and bakes the policy into a single shareable URL; a
static HTML/CSS/JS page decodes the URL and uploads the chosen file
straight to S3 from the user's browser. There is **no upload backend**.

## How it works

```
  ┌──────────────────────┐    1. run script    ┌────────────────┐
  │ generate_upload_link │ ──────────────────► │ S3 (presigned) │
  └─────────┬────────────┘                     └────────┬───────┘
            │  2. base64url-encode the policy + fields  │
            ▼                                           │
   https://upload.example.com/?config=<token>           │
            │                                           │
            │  3. share with the recipient              │
            ▼                                           │
       ┌─────────┐    4. POST file directly             │
       │ browser │ ────────────────────────────────────►│
       └─────────┘                                      │
```

The static page never touches AWS credentials — everything sensitive is
contained in the signed policy embedded in the link.

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

The bucket must allow `POST` from whatever origin serves the static page.
Apply this CORS configuration to the bucket (replace the origin to match
where you host `index.html`):

```json
[
  {
    "AllowedOrigins": ["https://upload.example.com"],
    "AllowedMethods": ["POST"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```

The bucket should **not** have a public-write policy. The presigned POST
is the only thing authorising the upload.

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

## Security notes

- **Links are bearer tokens.** Anyone holding the URL can upload until
  it expires. Treat them like a password — don't post them in public
  channels, and prefer short-lived links.
- **Keep `--expires-minutes` short.** The default (720 = 12 hours) is a
  ceiling, not a recommendation. Use 15–60 minutes when you can.
- **Each link writes to a unique random prefix.** Two recipients can't
  overwrite each other's uploads, and a leaked link can't read or
  overwrite anyone else's files.
- **The bucket must not allow public writes.** All authority comes from
  the signed POST policy, which scopes the write to one prefix and a
  size range.
- **Add a lifecycle rule** to expire objects under `incoming/` after a
  few days so the bucket doesn't accumulate forever.
- **Untrusted uploads.** If the senders are not fully trusted, run the
  uploaded objects through an antivirus / malware scan (S3 + Lambda or
  GuardDuty Malware Protection) before exposing them downstream.
