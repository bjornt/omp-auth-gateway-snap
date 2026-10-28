# omp-auth-gateway snap

A strictly-confined snap that runs the [oh-my-pi (`omp`)](https://github.com/can1357/oh-my-pi)
**auth-gateway** (forward proxy) and **auth-broker** (credential vault) as
auto-starting services. It keeps your LLM provider credentials outside of the
containers where you run `omp` itself — no hand-written systemd units, no
manually exported environment variables.

## What you get

| Service (`snap.omp-auth-gateway.*`) | Command | Default bind |
| --- | --- | --- |
| `gateway-daemon` | `omp auth-gateway serve` | `127.0.0.1:4000` |
| `broker-daemon` | `omp auth-broker serve` | `127.0.0.1:8765` |

Both are enabled and started automatically on install. The gateway is wired to
talk to the broker for you (it reads the broker's bearer token from the shared
vault and sets `OMP_AUTH_BROKER_URL` / `OMP_AUTH_BROKER_TOKEN` itself).

Plus two management wrappers:

- `omp-auth-gateway …` → `omp auth-gateway …` (token, status, check, …)
- `omp-auth-broker …` → `omp auth-broker …` (login, token, status, list, import, migrate, …)

## Install

```sh
sudo snap install omp-auth-gateway
```

## Use

```sh
# Add credentials to the vault the broker serves (root-owned vault → sudo).
sudo omp-auth-gateway.broker login
sudo omp-auth-gateway.broker list

# Restart the gateway to have it refresh its models
sudo snap restart omp-auth-gateway.gateway-daemon

# Inspect state and the gateway bearer token your containers will use.
sudo omp-auth-gateway status
sudo omp-auth-gateway token

# Health
snap services omp-auth-gateway
sudo snap logs omp-auth-gateway -n 50
```

Point a containerised `omp` at the gateway with the gateway base URL and the
bearer token printed by `omp-auth-gateway token`.

Example:

```sh
cat > ~/.omp/agent/models.yml <<EOF
providers:
     github-copilot:
       baseUrl: http://localhost:4000
       apiKey: OMP_GATEWAY_TOKEN
       transport: pi-native
     openrouter:
       baseUrl: http://localhost:4000
       apiKey: OMP_GATEWAY_TOKEN
       transport: pi-native
EOF

cat >> ~/.omp/agent/.env <<EOF
OMP_GATEWAY_TOKEN=$(sudo omp-auth-gateway token)
EOF

```

## Configure

Changing any option restarts the affected service automatically.

```sh
# Bind beyond loopback so a container/host on your tailnet can reach the gateway.
sudo snap set omp-auth-gateway gateway.bind=0.0.0.0:4000
sudo snap set omp-auth-gateway broker.bind=127.0.0.1:8765
sudo snap set omp-auth-gateway gateway.broker-url=http://127.0.0.1:8765
```

## Notes

- **Confinement:** strict, with only the `network` and `network-bind`
  interfaces. State (SQLite vault, bearer tokens) lives in the snap's own
  `$SNAP_COMMON/.omp`, which persists across refreshes. Because that area is
  root-owned, the management commands are run with `sudo`.
- **Security:** every broker/gateway endpoint except the health checks requires
  a bearer token, but transport security between operator, broker, gateway and
  clients is up to you (Tailscale / WireGuard / a TLS reverse proxy).

## Build

The build downloads the `omp` release pinned in `LATEST_OMP_VERSION` for the
target architecture from upstream's GitHub releases. Linux builds are supported
for `amd64` and `arm64`.

```sh
snapcraft                      # build for the host architecture
snapcraft remote-build         # build all declared architectures
```

## Candidate testing and stable promotion

Snaps are built in Launchpad after the GitHub mirror is updated. The
[Launchpad recipe](https://launchpad.net/~bjornt/+snap/omp-auth-gateway-snap-candidate)
publishes builds to `latest/candidate`; promotion to `latest/stable` is gated
by the smoke tests below.

The **Test and promote candidate Snap** workflow polls at 30 minutes past each
hour and can be dispatched manually from the default branch. It:

1. Reads explicit `latest/candidate` revisions for both supported architectures
   from the Snap Store. Missing candidates never fall back to another channel.
   Scheduled runs skip pairs already published to stable; manual runs retest.
2. Installs each exact revision on native Ubuntu 26.04 runners (`ubuntu-26.04`
   and `ubuntu-26.04-arm`), holds automatic refreshes, and downloads the latest
   released upstream `omp` client. The hosted images already include snapd;
   installing the candidate also installs its `core26` base automatically.
3. Runs `scripts/smoke-snap.py` against the installed, strictly confined broker
   and gateway. A loopback OpenAI-compatible mock advertises a Moonshot model
   and returns a deterministic streaming completion. Moonshot's supported
   `MOONSHOT_BASE_URL` override routes the gateway to the mock. The client gets
   only the gateway token; the mock checks the prompt, model and dummy provider
   key retrieved from the broker, and the script checks the exact answer.
4. Only after **both** smoke jobs succeed, rechecks that candidate still points
   to the tested revision pair and releases those exact revisions to
   `latest/stable`. Failed tests or a changed candidate set block promotion.

No LLM credentials, inference snap, GPU or model download is needed. This checks
the client/proxy/credential/streaming path, not real-model inference quality.
The smoke script is for disposable runners with an empty broker vault: it adds
a dummy credential, configures gateway port `14000`, temporarily overrides the
gateway service's upstream endpoint, and stops that service on exit.

Store publishing credentials are exposed only to the final promotion step,
never to the smoke jobs. Stable updates for the two architectures are separate
Store operations, not atomic; if one fails, the next run retests the candidate
pair and retries promotion.
