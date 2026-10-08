"""Bounded observe/decide/approve/act loop; the model never certifies success."""

import asyncio
import time

from sentinel_agent.computer_use.schema import (
    BrowserAction, BrowserFailure, BrowserResult, BrowserTask, StaleObservation,
)


def goal_matches(task, snapshot):
    normalize = lambda value: " ".join(value.split()).casefold()
    return (normalize(task.expect_text) in normalize(snapshot.text)
            and (task.expect_url is None or snapshot.url == task.expect_url))


class AdaptiveBrowserAgent:
    def __init__(self, planner, session, approve=None):
        self.planner, self.session, self.approve = planner, session, approve

    async def run(self, task: BrowserTask, cancel=None):
        trace, steps, recoveries = [], 0, 0
        started = time.monotonic()

        def result(status, reason):
            # Keep the trace reference so cleanup evidence appended in finally
            # is included in the result returned to the caller.
            return BrowserResult(status=status, reason=reason, steps=steps).model_copy(update={"trace": trace})

        def cancelled():
            return cancel is not None and cancel.is_set()

        async def bounded(awaitable):
            """Cancel in-flight planning or IO on either deadline or stop request."""
            remaining = task.max_seconds - (time.monotonic() - started)
            if remaining <= 0:
                if hasattr(awaitable, "close"):
                    awaitable.close()
                elif hasattr(awaitable, "cancel"):
                    awaitable.cancel()
                raise TimeoutError()
            operation = asyncio.ensure_future(awaitable)
            stop = asyncio.create_task(cancel.wait()) if cancel is not None else None
            try:
                watched = {operation} | ({stop} if stop is not None else set())
                done, _ = await asyncio.wait(watched, timeout=max(0, remaining), return_when=asyncio.FIRST_COMPLETED)
                if cancelled():
                    raise asyncio.CancelledError()
                if operation not in done:
                    raise TimeoutError()
                return await operation
            finally:
                for pending in (operation, stop):
                    if pending is not None and not pending.done():
                        pending.cancel()
                await asyncio.gather(*(p for p in (operation, stop) if p is not None), return_exceptions=True)

        try:
            if cancelled():
                return result("cancelled", "cancelled")
            await bounded(self.session.start(task.url))
            while True:
                if cancelled():
                    return result("cancelled", "cancelled")
                if time.monotonic() - started >= task.max_seconds:
                    return result("failed", "time-limit")
                try:
                    snapshot = await bounded(self.session.observe())
                    trace.append({"kind": "observe", "snapshot_id": snapshot.id, "elements": len(snapshot.elements)})
                    if goal_matches(task, snapshot):
                        trace.append({"kind": "verify", "matched": True})
                        return result("completed", "goal-verified")
                    if steps >= task.max_steps:
                        return result("failed", "step-limit")
                    try:
                        proposal = await bounded(self.planner.next_action(task.goal, snapshot, trace[-8:]))
                    except TimeoutError:
                        raise
                    except Exception:
                        return result("failed", "planner-error")
                    try:
                        action = BrowserAction.model_validate(proposal)
                        if action.snapshot_id != snapshot.id:
                            raise ValueError("Old snapshot")
                        targets = {e.id: e for e in snapshot.elements}
                        if action.kind in {"click", "fill"} and action.element_id not in targets:
                            raise ValueError("Unknown target")
                        if action.kind == "fill" and not targets[action.element_id].editable:
                            raise ValueError("Not editable")
                    except (ValueError, TypeError):
                        return result("failed", "invalid-action")
                    trace.append({"kind": "decision", "action": action.kind, "snapshot_id": snapshot.id})
                    if action.kind == "finish":
                        return result("failed", "unverified-finish")
                    validator = getattr(self.session, "validate", None)
                    if validator:
                        await bounded(validator(action))
                    if action.kind in {"click", "fill"}:
                        approved = self.approve is not None and await bounded(self.approve(action, snapshot))
                        trace.append({"kind": "approval", "action": action.kind, "approved": bool(approved)})
                        if not approved:
                            return result("failed", "approval-denied")
                    if cancelled():
                        return result("cancelled", "cancelled")
                    # Only StaleObservation certifies that nothing was dispatched.
                    # Other failures are terminal; never blindly retry an effect.
                    await bounded(self.session.act(action))
                    steps += 1
                    trace.append({"kind": "action", "action": action.kind, "step": steps})
                except StaleObservation:
                    recoveries += 1
                    trace.append({"kind": "recovery", "reason": "stale-observation", "attempt": recoveries})
                    if recoveries > task.max_recoveries:
                        return result("failed", "stale-limit")
        except TimeoutError:
            return result("failed", "time-limit")
        except asyncio.CancelledError:
            return result("cancelled", "cancelled")
        except BrowserFailure:
            return result("failed", "browser-error")
        except Exception:
            return result("failed", "runtime-error")
        finally:
            # Cleanup also runs when a model, approval or browser operation fails.
            try:
                await asyncio.wait_for(self.session.close(), timeout=10)
            except Exception:
                trace.append({"kind": "cleanup", "reason": "close-error"})
