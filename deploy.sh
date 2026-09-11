#!/usr/bin/env bash
# Deploy the static upload page to S3 (+ CloudFront invalidation).
#
# Reads .env for defaults:
#   S3_BUCKET                  (required) bucket to deploy into
#   WEB_PREFIX                 key prefix for the page files (default: web)
#   CLOUDFRONT_DISTRIBUTION_ID if set, invalidate the page paths after upload
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck source=/dev/null
  source .env
  set +a
fi

: "${S3_BUCKET:?S3_BUCKET is not set (put it in .env or the environment)}"
WEB_PREFIX=${WEB_PREFIX:-web}
WEB_PREFIX=${WEB_PREFIX%/}

FILES=(index.html app.js styles.css)
# Optional site-specific assets, deployed only when present (not in git).
[ -f favicon.ico ] && FILES+=(favicon.ico)

echo "Deploying to s3://${S3_BUCKET}/${WEB_PREFIX}/"
for f in "${FILES[@]}"; do
  aws s3 cp "$f" "s3://${S3_BUCKET}/${WEB_PREFIX}/${f}"
done

if [ -n "${CLOUDFRONT_DISTRIBUTION_ID:-}" ]; then
  paths=(/)
  for f in "${FILES[@]}"; do paths+=("/${f}"); done
  echo "Invalidating CloudFront distribution ${CLOUDFRONT_DISTRIBUTION_ID}: ${paths[*]}"
  invalidation_id=$(aws cloudfront create-invalidation \
    --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --paths "${paths[@]}" \
    --query 'Invalidation.Id' --output text)
  echo "Waiting for invalidation ${invalidation_id} to complete…"
  aws cloudfront wait invalidation-completed \
    --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --id "$invalidation_id"
else
  echo "CLOUDFRONT_DISTRIBUTION_ID not set — skipping cache invalidation."
fi

echo "Done."
