"""Bounded desktop observe/decide/approve/act loop with independent verification."""

import asyncio
import time

from sentinel_agent.computer_use.desktop_schema import (
    DesktopAction, DesktopFailure, DesktopResult, DesktopSnapshot, DesktopTask,
    StaleDesktopObservation, validate_desktop_action,
)


def desktop_goal_matches(task: DesktopTask, snapshot: DesktopSnapshot) -> bool:
    """Only document/content text can certify the optional text postcondition.

    A title or button named after the requested result is not evidence that the
    requested work exists. Visual-only goals require human review for now.
    """
    normalize = lambda value: " ".join(value.split()).casefold()
    return task.expect_text is not None and normalize(task.expect_text) in normalize(snapshot.text)


class AdaptiveDesktopAgent:
    def __init__(self, planner, session, approve=None):
        self.planner, self.session, self.approve = planner, session, approve

    async def run(self, task: DesktopTask, cancel=None) -> DesktopResult:
        trace, steps, recoveries = [], 0, 0
        started = time.monotonic()

        def result(status, reason):
            # Retain the list so a cleanup failure is also visible to the caller.
            return DesktopResult(status=status, reason=reason, steps=steps).model_copy(update={"trace": trace})

        def cancelled():
            return cancel is not None and cancel.is_set()

        async def bounded(awaitable):
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
                done, _ = await asyncio.wait(watched, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                if cancelled():
                    raise asyncio.CancelledError()
                if operation not in done:
                    raise TimeoutError()
                return await operation
            finally:
                for pending in (operation, stop):
                    if pending is not None and not pending.done():
                        pending.cancel()
                await asyncio.gather(*(pending for pending in (operation, stop) if pending is not None),
                                     return_exceptions=True)

        try:
            if cancelled():
                return result("cancelled", "cancelled")
            await bounded(self.session.start())
            while True:
                if cancelled():
                    return result("cancelled", "cancelled")
                if time.monotonic() - started >= task.max_seconds:
                    return result("failed", "time-limit")
                try:
                    snapshot = await bounded(self.session.observe())
                    trace.append({"kind": "observe", "snapshot_id": snapshot.id, "elements": len(snapshot.elements)})
                    if desktop_goal_matches(task, snapshot):
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
                        action = DesktopAction.model_validate(proposal)
                        validate_desktop_action(action, snapshot)
                    except (ValueError, TypeError):
                        return result("failed", "invalid-action")
                    trace.append({"kind": "decision", "action": action.kind, "snapshot_id": snapshot.id})
                    if action.kind == "finish":
                        if task.expect_text is None:
                            return result("needs_review", "visual-verification-required")
                        return result("failed", "unverified-finish")
                    await bounded(self.session.validate(action))
                    if action.kind != "wait":
                        approved = self.approve is not None and await bounded(self.approve(action, snapshot))
                        trace.append({"kind": "approval", "action": action.kind, "approved": approved is True})
                        if approved is not True:
                            return result("failed", "approval-denied")
                        if cancelled():
                            return result("cancelled", "cancelled")
                        # Approval can take arbitrarily long. Never use its old
                        # coordinates without another check immediately afterward.
                        await bounded(self.session.validate(action))
                    if cancelled():
                        return result("cancelled", "cancelled")
                    # StaleDesktopObservation guarantees no effect was dispatched.
                    # Every other failure is terminal, including uncertain input.
                    await bounded(self.session.act(action))
                    steps += 1
                    trace.append({"kind": "action", "action": action.kind, "step": steps})
                except StaleDesktopObservation:
                    recoveries += 1
                    trace.append({"kind": "recovery", "reason": "stale-observation", "attempt": recoveries})
                    if recoveries > task.max_recoveries:
                        return result("failed", "stale-limit")
        except TimeoutError:
            return result("failed", "time-limit")
        except asyncio.CancelledError:
            return result("cancelled", "cancelled")
        except DesktopFailure:
            return result("failed", "desktop-error")
        except Exception:
            return result("failed", "runtime-error")
        finally:
            try:
                await asyncio.wait_for(self.session.close(), timeout=10)
            except (Exception, asyncio.CancelledError):
                trace.append({"kind": "cleanup", "reason": "close-error"})
