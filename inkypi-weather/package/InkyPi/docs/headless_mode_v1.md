# `headless_mode_v1` transactional host migration

`headless_mode_v1` deliberately ships in two releases. The capability release
must be installed first without a migration request; it upgrades the updater
while leaving the systemd default target, LightDM, and `CPUQuota=100%`
unchanged. A later activation release may opt in with the exact allowlisted
identifier `headless_mode_v1`.

## Two-stage operation

1. Install and verify the capability release normally:

   ```bash
   sudo bash install/update.sh --release-id <capability-release-id>
   ```

2. Only after the capability updater is active, build and install a distinct
   activation release:

   ```bash
   sudo bash install/update.sh \
     --release-id <activation-release-id> \
     --migration headless_mode_v1
   ```

The target release's preflight requires an explicit updater capability
handshake. It is issued only by the fixed installed updater when its bytes
match the updater in the current release and that release has a durable
committed journal. A source-tree fallback, missing/non-executable installed
updater, or forged request therefore fails before host state changes.

Before accepting a headless candidate, the committed capability updater
atomically installs and enables two root-owned oneshots plus fixed systemd
drop-ins. Recovery runs before LightDM and InkyPi; both services have a
fail-closed `Requires` dependency on it. Finalization runs after deferred
service jobs and archives recovery only after exact state verification.
Capability files, `daemon-reload`, enablement, and the nonblocking recovery
start are serialized by the normal update lock. The updater then releases the
lock, waits for recover-only to acquire that same lock and finish, and
reacquires it to verify the current release and durable attestation before it
can create a candidate journal.

## Trusted mutation and recovery

Before the first host mutation, the updater durably records:

- the current default target (`graphical.target` or `multi-user.target`);
- whether `lightdm.service` is `enabled` or `disabled`;
- whether `lightdm.service` is `active` or `inactive`.

It then records `switched`, stops `inkypi.service`, enters the durable
`applying_host_migration` phase, and runs only these fixed commands through
`/usr/bin/systemctl`:

```text
set-default multi-user.target
disable lightdm.service
stop lightdm.service
```

Release metadata cannot provide executable names, subcommands, unit names, or
arguments. The migration never calls `systemctl isolate`.

Only after those commands succeed does the updater switch the release pointer,
install managed files, enter `starting`, start InkyPi, and require the new
release to pass `readyz`. Thus the service never performs a normal refresh
while LightDM is being stopped, and readiness is measured in the final
headless host state.

If an action, release health check, journal write, or process fails before the
release commits, rollback reapplies the exact captured target and LightDM
enabled/active states and restores the prior InkyPi release. Recovery after a
power loss treats `switched`, `applying_host_migration`, `starting`, and even
`healthy` as incomplete until `committed` is durable. It first restores host
state, pointers, managed files, and enable state, then records
`rollback_pending_services` before queuing active services without blocking
systemd ordering. The finalizer verifies target, LightDM, and InkyPi state
before recording `rolled_back`. Only `committed` preserves headless mode.

This capability does not raise the InkyPi service CPU quota. A separate,
measured rollout must change `CPUQuota` only after headless activation has been
verified.
