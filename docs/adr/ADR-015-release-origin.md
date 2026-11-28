# ADR-015 — Where a release comes from: a signed `SHA256SUMS`

- **Status:** Accepted
- **Date:** 2026-11-23 (proposed and accepted, with the arbitrations of 2026-11-23, §2)
- **Authors:** Cortex maintainers (initiated by the maintainer, drafted by @Oolon)
- **Affects:** `.github/workflows/release.yml`, `.github/workflows/release-guard.yml`, `cli/build.py`, `cli/cortex_cli/` (`self_update`, a verifier, the release keys), `bin/setup-release.sh`, `README.md`, `docs/getting-started.md`, `SECURITY.md`
- **Relates to:** [ADR-008](ADR-008-cortex-binary.md) §3.3 (the checksums a binary embeds) and §9 (*Integrity is not origin*) · [ADR-009](ADR-009-release-cycle.md) §3.4 (the draft) and §3.5 (the guard) · [ADR-010](ADR-010-lock-and-upgrade.md) §3.7 (`cortex self-update`) and §7.5 · [#275](https://github.com/xod-labs/cortex/issues/275)

## Size

| Criterion | Score | Why |
|---|---|---|
| Phases | S (1) | two phases: sign, then check |
| Public surface | M (2) | a seventh asset, `SHA256SUMS.sig`; the keys a binary embeds; the `release-signing` environment |
| User files Cortex writes | S (1) | none |
| Environment matrix | M (2) | the three systems, GitHub's releases or a mirror, a release candidate |
| Existing ADRs touched | M (2) | ADR-008, ADR-009, ADR-010 |

**8 — M.** It ships in 1.10.0, whose own sheet is L (12 without this record, 14 with it, in the description of its first pull request): the release's budget applies — 4 review passes, 2 counter-review rounds each, 100 commits.

---

## 1. Context

`cortex self-update`, `cortex sync` and the install scripts check a download against the release's `SHA256SUMS`. That file comes from the host that serves the asset: it proves the download whole, not who built it (ADR-008 §9). Today the origin rests on HTTPS to that host — GitHub's releases, or the one `CORTEX_RELEASES_URL` names. Since ADR-010 §3.8, the assistant runs `cortex self-update` on its own when the pin asks for it: a mirror that serves a binary with a `SHA256SUMS` that matches it gets that binary installed.

Reading the code for #275 narrowed the gap:

- **The specs are covered already.** A built binary embeds the checksum of every spec archive released before it (ADR-008 §3.3), and refuses a project pinned past its own version: every spec it downloads is one it knows. A mirror cannot change one.
- **The binary is not.** `cortex self-update` installs a newer binary, which the running one knows nothing of but its `SHA256SUMS`. Once that binary is installed, it is the one that knows the specs: whoever chose it chose everything after.

The release workflow already signs a build-provenance attestation for every asset (ADR-008 §5). Nothing checks them unless someone runs `gh attestation verify`, and they live at GitHub: a machine that reaches only a mirror — the usual reason for one — cannot read them.

## 2. Decision

The arbitrations of 2026-11-23, by the contributor who carries the change:

1. **`SHA256SUMS` is signed** by the release workflow with an Ed25519 key, in a seventh asset, `SHA256SUMS.sig` — option 3 of #275.
2. **The private key is a secret of a GitHub environment**, `release-signing`, open to the branches a release or a pre-release is cut from. Nobody holds a copy of it. The threat is a mirror, not GitHub: whoever can publish a release on GitHub could have it signed, and could publish a binary there anyway. Cortex is used inside one organisation today; a stronger custody — a cloud KMS, or a key held offline — is for when its use spreads.
3. **A backup key is held offline** by the maintainer. A binary embeds both public keys from the first signed release on: losing the primary, or a leak of it, does not strand the binaries in the field.
4. **`cortex self-update` checks the signature** of a release from 1.10.0 on, before it trusts any line of its `SHA256SUMS`; a release before 1.10.0, signed by nobody, is checked against the checksum the binary embeds for it. Wherever the release comes from — GitHub or a mirror.
5. **The install scripts do not change.** They trust the host they are downloaded from, as they already do: whoever serves the script chooses the binary.

## 3. Detailed contract

### 3.1 The keys

- **Ed25519.** Small keys and signatures, deterministic signing, and OpenSSL 3 signs with it in one command.
- **A key's id** is the first 16 hexadecimal digits of the SHA-256 of its 32-byte public key.
- **The public keys are committed**, in `cli/cortex_cli/release_keys.py`: each id with its key, base64, and its role, `primary` or `backup`. A binary embeds the file of the commit it is built from. Tests never use these keys: they are given their own.
- **The private key** is `RELEASE_SIGNING_KEY`, a secret of the `release-signing` environment, in PEM. The environment is open to `main`, `[0-9]*.x` and `release/**` — where a release or a pre-release is cut — and to no other branch. It has no required reviewer: the maintainer already publishes each draft by hand (ADR-009 §3.4).
- **The backup key** is never in the repository's settings. The maintainer keeps it offline, in two places.

### 3.2 The signature

`SHA256SUMS.sig` is text, one line per signature:

```text
ed25519 <key id> <signature, base64>
```

- The signature is over the exact bytes of `SHA256SUMS`, as it is served.
- A release is signed with the primary key; a release that rotates keys (§3.6) is signed with two, one line each.
- A line with another algorithm, or an id the binary does not know, is skipped. The file is valid when one line verifies with a key the binary embeds.

### 3.3 Signing — the release workflow

- The `draft` job runs in the `release-signing` environment. After `SHA256SUMS` is written, it signs it with `openssl pkeyutl -sign -rawin`, writes `SHA256SUMS.sig`, then **checks that signature against the committed public keys** before anything is drafted: a secret that does not match the committed primary key fails the run, before a draft exists.
- The draft's expected assets, and the guard's, become seven.
- `SHA256SUMS.sig` gets a build-provenance attestation like every other asset.

### 3.4 The guard

ADR-009 §3.5 gains an item: `SHA256SUMS.sig` is there, and one of its lines verifies `SHA256SUMS` with a key of `release_keys.py` at the tag's commit. A release published without it is one the binaries of 1.10.0 and later refuse to install: the guard fails, as it does for a missing asset.

### 3.5 Checking — `cortex self-update`

Before it reads a line of `SHA256SUMS`:

| The target | `cortex self-update` |
|---|---|
| 1.10.0 or later, its pre-releases included | downloads `SHA256SUMS.sig` beside `SHA256SUMS`, and installs nothing unless one line verifies with a key it embeds |
| before 1.10.0 | checks the asset against the checksum it embeds for that release and that target; one it does not embed is refused |
| the latest release, by its `latest/download/` redirect | the same, by the version the redirect names |

- **A refusal says what was missing**, and the host it came from: no signature, a signature by no known key, a signature that does not match. Nothing is replaced. There is no option to skip the check: a mirror serves the seven assets, or it does not serve a release.
- **A signed `SHA256SUMS` of another version cannot be replayed** under a newer one: the new binary still runs before it is installed and must answer `--version` with the target (ADR-010 §3.7, item 4).
- **The checksums of earlier releases** are what `cli/build.py` already reads to embed the specs' (ADR-008 §3.3): each release's `SHA256SUMS`, from GitHub, at build time. It keeps every binary archive's line too, not only the spec's.
- **The verifier is pure Python**, the verification of RFC 8032 §5.1.7: the standard library verifies no signature, and `cryptography` would add a native library to each of the four binaries. It is tested against RFC 8032 §7.1's vectors and against signatures that must fail — a changed message, a changed signature, an `S` not below the group order, a point not on the curve.
- **A source checkout** has no binary to replace, and `self-update` refuses it already.

### 3.6 Rotating a key

- **A key lost, or leaked**: the next release is signed with the backup key, and embeds a new pair — the backup becomes primary, and a new backup is made offline. A binary of the field verifies it with the backup it embeds.
- **A key retired** stays embedded until a release has been signed with its successor, then leaves `release_keys.py`; a release of that transition carries both signatures.
- **A leaked key cannot be revoked offline**: the binaries that embed it trust it until they are updated. The note of the release that rotates says so, and asks for an update.

### 3.7 What does not change

- **The specs**: their embedded checksums cover them (§1). `cortex sync`'s note on a host other than GitHub's stays (ADR-010 §9, 2026-11-23).
- **The install scripts**: §2, item 5.
- **`gh attestation verify`** still checks an installed binary against its provenance, by hand.

## 4. Phases

### Phase 1 — Sign

`release_keys.py` with the two public keys; the `draft` job signs and checks its signature; seven expected assets in the draft and the guard, and the guard's check of the signature; `bin/setup-release.sh` creates the environment and says how to set its secret. **Accepted when** a release candidate's run signs, its guard passes, and a run whose secret does not match the committed key fails before its draft.

### Phase 2 — Check

The verifier; `cortex self-update` checks the signature, or the embedded checksum before 1.10.0; `cli/build.py` embeds every archive's checksum; the docs — `README.md`, `docs/getting-started.md`, `SECURITY.md`. **Accepted when** a self-update from a local mirror installs a signed release, and refuses one unsigned, signed by an unknown key, or whose `SHA256SUMS` was changed after it was signed — each refusal naming what was wrong — and a release before 1.10.0 is installed by its embedded checksum, or refused when it does not match.

## 5. Consequences

### Positive

- A mirror can serve a release; it cannot change one. Whoever chose the first binary — the install script, from its host — chose every update after.
- No new dependency in the binary, and nothing for a user to configure.
- The release pipeline stays what ADR-009 made it: a merge drafts, the maintainer publishes.

### Negative

- **A key to keep, and to rotate.** The backup makes a loss survivable; a leak is not revocable offline.
- **A mirror must copy a seventh asset.** One that copies a release's assets whole does.
- **The verification is cryptography written here.** It is RFC 8032's reference algorithm, tested against its vectors and its failures; signing stays OpenSSL's.

## 6. Alternatives considered

- **`gh attestation verify`, when `gh` is installed** (option 1 of #275). Rejected: it protects only the machines that have `gh` and reach GitHub, which a mirror's machines usually do not.
- **The attestations, asked of GitHub's API by the binary.** Rejected for the same reason, and the binary would trust the API's answer without verifying Sigstore's bundle.
- **Sigstore's verification inside the binary** (option 2). Rejected: a large dependency, and a trust root to keep current.
- **The key held offline by the maintainer**, signing each release by hand. Rejected for now: a manual step at each release, and a release left unsigned is one no binary installs. Kept as the way to go if Cortex's use spreads.
- **A cloud KMS.** Rejected for now: an account and an infrastructure to keep, for a threat that is a mirror's.
- **Nothing beyond ADR-010's note** (option 4). Rejected: the assistant runs `self-update` on its own.

## 7. Follow-ups (out of scope for this record)

1. **The install scripts' first download** — checked by a key the script embeds, if a script is ever served from a host that does not also serve the binary.
2. **A stronger custody** — a KMS, or an offline key — when Cortex is used outside its organisation.

## 8. References

- [ADR-008](ADR-008-cortex-binary.md) — §3.3 the store and the embedded checksums, §5 the attestations, §9 *Integrity is not origin*
- [ADR-009](ADR-009-release-cycle.md) — §3.4 the draft, §3.5 the guard
- [ADR-010](ADR-010-lock-and-upgrade.md) — §3.7 `cortex self-update`, §3.8 the assistant runs it, §7.5
- [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032) — Ed25519, §5.1.7 verification, §7.1 test vectors
- [#275](https://github.com/xod-labs/cortex/issues/275)

## 9. Amendments

### 2026-11-28 — A rotation's second signature needs a second secret (#275)

The release workflow of 1.10.0 signs with one key, the primary, from `RELEASE_SIGNING_KEY`. A release that rotates keys and carries two signatures (§3.2, §3.6) needs a second secret in the `release-signing` environment and a second signing step, added by the change that makes the first rotation. Until then, a lost or leaked primary is answered as §3.6's first item says: the backup key becomes the environment's secret, and the release it signs embeds the new pair.
