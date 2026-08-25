# Operator authorization

Operator authorization protects sensitive control-plane changes in Settings. It is separate from access to the ordinary Web UI: browsing agents, terminals, docs, and statistics does not require the operator secret.

This feature is not remote-user authentication. Keep ThreadCells loopback-only and follow [Remote access](REMOTE_ACCESS.md) when another machine needs access.

## How it works

ThreadCells stores a verifier derived from the secret, never the plaintext secret. The server loads that verifier at startup. Entering the correct secret creates a short-lived, secure operator session; protected mutations remain locked after it expires.

```text
Verifier configured
      ↓
Settings shows Locked
      ↓ enter operator secret
Unlock operator changes
      ↓
Short-lived authenticated session
      ↓ expires
Locked again
```

The minimum operator secret length is exactly **5 characters**. Four characters are rejected. A longer, randomly generated secret is strongly recommended.

## Create a verifier

Run the standalone command as an administrative user from any readable working directory:

```bash
threadcells operator create-verifier --output /etc/threadcells/operator-verifier.json
```

The command prompts without echoing the secret and writes only the salted KDF verifier. Protect the containing directory from modification by the ThreadCells service account while allowing that account to read the file. One suitable layout is:

```bash
sudo chown root:threadcells /etc/threadcells
sudo chmod 0750 /etc/threadcells
sudo chown root:threadcells /etc/threadcells/operator-verifier.json
sudo chmod 0640 /etc/threadcells/operator-verifier.json
```

Adapt the group name to the service account used by your installation. Every parent directory in the path must also be trustworthy: ThreadCells rejects a verifier reached through a service-owned or group/world-writable directory.

Do not put the secret or verifier JSON in the repository, database, logs, browser storage, telemetry, or an API request outside the unlock operation.

## Configure the server

Set the absolute verifier reference in the server environment:

```bash
THREADCELLS_OPERATOR_VERIFIER_FILE=/etc/threadcells/operator-verifier.json
```

Restart only the ThreadCells server and inspect Settings → General → Operator authorization. The state should be **Configured · Locked**, not **Not configured** or **Configuration invalid**.

The session endpoint reports only safe state:

```bash
curl -s http://127.0.0.1:9889/operator/session
```

Expected result includes `"configured": true` and `"authenticated": false` before unlock. It never returns the verifier path, salt, hash, or secret.

## Unlock protected changes

In Settings, enter the secret and choose **Unlock operator changes**. The default authenticated window is five minutes. The UI shows the expiration and returns to locked when the session ends.

Protected settings calls fail while locked and succeed during the authenticated session. The browser uses the server's short-lived secure session cookie; it does not persist the operator secret.

Full Cleanup reuses this exact authority. Preview remains available as a read-only safety inspection, while execution requires the current operator session plus the standard permanent-action confirmation. The confirmation never asks for the secret again. No separate cleanup secret, URL credential, browser-storage value, or durable plaintext copy exists; expiry, relock, and rate limits are unchanged.

## Replace the secret

Create a new verifier at a temporary administrative path, validate its ownership and permissions, then atomically replace the configured file and restart ThreadCells. Existing operator sessions should be treated as invalid after replacement.

The current Web UI intentionally does not offer an unauthenticated remote reset or a Settings-based verifier writer. CLI provisioning keeps the verifier under operating-system ownership and avoids creating a broader security subsystem.

## Owner XHigh launch

The builtin `critical_sol_xhigh_owner` profile is available through **Create Session & Spawn Agent**, **Add Agent** for an existing session, and the local CLI. Both Web flows show the same exceptional-authority warning, require explicit confirmation and an unlocked operator session, mint a short-lived revision/scope-bound one-use capability, and consume it through the normal launch path. Add Agent binds the capability to the existing session and its canonically resolved working directory; the operator cannot type an arbitrary replacement path.

The local CLI path requires `--owner-xhigh` and an explicit interactive confirmation. It mints and consumes the same class of one-use capability over loopback. There is no reusable bypass/header shortcut: an absent checkbox/confirmation, missing or wrong operator secret, mismatched scope, or reused grant fails closed. The authenticated Web client receives the opaque capability once solely to perform the matching launch; the operator secret is never returned. Neither value is copied into agent/session metadata, provider prompts, terminal transcripts, logs, or browser storage. These launch paths do not authorize children or weaken protected Settings mutations.

## Troubleshooting

- **Not configured:** the environment variable is absent or empty. Confirm it reaches the actual server process, then restart.
- **Configuration invalid:** inspect server logs for the safe validation reason. Check JSON schema, absolute path, readability, owner, mode, and every parent directory. Do not recreate a valid verifier merely to hide a path or ownership problem.
- **Correct secret rejected:** ensure the generator and server use the same verifier file and that no old server process is still running.
- **Unlock succeeds then immediately locks:** confirm browser cookies are accepted and the system clock is correct.
- **Unlock works locally but protected changes fail through an HTTPS proxy:** set `THREADCELLS_TRUSTED_PROXY_ORIGINS` to the exact public HTTPS origin (for example `https://threadcells.example.com`) in the ThreadCells service environment, then restart. Do not add paths, wildcards, or unauthenticated origins.
- **Verifier creation fails in an unrelated directory:** use a current ThreadCells build. The standalone command must not inspect a working-directory `.env`.

See [Security model](SECURITY_MODEL.md) for the surrounding trust assumptions.
