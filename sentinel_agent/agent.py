from __future__ import annotations

from uuid import uuid4

from sentinel_agent.desktop import DesktopEnvironment
from sentinel_agent.planner import HybridPlanner
from sentinel_agent.policy import SafetyPolicy
from sentinel_agent.schemas import AgentRun, RunEvent, RunStatus
from sentinel_agent.verifier import StateVerifier
from sentinel_agent.egress.client import EgressGateway


class SentinelAgent:
    def __init__(self, planner: HybridPlanner, max_steps: int = 10, *, egress_enabled=None, gateway_factory=EgressGateway):
        self.planner = planner
        self.max_steps = max_steps
        self.policy = SafetyPolicy()
        self.verifier = StateVerifier()
        self.runs: dict[str, AgentRun] = {}
        self.environments: dict[str, DesktopEnvironment] = {}
        self.egress_enabled = egress_enabled
        self.gateway_factory = gateway_factory
        self.gateways = {}
        self.network_pending = {}

    def close(self):
        for gateway in self.gateways.values():
            gateway.close()

    def _finish_gateway(self, run):
        gateway = self.gateways.get(run.id)
        if gateway and run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
            run.result["egress_log"] = str(gateway.audit.path)
            # Interactive live browsers need their proxy until session exit.
            if not self.environments[run.id].live_desktop or run.status == RunStatus.FAILED:
                gateway.close()
        return run

    async def start(
        self,
        instruction: str,
        use_model: bool = True,
        disturbance: str | None = None,
        live_desktop: bool = False,
    ) -> AgentRun:
        run_id = uuid4().hex[:10]
        gateway = self.gateway_factory(run_id, enabled=self.egress_enabled).start()
        self.gateways[run_id] = gateway
        environment = DesktopEnvironment(disturbance=disturbance, live_desktop=live_desktop, gateway=gateway)
        try:
            with gateway.activate():
                plan, planner_name = await self.planner.plan(
                    instruction, environment.public_state(), environment.action_descriptions, use_model,
                )
        except BaseException:
            gateway.close()
            raise
        plan.actions = plan.actions[: self.max_steps]
        run = AgentRun(id=run_id, instruction=instruction, status=RunStatus.RUNNING, plan=plan)
        for event in gateway.events:
            if event.get("decision") == "block" or event.get("dlp", {}).get("findings"):
                run.events.append(RunEvent(kind="egress", message=f"Outbound policy: {event['rule']}", data=event))
        run.events.append(RunEvent(kind="plan", message=f"Plan created by {planner_name}", data={"steps": len(plan.actions)}))
        self.runs[run_id] = run
        self.environments[run_id] = environment
        return self._finish_gateway(self._advance(run))

    def approve(self, run_id: str, approved: bool) -> AgentRun:
        run = self.runs[run_id]
        if run.status != RunStatus.WAITING_APPROVAL or run.pending_action is None:
            raise ValueError("This run has no action awaiting approval")
        if not approved:
            run.events.append(RunEvent(kind="approval", message="Human rejected the sensitive action"))
            run.pending_action = None
            run.status = RunStatus.FAILED
            self.network_pending.pop(run_id, None)
            return self._finish_gateway(run)
        run.events.append(RunEvent(kind="approval", message="Human approved the sensitive action"))
        action = run.pending_action
        run.pending_action = None
        run.status = RunStatus.RUNNING
        if run_id in self.network_pending:
            decision = self.network_pending.pop(run_id)
            if not self.gateways[run_id].approve_sync(decision):
                run.status = RunStatus.FAILED
                run.result = {"error": "Destination no longer eligible for approval"}
                return self._finish_gateway(run)
            return self._finish_gateway(self._advance(run))
        if not self._execute_and_verify(run, action):
            return self._finish_gateway(run)
        run.current_step += 1
        return self._finish_gateway(self._advance(run))

    def _advance(self, run: AgentRun) -> AgentRun:
        assert run.plan is not None
        while run.current_step < len(run.plan.actions):
            action = run.plan.actions[run.current_step]
            environment = self.environments[run.id]
            gateway = self.gateways[run.id]
            if action.name == "youtube_search" and environment.live_desktop and gateway.enabled:
                destination = gateway.check_sync("https://www.youtube.com/results")
                if not destination.allowed:
                    gateway.record(kind="request", method="BROWSER", host=destination.host,
                                   port=destination.port, decision="block", rule=destination.rule)
                    run.events.append(RunEvent(kind="egress", message=destination.reason,
                                               data={"rule": destination.rule}))
                    if destination.approval_required:
                        self.network_pending[run.id] = destination
                        run.status = RunStatus.WAITING_APPROVAL
                        run.pending_action = action
                    else:
                        run.status = RunStatus.FAILED
                        run.result = {"error": destination.reason}
                    return run
            decision = self.policy.evaluate(action)
            run.events.append(RunEvent(kind="policy", message=decision.reason, data={"action": action.name, "risk": decision.risk}))
            if not decision.allowed:
                run.status = RunStatus.FAILED
                run.result = {"error": decision.reason}
                return run
            if decision.requires_approval:
                run.status = RunStatus.WAITING_APPROVAL
                run.pending_action = action
                return run
            if not self._execute_and_verify(run, action):
                return run
            run.current_step += 1

        run.status = RunStatus.COMPLETED
        state = self.environments[run.id].public_state()
        run.result = {
            "shortages": state.get("shortages"),
            "report": state.get("report"),
            "planner_steps": len(run.plan.actions),
        }
        if state.get("active_window_title"):
            run.result["active_window_title"] = state["active_window_title"]
        if state.get("observed_controls") is not None:
            run.result["observed_controls"] = state["observed_controls"]
        if state.get("last_control_action"):
            run.result["last_control_action"] = state["last_control_action"]
        if state.get("scratch_file"):
            run.result["scratch_file"] = state["scratch_file"]
        if state.get("last_file_action"):
            run.result["file"] = state["last_file_action"]
        run.events.append(RunEvent(kind="complete", message="Goal completed and verified"))
        return run

    def _execute_and_verify(self, run: AgentRun, action) -> bool:
        observation = self.environments[run.id].execute(action)
        verified, reason = self.verifier.verify(observation)
        run.events.append(RunEvent(kind="action", message=observation.message, data={"action": action.name, "verified": verified}))
        if not verified:
            run.status = RunStatus.FAILED
            run.result = {"error": reason, "failed_action": action.name}
            return False
        return True
