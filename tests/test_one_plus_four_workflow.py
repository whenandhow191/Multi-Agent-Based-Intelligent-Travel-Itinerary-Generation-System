"""C24 end-to-end fan-out/fan-in and structured hand-off tests."""

import asyncio

from packages.agents import (
    DestinationIntelArtifact,
    DeterministicCritic,
    DeterministicItineraryOptimizer,
    MobilityLodgingArtifact,
)
from packages.evals.fixtures import FIXTURE_NOW, build_synthetic_scenario
from packages.harness import MemoryBlackboard
from packages.workflow import OnePlusFourWorkflow, WorkflowServices


def test_one_plus_four_workflow_runs_a1_a2_in_parallel_and_fans_in() -> None:
    async def scenario_run() -> None:
        scenario = build_synthetic_scenario()
        board = MemoryBlackboard()
        both_started = asyncio.Event()
        started: set[str] = set()
        start_lock = asyncio.Lock()

        async def mark_started(name: str) -> None:
            async with start_lock:
                started.add(name)
                if len(started) == 2:
                    both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.2)

        async def destination_runner(request):  # type: ignore[no-untyped-def]
            assert request == scenario.request
            await mark_started("destination")
            return DestinationIntelArtifact(
                artifact_id="artifact_destination_workflow",
                run_id="run_workflow_fixture",
                producer_agent="destination_intelligence",
                destination=request.destination,
                places=scenario.places,
                weather=scenario.weather,
                claims=scenario.claims,
                evidence=scenario.evidence[:4],
                created_at=FIXTURE_NOW,
            )

        async def mobility_runner(request):  # type: ignore[no-untyped-def]
            assert request == scenario.request
            await mark_started("mobility")
            return MobilityLodgingArtifact(
                artifact_id="artifact_mobility_workflow",
                run_id="run_workflow_fixture",
                producer_agent="mobility_lodging",
                transport_options=scenario.transport,
                lodging_candidates=scenario.lodging,
                evidence=scenario.evidence[4:6],
                unknowns=("参考车次需要人工确认。",),
                manual_confirmation_required=True,
                created_at=FIXTURE_NOW,
            )

        async def route_builder(destination, mobility):  # type: ignore[no-untyped-def]
            assert destination.places and mobility.lodging_candidates
            return scenario.routes

        async def planning_runner(request, destination, mobility, routes):  # type: ignore[no-untyped-def]
            return DeterministicItineraryOptimizer().solve(
                request=request,
                destination=destination,
                mobility=mobility,
                route_matrix=routes,
                run_id="run_workflow_fixture",
                created_at=FIXTURE_NOW,
            )

        async def critic_runner(  # type: ignore[no-untyped-def]
            request, candidates, destination, mobility, routes
        ):
            return DeterministicCritic().review(
                request=request,
                candidates=candidates,
                destination=destination,
                mobility=mobility,
                route_matrix=routes,
                created_at=FIXTURE_NOW,
            )

        workflow = OnePlusFourWorkflow(
            board,
            WorkflowServices(
                destination=destination_runner,
                mobility=mobility_runner,
                build_route_matrix=route_builder,
                planning=planning_runner,
                critic=critic_runner,
            ),
        )
        result = await workflow.run(scenario.request, run_id="run_workflow_fixture")

        spans = {span.task_id: span for span in result.trace.spans}
        destination_span = spans["destination_intelligence"]
        mobility_span = spans["mobility_lodging"]
        assert destination_span.started_at <= mobility_span.ended_at
        assert mobility_span.started_at <= destination_span.ended_at
        assert spans["build_route_matrix"].input_artifact_refs == (
            destination_span.output_artifact_ref,
            mobility_span.output_artifact_ref,
        )
        assert len(result.candidates.plans) == 3
        assert result.review.verdict.value == "pass"

        lineage = await board.lineage(result.review.artifact_id)
        lineage_ids = {item.artifact_id for item in lineage}
        assert {
            result.destination.artifact_id,
            result.mobility.artifact_id,
            result.route_matrix.artifact_id,
            result.candidates.artifact_id,
            result.review.artifact_id,
        } <= lineage_ids

    asyncio.run(scenario_run())
