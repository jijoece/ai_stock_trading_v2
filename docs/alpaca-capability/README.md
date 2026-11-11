# Alpaca Paper Capability Matrix (Phase 1 spike)

**Status:** Evidence for [ADR 0010](../adr/0010-automated-live-trading.md) Section 4A, gathered 2026-11-11. It is
not an acceptance of the ADR, and it is not research output or financial advice. The repository remains paper only.

**What was run.** A throwaway script, `spikes/alpaca_phase1/capability_spike.py`, against the Alpaca **paper**
account using the official `alpaca-py` SDK: `read-only`, `rest` (orders priced far from the market, which cannot
fill) and `fill` (one-share SPY orders, regular hours). Every request and response is stored in
[`fixtures/`](fixtures/) with the account number and account ID redacted. The account ended with no SPY position
and no open order (the pre-existing AAPL share was untouched).

**What this does not show.** Paper trading is not live. Fills here are simulated by Alpaca. Not demonstrated, and
still open: partial parent fills, a gap through a stop, GTC age expiry, split and dividend handling, rate-limit
headers, order-event streams, and whether a GTC stop survives past a close (the overnight check needs a resting
stop observed after one session end).

## ADR Section 4A assumptions

| ID | Assumption | Result | Evidence |
|---|---|---|---|
| A2 | Official SDK exposes parent and child IDs and leg states; polling is enough | **Confirmed** for polling. The SDK returns parent, legs, `replaces`, `replaced_by`, `status` and `filled_avg_price` on a nested read. Streams not tested. | `rest_02`, `fill_02`, `fill_07` |
| A3 | One time in force for all legs of a bracket or OTO | **Confirmed.** Each leg takes the parent's time in force (DAY parent: DAY stop; GTC parent: GTC stop). The SDK's stop-loss request has no time-in-force field, so a DAY entry cannot carry a GTC stop. The GTC-entry-plus-cancel-at-validity design stands. GTC stop surviving a close is **not yet shown**. | `rest_01`, `rest_08`, `fill_01` |
| A4 | Shares covered by a resting stop cannot be sold by a second order | **Confirmed.** A competing 1-share sell was refused: code 40310000, "insufficient qty available (requested 1, available 0)", `held_for_orders: 1`, with the stop in `related_orders`. Option 1 (turn the stop into the exit in one replace) is **not possible**: a replace keeps the order's side and type. Option 2 worked: cancel the stop, sell, fill confirmed. | `fill_04`, `fill_08` to `fill_10` |
| A9 | Shorting without a position is blocked | **Revised.** Shorting is enabled on this account (`shorting_enabled: true`, `no_shorting: false`). A sell with no position is not refused as such; it was refused earlier only because a buy order was open ("cannot open a short sell while a long buy order is open"). The system must therefore never rely on the broker to stop an unintended short. A stale sell can open one. | `read_only_01`, `rest_18` |

## Behaviour the order manager must handle (new findings)

1. **Replace creates a new order.** Replacing a parent, a held leg or a resting stop returns a new order ID with
   `replaces` set; the old order becomes `replaced` and carries `replaced_by`. Cancelling or replacing the old ID
   fails ("order is already in replaced state", "order already replaced"). The manager must follow `replaced_by`
   and store the live ID. The replacement's client order ID is auto-generated, so a client-ID prefix cannot find it.
2. **Cancelling the parent cancels its held legs.** `rest_06`.
3. **Duplicate client order ID is refused** (422, "client_order_id must be unique") and lookup by client order ID
   works. This supports "reconcile first, never resubmit after an unknown response".
4. **Invalid stop is refused before acceptance** (422, "stop_price must be <= base_price - 0.01"). `rest_14`.
5. **Held legs.** Legs are `held` until the parent fills. In an OTO the stop leg becomes `new` (active) at the
   fill. In a bracket after the fill, the take-profit leg is `new` but the **stop leg still reports `held`**
   (`fill_12`, `fill_13`). Whether that stop protects the position while it reads `held` was **not established**.
   Until it is, a bracket cannot be treated as a verified stop. The OTO form, whose stop becomes `new`, is the
   candidate for the protective entry.
6. **Bracket legs are linked.** Cancelling the take-profit leg also cancelled the sibling stop.
7. **A resting take-profit blocks a flatten.** The spike's own flatten failed for the same reason as A4 until the
   legs were cancelled. The corrective-exit design must cancel legs first.
8. **Open-order queries do not list a `held` leg as its own order.** Reconciliation must read legs through
   the parent (nested read), not from a flat open-orders list.
9. **Marketable limit orders filled immediately** at the touch in regular hours (entry 762.01, exit 762.00).
10. **Two same-day round trips raised no day-trade restriction.** The account reports `pdt_check: null`,
    `daytrade_count: null`; settlement and PDT rules for a real account are **not** tested (O11 stays open).

## Spike defects (throwaway code, recorded so nobody trusts the omitted step)

- The stop **loosen** step was not demonstrated: it reused a superseded order ID (finding 1). Tighten worked
  (`fill_05`).
- The bracket flatten step failed on finding 7 and was completed by hand (cancel legs, market sell, verified flat).
- The first `rest` run left one replacement order open because cleanup matched only the client-ID prefix. Fixed in
  the script; the order was cancelled.

## Consequences for the plan

- A2, A3 and A4 hold as written, with A4 option 2 as the only exit path.
- A9 is revised in the ADR: the guard against an unintended short is the platform's job, not the broker's.
- Finding 5 is a **blocker for Phase 5** (stop verification): a design that treats a `held` stop as protective is
  unsafe. Resolve it on paper with a GTC OTO position observed across a session close before relying on brackets.
- The open items above stay on the Phase 1 exit list. They are not decided by this spike.
