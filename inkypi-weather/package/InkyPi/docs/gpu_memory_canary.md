# Zero 2 W GPU-memory tryboot canary

This capability runs a one-boot `gpu_mem=32` experiment on a Raspberry Pi
Zero 2 W. It does not edit `config.txt`, provide a permanent commit action, or
accept caller-selected memory values, paths, or reboot arguments.

## Stage 1: deploy the capability

Publish the capability release through the existing verified transactional
updater. Verify that the installed updater matches the committed `current`
release before using the canary commands. Do not combine the capability deploy
and the experiment in one update operation.

The older updater already manages both `install/inkypi-update` and
`install/lib/host_migration.py`, so the capability does not expand the sudo
surface or depend on a newly managed helper.

## Fixed operator commands

```sh
sudo /usr/local/sbin/inkypi-update --gpu-memory-canary status
sudo /usr/local/sbin/inkypi-update --gpu-memory-canary start
sudo /usr/local/sbin/inkypi-update --gpu-memory-canary rollback
```

`start` creates a release-, updater-, and original-config-bound `tryboot.txt`.
Its fixed body includes the normal `config.txt`, then overrides only
`gpu_mem=32`. It invokes exactly `reboot '0 tryboot'` after the ownership state
and file are durable.

Tryboot is one-shot. The firmware clears the flag before starting the trial, so
a crash or reset loads the original `config.txt` on the following boot. A
`testing` status means the current boot has the tryboot flag. An
`auto_rolled_back` status means a later normal boot was observed without it.
Neither result permanently changes GPU memory.

This behavior follows Raspberry Pi's documented
[fail-safe tryboot flow](https://www.raspberrypi.com/documentation/computers/config_txt.html#fail-safe-os-updates-tryboot).

## A/B acceptance

1. Record the normal-boot service, image, available-memory, swap, temperature,
   and display baseline.
2. Run `start` once and wait for the host to return.
3. Require `status` to report `testing`; verify the web service, a valid current
   image, Waveshare display output, and the same runtime health checks.
4. Compare memory headroom and refresh behavior with the recorded baseline.
5. End the experiment with `rollback`, even when the trial passes. This release
   intentionally has no permanent-commit command.

Any managed canary state blocks a normal release update, including malformed
state. Finish rollback before deploying another release. Do not manually edit
or remove the state or `tryboot.txt`: foreign, symlinked, or hash-mismatched
files are deliberately refused.

## Emergency rollback

Run the fixed rollback command. It verifies the current committed identity,
the original config hash, and exact tryboot ownership before removing anything,
then requests a normal reboot:

```sh
sudo /usr/local/sbin/inkypi-update --gpu-memory-canary rollback
```

After the host returns, check status and run the same rollback command once more
to finalize the durable reboot record. Status reports
`rollback_complete_pending_finalize` until that second command removes the
record. The final result must be `inactive` before a normal release update is
attempted.
