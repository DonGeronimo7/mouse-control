# Unknown Mouse Hardware Acceptance

This procedure validates automatic discovery on a mouse for which mouse-control
has no trusted model-specific backend.

The acceptance goal is **not** to make the device writable immediately. The
first goal is to learn the hardware in its untouched native state, classify any
known protocol-family grammar, correlate physical behavior with raw HID state,
and refuse to invent semantics that were not observed or proven.

## Non-negotiable discovery rules

1. Start with the mouse in its normal firmware/native/onboard state.
2. Stop the normal mouse-control runtime before the acceptance run.
3. Do not enter Host/software-control mode before or during discovery.
4. Full-evidence acceptance runs use root hardware visibility so permission
   differences between composite hidraw siblings cannot hide protocol evidence.
5. Root visibility does **not** relax protocol safety. Unknown HID remains
   read-only. No speculative SET_FEATURE, SET_REPORT, Output report or raw HID
   write is allowed.
6. In `--full-access` mode, every hidraw sibling correlated to the selected
   physical mouse must pass a passive-open preflight. A still-unreadable sibling
   is an incomplete acquisition and the test must fail rather than silently
   downgrade its evidence set.
7. VID/PID and descriptor resemblance are hints only. They never authorize a
   protocol-family write by themselves.
8. A momentary button trigger and a persistent hardware state are different
   semantic facts and must be learned separately.
9. Privileged discovery profiles are persisted back to the invoking user's
   mouse-control data directory rather than `/root`.

## Preparation

```fish
cd ~/Mouse-control
git switch feat/automatic-hardware-discovery
git pull --ff-only
mouse-control stop
```

Plug in the unknown mouse and do not run vendor configuration software or change
its operating mode.

## Primary acceptance command

For a source checkout during development:

```fish
sudo env PYTHONPATH="$PWD/src" python -m mouse_control.discovery_cli \
    --full-access \
    --generic-only \
    --learn-dpi-button \
    --verbose
```

For an installed v1 package the equivalent command is:

```fish
sudo mouse-control-discover \
    --full-access \
    --generic-only \
    --learn-dpi-button \
    --verbose
```

`--full-access` requires root and verifies passive read access to every hidraw
sibling already correlated to the selected physical mouse. It does not authorize
unknown writes.

`--generic-only` deliberately disables known protocol detectors. The command
first prints the untouched topology/descriptor/repertoire discovery result and
only then starts the guided action phase.

When prompted three times, press the physical DPI button exactly once during
each capture window.

Do **not** add `--teacher` for genuinely unknown hardware. Teacher mode is only
for mastered devices such as the G305 where a proven backend can supply semantic
ground truth without exposing its packet implementation to the learner.

## Acceptance levels

### A. Physical identity — required

Pass when mouse-control:

- finds the selected evdev device;
- correlates its relevant hidraw siblings into one physical device;
- reports ambiguity instead of guessing if more than one physical device fits;
- computes a path-independent model fingerprint;
- does not persist `/dev/hidrawN` or `/dev/input/eventN` as identity.

### B. HID grammar and acquisition completeness — required

Pass when mouse-control:

- parses all HID report descriptors associated with the selected physical mouse;
- inventories Input/Output/Feature reports and vendor-defined usage pages;
- retains composite sibling interfaces even when only one carries ordinary
  mouse motion;
- reports `N/N correlated hidraw sibling(s) readable` in full-access mode;
- fails rather than pretending the acquisition is complete if a correlated HID
  sibling still cannot be opened even with root visibility.

### C. Protocol-family repertoire — evidence dependent

A strong result may identify one or more structural family candidates. A match
is useful evidence, not write permission.

Pass when:

- a structurally compatible known family can be proposed from report grammar;
- VID/PID alone cannot manufacture a family match;
- descriptor similarity alone cannot authorize writes;
- an unknown grammar remains explicitly unknown instead of being forced into
  the closest known family.

### D. Guided DPI-button learning — primary behavioral test

The learner has two independent channels:

- **persistent raw-state candidates** — fields whose final state changes across
  repeated actions, useful for DPI stage/value state;
- **momentary raw-trigger candidates** — fields that transition inside a sample
  and return to rest, useful for DPI-cycle button press/release behavior.

Pass when at least the behavior actually exposed by the hardware is reported.
A mouse may expose both, one, or neither through host-visible HID.

A momentary trigger must never be mislabelled as a persistent DPI stage merely
because the guided action was a DPI-button press.

### E. Semantic restraint — required

Pass when mouse-control does **not** claim unsupported facts.

Examples:

- seeing a four-state field does not by itself prove the numeric DPI values;
- seeing a 125 Hz event stream does not prove a writable polling command;
- seeing vendor-defined Feature reports does not prove a known protocol;
- a family resemblance does not prove persistence behavior or a safe write;
- no battery capability is invented for a wired device that exposes none.

### F. Write safety — required

For a genuinely unknown mouse, the first acceptance run should normally finish
with no generic writable capability.

A future write can only be promoted after stronger evidence such as:

- an already-proven backend;
- an exact hardware-verified repertoire rule;
- a proven protocol-family handshake whose write scope allows transfer;
- controlled readback and behavioral verification;
- explicit control-ownership transition when the operation requires Host mode.

Host/software-control takeover is **post-discovery only**.

## Interpreting a successful unknown-device result

A result like the following is valuable even without writable DPI:

```text
Physical binding: unambiguous
Full-access acquisition: 3/3 correlated hidraw siblings readable
Protocol: unknown / not yet validated
Descriptor grammar: parsed
Vendor-defined reports: observed
Momentary raw-trigger candidates: 1
Persistent raw-state candidates: 0
Semantic hypothesis: dpi_cycle_trigger (correlated)
Validated write semantics: none
```

That means mouse-control acquired the complete host-visible evidence set,
correctly learned what the device actually exposed and refused to invent what it
did not expose.

A stronger result may additionally identify a reusable ODM/protocol family or a
persistent stage field. Those observations can be validated in later phases.

## Post-discovery control phase

Only after native discovery is complete may a device be considered for:

- safe active queries;
- reversible configuration writes;
- persistent/onboard writes;
- Host/software-control takeover.

If takeover changes native behavior, that side effect must be represented in the
protocol grammar and presented as an explicit control decision rather than an
implicit side effect of discovery, startup, reconnect or a read operation.

## Cleanup

After an acceptance session on an already-configured daily-use mouse:

```fish
mouse-control restart
```

For a brand-new unknown test mouse, leave the runtime stopped until the captured
evidence has been reviewed if doing so avoids applying an unrelated saved
configuration.
