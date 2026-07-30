"""Entry point: wire config, checkpointer, graph, Slack adapter, and run."""

from __future__ import annotations

import logging

from data_request_agent.config import Settings
from data_request_agent.destinations import SlackThreadDestination
from data_request_agent.governance import Governance
from data_request_agent.graph import AgentRuntime, build_graph
from data_request_agent.proposers import default_proposers
from data_request_agent.slack_app import create_slack_app
from data_request_agent.stores import PostgresStore


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    gov = Governance(settings.database_url)
    runtime = AgentRuntime(
        settings=settings,
        gov=gov,
        store=PostgresStore(settings.query_database_url),
        destination=SlackThreadDestination(bot_token=settings.slack_bot_token),
        proposers=default_proposers(settings=settings, gov=gov),
    )
    with gov.open_checkpointer() as handle:
        graph = build_graph(settings, handle.checkpointer, runtime=runtime)
        app = create_slack_app(settings=settings, graph=graph)
        app.start()


if __name__ == "__main__":
    main()
