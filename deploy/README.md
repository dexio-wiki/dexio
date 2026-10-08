# Running your own Dexio

The server is one container plus Caddy for automatic TLS, so anything that runs
Docker and has a DNS name works. This is the same compose file the hosted service
at https://app.dexio.wiki runs.

## On a host you already have

```bash
cd deploy
cp .env.example .env      # set DEXIO_DOMAIN at least
docker compose --env-file .env up -d --build
```

To skip the build, use the published image (amd64 and arm64, rebuilt on every
change to `main`): set `DEXIO_IMAGE=ghcr.io/dexio-wiki/dexio:latest` in `.env` and
run `docker compose --env-file .env up -d` without `--build`.

DNS must resolve to the host before you bring the stack up. Caddy completes an
ACME HTTP-01 challenge on first start (ports 80 and 443 open), and without a
working A record it sits in a retry loop and never serves HTTPS.

Then open `https://$DEXIO_DOMAIN`, sign up, and connect an agent from the Connect
page. Agents reach MCP at `https://$DEXIO_DOMAIN/mcp`, with an API key from the
Connect page or through OAuth (Claude and ChatGPT connectors). An API key covers
every wiki in its workspace.

## Settings

Every setting lives in `deploy/.env`; `.env.example` lists them with notes.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEXIO_DOMAIN` | required | DNS name of this host; Caddy gets its certificate for it |
| `DEXIO_PUBLIC_URL` | `https://$DEXIO_DOMAIN` | the address OAuth, MCP and email links are built on |
| `DEXIO_ADMIN_EMAIL`, `DEXIO_ADMIN_PASSWORD` | unset | seed the first account instead of signing up |
| `DEXIO_FILES_SSM` | `/dexio/files-bucket` | SSM parameter naming the S3 bucket for files; set it empty, as `.env.example` does, to keep files on disk |
| `DEXIO_FILES_BUCKET` | unset | an S3 bucket for files, named directly |
| `DEXIO_DATABASE_URL` | unset | Postgres; unset uses SQLite under the data directory |
| `DEXIO_MAIL` | unset | `ses` sends email through Amazon SES; unset logs it instead |
| `DEXIO_MAIL_FROM`, `DEXIO_MAIL_REPLY_TO`, `DEXIO_CONTACT_TO` | Dexio's addresses | set these to your own when mail is on |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | unset | Sign in with Google; callback `/auth/google/callback` |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | unset | Sign in with GitHub; callback `/auth/github/callback` |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | unset | plans sold through Stripe; unset, the server sells no plans and its workspaces have no member or storage limits |
| `DEXIO_DATA_DIR` | `./data` | where the database, files and certificates live on the host |

Without mail, people sign up and sign in with a password; sign-in links and
invitations by email need SES.

## On a fresh EC2 instance

`ec2-user-data.sh` does the whole setup on first boot of an Amazon Linux 2023
instance: installs Docker and the compose plugin, clones this repository to
`/opt/dexio/src`, writes `.env`, brings the stack up, and installs a systemd unit
so it survives a reboot.

```bash
aws ec2 run-instances \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --instance-type t4g.small \
  --user-data file://ec2-user-data.sh \
  ...
```

You still create the instance, security group (80 and 443 inbound) and DNS record
yourself. Set `DEXIO_DOMAIN` (and optionally `DEXIO_ADMIN_EMAIL` and
`DEXIO_ADMIN_PASSWORD`) in the environment before launching.

## Backups

Everything is under `DEXIO_DATA_DIR`: `dexio/dexio.db` (SQLite) and `dexio/files/`,
or your Postgres database and bucket. Back those up. Each wiki can also be
downloaded as a zip of markdown files from Settings or `GET /api/v1/export`.

## Access log

Caddy writes an access log to `caddy/logs/access.log` under `DEXIO_DATA_DIR` and keeps 30
days of it. It logs signing up, signing in and connecting agents; wiki pages and the API
calls that read them are left out. Caddy drops sign-in codes and page paths from the
addresses it logs and redacts cookies and keys. To turn the log off, remove the `log_skip`
line and the `log` block from the `Caddyfile`.

## Files

| File | Purpose |
| --- | --- |
| `Dockerfile` | Server image, runs non-root, has a healthcheck |
| `docker-compose.yml` | The server plus Caddy for automatic TLS |
| `Caddyfile` | Reverse proxy and certificate config |
| `ec2-user-data.sh` | First-boot provisioning for a fresh instance |
