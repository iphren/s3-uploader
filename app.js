// Static S3 upload page. Reads a presigned-POST config either by fetching
// links/<id>.json from the bucket (short `?c=<id>&b=<bucket>&r=<region>`
// links) or from the legacy `?config=` query param (base64url-encoded JSON),
// then POSTs the chosen file directly to S3. No credentials live here —
// everything sensitive is inside the fetched config / the link.

(async () => {
  const $ = (id) => document.getElementById(id);
  const els = {
    expires: $("expires"),
    maxSize: $("max-size"),
    prefix: $("prefix"),
    meta: $("meta"),
    file: $("file"),
    upload: $("upload"),
    status: $("status"),
    progress: $("progress"),
    error: $("error"),
    success: $("success"),
  };

  const showError = (msg) => {
    els.error.textContent = msg;
    els.error.hidden = false;
  };
  const clearError = () => {
    els.error.textContent = "";
    els.error.hidden = true;
  };
  const showSuccess = (msg) => {
    els.success.textContent = msg;
    els.success.hidden = false;
  };
  const setStatus = (msg) => {
    els.status.textContent = msg;
  };

  // base64url -> Uint8Array -> string
  const decodeBase64Url = (s) => {
    const padded = s.replace(/-/g, "+").replace(/_/g, "/");
    const pad = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
    const bin = atob(padded + pad);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  };

  const formatBytes = (n) => {
    if (!Number.isFinite(n)) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  };

  const formatRemaining = (ms) => {
    if (ms <= 0) return "expired";
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}h ${m}m ${sec}s`;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
  };

  const checkShape = (cfg) => {
    if (!cfg || typeof cfg.url !== "string" || !cfg.fields || typeof cfg.fields !== "object") {
      throw new Error("Missing url/fields");
    }
    return cfg;
  };

  // Fetch links/<id>.json using the id/bucket/region from a short link.
  // The patterns keep a crafted link from pointing the page at an
  // attacker-controlled host — only real S3 endpoints can be built.
  const fetchConfig = async (params) => {
    const id = params.get("c");
    const bucket = params.get("b");
    const region = params.get("r");
    if (!/^[A-Za-z0-9_-]{16,64}$/.test(id)) {
      showError("This upload link is malformed and cannot be decoded.");
      return null;
    }
    let configUrl;
    if (bucket === null && region === null) {
      // Bucket served behind the same origin as this page (e.g. CloudFront).
      configUrl = `links/${id}.json`;
    } else if (
      /^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(bucket) &&
      /^[a-z]{2}(-[a-z0-9]+)+$/.test(region)
    ) {
      // Path-style, because virtual-hosted style breaks TLS for bucket
      // names containing dots (the wildcard cert covers one label only).
      configUrl = `https://s3.${region}.amazonaws.com/${bucket}/links/${id}.json`;
    } else {
      showError("This upload link is malformed and cannot be decoded.");
      return null;
    }
    let resp;
    try {
      resp = await fetch(configUrl, { cache: "no-store" });
    } catch (e) {
      showError(
        "Could not load the upload link details. This is often a bucket misconfiguration — " +
        "the bucket must allow public GET on links/* and list this page's origin in its CORS rules."
      );
      return null;
    }
    if (!resp.ok) {
      showError(
        resp.status === 403 || resp.status === 404
          ? "This upload link is invalid or has expired. Please request a new one."
          : `Could not load the upload link details (HTTP ${resp.status}).`
      );
      return null;
    }
    try {
      return checkShape(await resp.json());
    } catch (e) {
      showError("This upload link is malformed and cannot be decoded.");
      return null;
    }
  };

  // Load the config from the URL. Returns null on any failure (and surfaces an error).
  const loadConfig = async () => {
    const params = new URLSearchParams(location.search);
    if (params.get("c")) return fetchConfig(params);

    // Legacy long links: the whole config is base64url-encoded in ?config=.
    const raw = params.get("config");
    if (!raw) {
      showError("This page needs an upload link with its details attached. Ask the sender for a fresh upload link.");
      return null;
    }
    try {
      return checkShape(JSON.parse(decodeBase64Url(raw)));
    } catch (e) {
      showError("This upload link is malformed and cannot be decoded.");
      return null;
    }
  };

  const config = await loadConfig();
  if (!config) return;

  // Render meta.
  if (config.maxBytes) els.maxSize.textContent = formatBytes(config.maxBytes);
  if (config.keyPrefix) {
    els.prefix.textContent = config.keyPrefix;
    els.meta.classList.add("has-prefix");
  }

  // Expiry countdown.
  let expiresAt = null;
  if (config.expiresAt) {
    const t = Date.parse(config.expiresAt);
    if (!Number.isNaN(t)) expiresAt = t;
  }

  const isExpired = () => expiresAt !== null && Date.now() >= expiresAt;

  const tick = () => {
    if (expiresAt === null) {
      els.expires.textContent = "no expiry set";
      return;
    }
    const remaining = expiresAt - Date.now();
    els.expires.textContent = formatRemaining(remaining);
    if (remaining <= 0) {
      els.upload.disabled = true;
      showError("This upload link has expired. Please request a new one.");
    }
  };
  tick();
  setInterval(tick, 1000);

  // Enable the upload button only once a file is selected and not expired.
  const refreshButton = () => {
    els.upload.disabled = !els.file.files?.length || isExpired();
  };
  els.file.addEventListener("change", () => {
    clearError();
    refreshButton();
  });
  refreshButton();

  // Try to extract a human-readable message from S3's XML error body.
  const extractS3Message = (xml) => {
    const m = /<Message>([^<]+)<\/Message>/i.exec(xml || "");
    return m ? m[1] : (xml || "");
  };

  els.upload.addEventListener("click", () => {
    clearError();
    els.success.hidden = true;

    const file = els.file.files?.[0];
    if (!file) {
      showError("Please choose a file first.");
      return;
    }
    if (config.maxBytes && file.size > config.maxBytes) {
      showError(`File is ${formatBytes(file.size)}, which exceeds the ${formatBytes(config.maxBytes)} limit.`);
      return;
    }
    if (isExpired()) {
      showError("This upload link has expired. Please request a new one.");
      return;
    }

    // S3 requires fields BEFORE the file in the multipart body.
    const fd = new FormData();
    for (const [k, v] of Object.entries(config.fields)) fd.append(k, v);
    fd.append("file", file);

    // Use XHR (not fetch) so we can show real upload progress.
    const xhr = new XMLHttpRequest();
    xhr.open("POST", config.url, true);

    els.progress.hidden = false;
    els.progress.value = 0;
    setStatus("Uploading…");
    els.upload.disabled = true;
    els.file.disabled = true;

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = (e.loaded / e.total) * 100;
        els.progress.value = pct;
        setStatus(`Uploading… ${pct.toFixed(0)}% (${formatBytes(e.loaded)} / ${formatBytes(e.total)})`);
      }
    };

    xhr.onload = () => {
      els.file.disabled = false;
      refreshButton();
      if (xhr.status >= 200 && xhr.status < 300) {
        setStatus("");
        els.progress.hidden = true;
        const keyTemplate = config.fields.key || "";
        const finalKey = keyTemplate.replace("${filename}", file.name);
        showSuccess(`Upload complete. Stored as: ${finalKey}`);
        els.upload.disabled = true; // single-shot link
      } else {
        els.progress.hidden = true;
        setStatus("");
        const detail = extractS3Message(xhr.responseText);
        showError(`Upload failed (HTTP ${xhr.status}). ${detail}`.trim());
      }
    };

    xhr.onerror = () => {
      els.file.disabled = false;
      refreshButton();
      els.progress.hidden = true;
      setStatus("");
      showError(
        "Network error during upload. This is often a CORS misconfiguration on the S3 bucket — " +
        "the bucket must allow POST from this page's origin."
      );
    };

    xhr.send(fd);
  });
})();
