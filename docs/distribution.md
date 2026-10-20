# Tester distribution

Version 0.4 is packaged as a local desktop app plus the read-only MotiveWave Journal study. Journal
data never leaves the tester's computer. The app binds its private web backend to `127.0.0.1` and
contains no screen, video or audio recorder.

## What a tester installs

1. Install and open **MotiveWave Journal**.
2. Open **Setup** and click **Install MotiveWave study**.
3. Restart MotiveWave.
4. Add **Journal → MotiveWave Journal** to every exact-contract chart that will be traded.
5. Leave the study's file setting unchanged. The app and study use the same per-user folder by
   default.

The study is read-only. It observes retained account fills, linked stop/target orders, chart trade
prints, one-minute history and replay events. It cannot place, change or cancel orders.

## Trade grouping and partials

One trade is one position from flat back to flat for the same provider, account, exact contract and,
when MotiveWave supplies it, native position ID.

- Scale-ins remain in the open trade.
- Partial exits remain in the open trade.
- The fill that returns the position to zero closes the trade.
- A reversal closes the first trade at the crossing fill and opens a second trade for the remainder.
- Accounts, contracts and simultaneous native position IDs remain separate.

The journal retains every fill inside the trade, so average entry, average exit, fees, maximum size,
realized P&L and the chart's entry-to-exit markers account for partials. If MotiveWave exposes only a
partial retained history, the journal marks the trade incomplete instead of inventing the missing
side.

## Tick path and replay

The study records the chart's observed trade prints while its capture gate is open, including a
bounded pre-roll and exit tail. The resulting path drives observed-tick MAE/MFE and the one-minute
candles drawn from those prints. Put the study on the exact contract chart; another contract's chart
cannot supply its ticks.

Since version 0.3.2 the study defaults to **Always While Study Runs**, retaining every trade print
MotiveWave sends to that chart; version 0.3.3 is the first that MotiveWave actually sends them to, because
it requires bar updates. This supports 5/15/30-minute runner comparisons by individual tag, tag category or
setup. The lower-storage position-gated mode remains available when its After Exit window is long
enough for the selected comparison horizon.

Replay records the events the replay actually emits. Its own prints are tick-accurate only when
MotiveWave replay is running from real tick data; bar-generated replay remains labelled
simulated/source-unverified. Once a replay trade is flat, study 0.3.3 also loads the real ticks of its
holding time from MotiveWave's tick history for that replay run, never past the replay's current time.

## macOS release

The release DMG contains a self-contained `.app`, the study JAR and an Applications shortcut. An
unsigned/ad-hoc build is suitable for known testers but macOS Gatekeeper may require a control-click
and **Open**. Public friction-free distribution requires an Apple Developer ID signature and
notarization.

## Windows release

The Windows ZIP contains the complete portable folder. Extract the whole folder and run
`MotiveWave Journal.exe`; do not move the EXE away from its `_internal` folder. Current Windows 10/11
normally includes the Edge WebView2 runtime used for the native window.

PyInstaller cannot cross-compile, so the Windows artifact is built and smoke-tested on Windows. See
`desktop/windows/BUILD.md` for the release command.

## Acceptance test before sharing

Use a simulation account, not a live account:

1. Confirm Setup says **Connected** with a fresh study version.
2. Buy two contracts, exit one, add one, then flatten two.
3. Confirm exactly one completed trade appears with four fills and the correct maximum quantity.
4. Open the trade and confirm observed ticks exist from entry through exit.
5. Create `Orderflow` and `Psychology` categories, add tags, restart the app and confirm they persist.
6. Repeat in Replay and confirm it appears only in the Replay journal.
7. Quit the app and confirm MotiveWave remains running.

This acceptance test validates the installed platform/version and data path. Synthetic tests cannot
prove what a specific broker connection retains or what replay source MotiveWave emits.
