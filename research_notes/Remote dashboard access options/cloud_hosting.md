# Free or near-free cloud hosting for a private, single-user Streamlit dashboard (as of Nov 2026)

Context: the app reads a local SQLite research DB that a scheduler on the Mac updates. Optional pages use Robinhood OAuth tokens and Alpaca API keys. All pages below were fetched on 2026-11-14 unless noted. Most vendor pages carry no "last updated" date, so "fetched 2026-11-14" is the only date available for them.

## Current free-tier limits (RAM, CPU, hours, sleep or cold start, egress), per platform

### Takeaway
Streamlit Community Cloud (SCC) is the only option that is truly free, private and Streamlit-native, but it allows only one private app and sleeps after 12h of no traffic. Two hosts that were free in earlier years now cost money: Hugging Face now needs PRO ($9/mo) for any Docker or Gradio Space, and Fly.io has no free tier for new orgs. Oracle Always Free has the most capacity (Arm, 2 OCPU / 12 GB) but you run the server yourself and it can be reclaimed when idle. Render, Koyeb and Railway's free tiers are too small or sleep too much to be comfortable for Streamlit. Cloud Run's free tier is generous for one user.

### Cited Findings
**Streamlit Community Cloud**
- Approximate limits "as of February 2024": CPU 0.078 to 2 cores, memory 690 MB to 2.7 GB, storage up to 50 GB. "These limits may change at any time without notice." — [SCC: Manage your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app)
- Apps with no traffic for 12 hours go to sleep. Any viewer wakes it by clicking "Yes, get this app back up!" — [SCC: Manage your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app)
- All apps are hosted in the US (not configurable) on Debian 11. Updates from GitHub are rate-limited to 5 per minute. The IP addresses "may change at any time without notice". — [SCC: Status and limitations](https://docs.streamlit.io/deploy/streamlit-community-cloud/status)

**Hugging Face Spaces**
- CPU Basic is 2 vCPU / 16 GB RAM / 50 GB of non-persistent disk with no hourly cost. However: "Gradio and Docker Spaces run on compute and require a paid plan to create: PRO for personal accounts, Team or Enterprise for organizations." Only Static Spaces are free for everyone. Free accounts may host up to 2 Gradio Spaces on ZeroGPU. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- The SDK options listed are Gradio, Docker and static HTML. There is no native Streamlit SDK, so Streamlit has to run as a Docker Space. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- PRO costs $9/month. CPU Upgrade (8 vCPU / 32 GB) costs $0.03/hr, about $22/mo. — [HF pricing](https://huggingface.co/pricing)
- On free hardware the Space "will go to sleep and stop executing after a period of time if unused". The docs do not give the duration. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- Outbound requests are allowed only on ports 80, 443 and 8080. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)

**Render (free web service)**
- Spins down after 15 minutes without inbound traffic. Spin-up "takes about one minute". — [Render: Free instances](https://render.com/docs/free)
- 750 free instance hours per workspace per month. When they run out, all free services are suspended until next month. Free services count against the included outbound bandwidth. With no payment method on file, overage suspends the services instead of billing them. — [Render: Free instances](https://render.com/docs/free)
- The free page does not state the RAM or CPU for free instances. — [Render: Free instances](https://render.com/docs/free)

**Fly.io**
- "New organizations don't have a free tier or a monthly free usage allowance." New accounts get a $1 trial credit valid for 7 days. A personal org's bill under $5/month is waived. Legacy free allowances remain only for orgs on plans discontinued in Oct 2024. — [Fly pricing](https://docs.fly.io/about/pricing)
- shared-cpu-1x prices: 256 MB $2.19/mo, 512 MB $3.69/mo, 1 GB $6.70/mo (iad/ewr). — [Fly pricing](https://docs.fly.io/about/pricing)

**Railway**
- Trial: a one-time $5 credit that expires in 30 days, no card required, up to 2 vCPU / 1 GB RAM per service, 500 MB max volume. — [Railway pricing](https://railway.com/pricing)
- Free plan: $0/mo with $1 of usage credit per month, up to 1 vCPU / 0.5 GB RAM per service, 1 project, 3 services, 500 MB max volume, no custom domains. — [Railway pricing](https://railway.com/pricing)
- Hobby: $5/mo, which includes $5 of usage. — [Railway pricing](https://railway.com/pricing)

**Koyeb**
- One free instance per organization: 512 MB RAM, 0.1 vCPU, 2 GB SSD. It runs in Frankfurt or Washington DC only and scales to zero after 1 hour without traffic. — [Koyeb instances](https://www.koyeb.com/docs/reference/instances)
- "You will never be charged when using the free web Service." — [Koyeb pricing FAQ](https://www.koyeb.com/docs/faqs/pricing)

**Google Cloud Run**
- Free tier for request-based billing (Tier 1 regions): first 180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests per month. Instance-based billing gets 240,000 vCPU-s and 450,000 GiB-s. The free tier is aggregated per billing account. — search snippets of [Cloud Run pricing](https://cloud.google.com/run/pricing). The page itself was truncated when fetched, so these figures come from the search snippet, not a full page read.

**Oracle Cloud Always Free**
- Up to 2 AMD VM.Standard.E2.1.Micro instances (1/8 OCPU, 1 GB RAM, 50 Mbps internet). — [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- Ampere A1 Flex: first 1,500 OCPU-hours and 9,000 GB-hours per month, "equivalent to 2 OCPUs and 12 GB of memory" for Always Free tenancies. Note that older sources widely cite 4 OCPU / 24 GB; the current page says 2 / 12. — [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- 10 TB/month of outbound data transfer. Resources must be in the home region. — [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- Idle reclamation: an instance "may be reclaimed" if, over 7 days, 95th-percentile CPU < 20%, network < 20% and (A1 only) memory < 20%. — [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

**Other options**
- Modal Starter: $30/month of free credits and no monthly fee. CPU costs $0.0000131 per physical core-second (1 core = 2 vCPU, minimum 0.125 core). Memory costs $0.00000222 per GiB-second. Includes "Scheduled and Web Functions (limited)". — [Modal pricing](https://modal.com/pricing)
- Posit Connect Cloud supports Streamlit. The beta-era free plan listed 5 apps, 4 GB / 2 CPU and 20 active hours. A Posit FAQ (via search snippet) says free-plan usage credits will be enforced from the first usage period starting on or after 2026-11-01. Private sharing and private GitHub repos appear to be paid features. — [Connect Cloud beta announcement (Dec 2024)](https://docs.posit.co/connect-cloud/whats-new/posts/2024-12-01-connect-cloud-beta.html); [Connect Cloud FAQ](https://docs.posit.co/connect-cloud/user/support/02-faqs.html); [Posit Connect Cloud product page](https://posit.co/products/enterprise/connect-cloud). These sources conflict on the app count ("5 apps" vs "unlimited"), and the live plans page could not be fetched.

### Inferences
- Even a basic Streamlit app with pandas usually needs at least 512 MB to 1 GB of RAM. That makes Koyeb (512 MB / 0.1 vCPU) and Railway Free (0.5 GB) marginal. The $1/month credit on Railway Free would not cover an always-on service.
- On Cloud Run with request-based billing, an open Streamlit WebSocket probably counts as an in-flight request. If so, CPU and memory are billed while the tab stays open. For one user browsing a few hours a day, 180k vCPU-s (~50 vCPU-hours) is likely enough at 1 vCPU.
- Oracle A1 is the only free option with enough capacity to also run the scheduler in the cloud. A keepalive or cron job does not guarantee protection from idle reclamation: a brief periodic job can leave 95th-percentile CPU below 20%, with network and memory also under Oracle's thresholds, so Oracle may still reclaim the instance. Treat reclamation as a remaining risk and keep off-instance backups of the database and configuration.

### Gaps
- The RAM and CPU of Render's free instance were not on the fetched page. Older Render pricing showed 512 MB / 0.1 CPU, but this was not verified.
- The HF free-hardware sleep duration is not documented on the fetched page. It was historically 48h, which is unverified.
- Cloud Run egress free allowance (historically 1 GiB/month from North America) and Tier-2 values: the pricing page was truncated.
- Posit Connect Cloud's current free-plan limits and whether it allows private content: the live plans page did not render.

## Persistent disk: is there one, and is it ephemeral?

### Takeaway
None of the PaaS free tiers keep local disk across restarts. That includes SCC, HF, Render free, Koyeb free and Cloud Run. Railway gives up to 500 MB of volume and Fly volumes cost $0.15/GB-month. Oracle gives 200 GB of persistent block storage. On most hosts the SQLite file has to be fetched at startup or read remotely.

### Cited Findings
- HF Spaces disk "is ephemeral, meaning its content will be lost if your Space restarts or is stopped". HF recommends attaching Storage Buckets as volumes, mounted read-write or read-only. — [HF Spaces storage](https://huggingface.co/docs/hub/spaces-storage)
- Render free web services do not support persistent disks. — [Render: Free instances](https://render.com/docs/free)
- Fly volumes cost $0.15/GB-month of provisioned capacity. A stopped Machine is billed $0.15/GB-month for its rootfs. — [Fly pricing](https://docs.fly.io/about/pricing)
- Railway Trial and Free allow a 500 MB max volume. — [Railway pricing](https://railway.com/pricing)
- Koyeb free has a 2 GB SSD. NVMe Volumes appear under the Pro plan features. — [Koyeb instances](https://www.koyeb.com/docs/reference/instances); [Koyeb pricing](https://www.koyeb.com/pricing)
- Oracle gives 200 GB of combined boot and block volume storage plus 5 backups. Boot volumes default to 50 GB. — [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- SCC lists up to 50 GB of storage. Persistence is not documented, but in practice an app's files are rebuilt from the repo on redeploy or reboot. — [SCC: Manage your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app)

### Inferences
- For read-only dashboard use, an ephemeral disk is acceptable. The app can download the latest DB snapshot from object storage at startup or on a TTL cache. Persistence on the host is only required if the cloud app writes data.

### Gaps
- The price of HF Storage Buckets was not fetched.
- Whether SCC's filesystem survives sleep and wake cycles is not documented.
- Whether a Koyeb free instance's SSD survives a restart was not verified.

## Built-in auth or access control for viewers

### Takeaway
Three options have real built-in viewer allowlists: SCC (email allowlist), HF private Spaces (owner and collaborators only, so a 404 for everyone else) and Cloud Run + IAP (a Google identity allowlist enforced before the request reaches the app). Render, Fly, Railway, Koyeb and Oracle give you a public URL. On those you must add auth yourself: Streamlit's `st.login` (OIDC), Cloudflare Access, or a Tailscale-only listener.

### Cited Findings
- SCC: "if your repo is private your app will be private". "You are only allowed one private app at a time." Viewers are invited by email and sign in with Google OAuth or single-use emailed links. Caveat: an invited viewer gets analytics access for that app and your public apps, "and they can invite others". — [SCC: Share your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)
- HF private Spaces: source and running app are accessible only to the owner and collaborators. Others get a 404. A private Space cannot use embed URLs or custom domains. "Protected" visibility, available with PRO, makes the app public while keeping the code private. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- Cloud Run IAP: enabled on the service itself through Security, then Require authentication, then IAP, or with `gcloud run deploy --iap`. It covers all ingress paths, including `run.app` URLs, so no load balancer is required. Projects without an organization need console setup or a custom OAuth client. To allow outside users, set the consent-screen audience to External. IAP cannot be configured on both a load balancer and the service. — [Cloud Run IAP docs](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)
- Streamlit in-app auth: `st.login()` uses OIDC with Google, Entra, Okta or Auth0, plus `st.user`. It needs `[auth]` secrets (`redirect_uri`, `cookie_secret`, `client_id`, `client_secret`, `server_metadata_url`). It handles authentication only; your code must check `st.user.email` against an allowlist itself. — [Streamlit authentication](https://docs.streamlit.io/develop/concepts/connections/authentication)

### Inferences
- An `st.login` gate runs inside the app process. The app, and any code paths an unauthenticated session can reach before the check, are still exposed to the internet. An edge gate (IAP, SCC allowlist, HF private, Cloudflare Access) is stronger for pages that hold broker tokens.

### Gaps
- IAP pricing and whether IAP is GA for Cloud Run: the fetched page covers neither.
- Whether Render, Koyeb or Railway offer any built-in basic-auth or password protection was not checked.

## Secrets storage, and who can read the secrets

### Takeaway
Every platform injects secrets as environment variables or a `secrets.toml` that the app code can read. Anyone who can push code can therefore read them, because their code can print them. Platform staff access is not documented anywhere I fetched. HF is the most explicit: secret values cannot be read back from the settings page, and secrets are not copied when someone duplicates the Space.

### Cited Findings
- SCC: secrets are saved in the app's settings and can be updated at any time. The docs do not cover encryption at rest or which workspace roles can view them. — [SCC secrets management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- HF: secret values "cannot be read from the Space's settings page once set" and are not added to duplicated Spaces. Variables are public. Secrets reach the app as environment variables. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- SCC viewers can invite other viewers. A viewer sees the running app, not the secrets. — [SCC: Share your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)

### Inferences
- On every platform, GitHub collaborators with push access to the deploy branch can read secrets, because they can add code that logs them. Keep the deploy repo single-owner.
- Cloud Run can use Secret Manager with IAM. That gives the clearest audit trail of the options here. This is based on general GCP knowledge and was not fetched in this session.

### Gaps
- No fetched source documents platform-staff access to secrets or encryption at rest for SCC, Render, Railway, Koyeb or Fly. Check each vendor's security or trust page before uploading broker tokens.

## WebSocket support (Streamlit needs it)

### Takeaway
Streamlit-native hosts (SCC, HF) obviously support it. Render explicitly documents WebSocket handling. For the others I assumed support from general knowledge and did not verify it in this session.

### Cited Findings
- Render: WebSocket messages count as inbound traffic and prevent spin-down. A new WebSocket connection wakes a suspended service. — [Render: Free instances](https://render.com/docs/free)
- HF: Streamlit runs as a Docker Space. Only outbound ports 80, 443 and 8080 are restricted; that restriction does not limit inbound WebSocket. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview)

### Inferences
- On Render, an open browser tab keeps the service awake and burns the 750 free hours. One instance running 24/7 is about 720h, so it fits.

### Gaps
- WebSocket support on Fly.io, Railway, Koyeb and Cloud Run was not verified this session. All are widely reported to support it. The Cloud Run request timeout (max 60 min, historically) would cut long-lived Streamlit sessions, and Streamlit reconnects automatically; also unverified.
- Whether IAP-on-Cloud-Run proxies WebSockets cleanly is not stated on the IAP page.

## Is a credit card required?

### Takeaway
No card is needed for SCC, Render free, Railway Trial or Free, Fly's 7-day trial, or Turso free. Fly beyond the trial, Koyeb and GCP need a card. HF needs PRO ($9/mo, so a card) for a Docker or Streamlit Space.

### Cited Findings
- Fly: no card needed to start the trial. After it ends, all orgs need a card on file. — [Fly pricing](https://docs.fly.io/about/pricing)
- Railway Trial: "No credit card required". — [Railway pricing](https://railway.com/pricing)
- Render: a payment method only matters for overage. Without one, services are suspended rather than billed. — [Render: Free instances](https://render.com/docs/free)
- Koyeb: "We require a credit card to prevent fraud and abuse", with a $29 pre-authorization hold that is cancelled right away. — [Koyeb pricing FAQ](https://www.koyeb.com/docs/faqs/pricing)
- Turso free: "Start free, no credit card required". — [Turso pricing](https://turso.tech/pricing)

### Gaps
- Whether GCP (Cloud Run), Oracle and Modal require a card was not verified this session. GCP and Oracle are known to require one at signup for identity verification. That is from training knowledge, unverified.
- Whether SCC requires a card: none is mentioned in its docs. It needs only a GitHub account.

## Keeping the SQLite DB in sync from the Mac

### Takeaway
The simplest safe pattern is a periodic snapshot upload. The Mac scheduler runs `sqlite3 .backup` (or `VACUUM INTO`) to get a consistent copy, then uploads it to a private R2 or S3 bucket after each run. The cloud app downloads it with a short TTL cache. R2's free tier (10 GB, no egress fees) covers this at no cost. Litestream gives seconds-level staleness and supports S3-compatible targets, but needs a long-running process on the Mac. Turso/libSQL means changing the app's DB layer. Pushing the DB to a private git repo works on SCC but bloats history, triggers a redeploy on every push, and puts the data in GitHub.

### Cited Findings
- Cloudflare R2 free tier: 10 GB-month of storage, 1M Class A (write) and 10M Class B (read) operations per month, no egress charges. — [R2 pricing](https://developers.cloudflare.com/r2/pricing/)
- Litestream continuously streams SQLite changes to cloud storage or local files. The default sync interval is 1s. Replica types: abs, file, gs, nats, oss, s3 (S3-compatible: B2, Tigris, OCI, Filebase, MinIO, DO Spaces), sftp and webdav. It also offers "VFS read replicas". v0.5.x is the current release. — [Litestream](https://litestream.io/); [Litestream config reference](https://litestream.io/reference/config/)
- The Litestream config page does not list R2 by name. R2 is S3-compatible, so the `s3` replica type with a custom endpoint should work, but this is unverified. — [Litestream config reference](https://litestream.io/reference/config/)
- Turso free plan: 100 databases, 5 GB storage, 500M rows read and 10M rows written per month, 3 GB of syncs per month (embedded replicas), no card required. — [Turso pricing](https://turso.tech/pricing)
- HF Spaces can mount a Storage Bucket or a private dataset repo read-only. That is a native way to push the DB to HF and have the Space read it. — [HF Spaces storage](https://huggingface.co/docs/hub/spaces-storage)
- SCC rebuilds from GitHub and rate-limits updates to 5 per minute. — [SCC: Status and limitations](https://docs.streamlit.io/deploy/streamlit-community-cloud/status)

### Inferences
- Trade-offs:
  - Snapshot to R2/S3/GCS: staleness equals the scheduler interval. Costs about $0. Exposure is limited to the bucket, which needs a private bucket and a read-only scoped token in the host's secrets.
  - Litestream: staleness of seconds. Needs a daemon on the Mac and restore or VFS logic in the cloud. Also uses the bucket.
  - Turso: near-real-time. The data lives at a third-party DB vendor, and code changes are needed.
  - Git push: staleness equals commit frequency. Causes a redeploy each time and grows repo history, and anyone with repo access sees the full research data.
  - rsync/scp to an Oracle VM: simple, with staleness equal to the interval. Needs SSH key management and an open port, or Tailscale.
- Never sync the token store or `.env` along with the DB. Ship only the research DB, ideally a filtered export without account identifiers.

### Gaps
- No primary source was checked for whether SCC apps can reliably pull from R2 at runtime. They should, since outbound HTTPS is normal.

## Security risks of putting broker OAuth tokens on a third-party host

### Takeaway
Hosting Robinhood OAuth tokens or Alpaca keys in the cloud adds several exposure paths: the platform's staff and infrastructure, anyone with push access to the deploy repo, and anyone who gets past the viewer gate. With SCC, that includes viewers that other viewers invited. The lowest-risk design is to host only the read-only research pages in the cloud and keep the broker pages local, or reach them over Tailscale. If broker pages must be hosted, use read-only or paper-only keys, an edge auth gate, and short-lived tokens.

### Cited Findings
- SCC viewers "can invite others", so the allowlist is not strictly owner-controlled. — [SCC: Share your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)
- Secrets on every platform reach the app as environment variables or `secrets.toml`, so any code running in the app can read them. — [HF Spaces overview](https://huggingface.co/docs/hub/spaces-overview); [SCC secrets management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- `st.login` provides authentication only, not authorization. — [Streamlit authentication](https://docs.streamlit.io/develop/concepts/connections/authentication)
- SCC hosting is US-only, and its IP addresses can change without notice. Any IP allowlisting on the broker side would therefore be unreliable. — [SCC: Status and limitations](https://docs.streamlit.io/deploy/streamlit-community-cloud/status)

### Inferences
- Robinhood OAuth refresh tokens are usually long-lived and may carry trading scope. If one leaks, the account itself is at risk, not just its data. Alpaca paper keys carry much lower risk than live keys.
- A refresh-token flow writes rotated tokens back to disk. On ephemeral hosts the rotated token is lost on restart, which can break auth or force the original refresh token to be reused. That is a functional reason, besides security, to keep Robinhood pages local.
- This repo's guardrail ("Default all broker interaction to read-only") suggests the cloud deployment should leave out the broker pages through a config flag, instead of relying only on hiding them in the UI.

### Gaps
- Robinhood's terms on using tokens from third-party cloud IPs, and the token lifetime and scope, were not researched in this session.
- No vendor security or trust pages (SOC 2, staff access policies) were fetched for any platform.
