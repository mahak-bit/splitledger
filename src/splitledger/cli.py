"""Operational CLI: run the server, create tables, or seed a demo ledger."""

import argparse
import asyncio
import uuid
from collections.abc import Sequence
from decimal import Decimal

from splitledger import models  # noqa: F401 - imported so the tables register on Base
from splitledger.config import get_settings
from splitledger.db import Base, dispose_engine, get_session_factory, init_engine
from splitledger.models import SplitStrategy
from splitledger.money import to_major_units
from splitledger.schemas import ExpenseCreate, GroupCreate, MemberAdd, ShareInput, UserCreate
from splitledger.services import balances as balance_service
from splitledger.services import expenses as expense_service
from splitledger.services import groups as group_service
from splitledger.services import users as user_service

DEMO_PASSWORD = "demo-password-123"  # noqa: S105 - seed data for a local database
DEMO_PEOPLE = ["Ada", "Blaise", "Cleo", "Dmitri", "Esther"]


async def _create_schema() -> None:
    settings = get_settings()
    engine = init_engine(settings.database_url, echo=settings.database_echo)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await dispose_engine()


async def _seed_demo() -> None:
    settings = get_settings()
    engine = init_engine(settings.database_url, echo=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    suffix = uuid.uuid4().hex[:8]
    factory = get_session_factory()
    async with factory() as session:
        people = {}
        for name in DEMO_PEOPLE:
            people[name] = await user_service.register(
                session,
                UserCreate(
                    email=f"{name.lower()}.{suffix}@example.com",
                    display_name=name,
                    password=DEMO_PASSWORD,
                ),
            )

        owner = people["Ada"]
        group = await group_service.create_group(
            session,
            owner,
            GroupCreate(name=f"Lisbon trip {suffix}", description="Demo ledger", currency="EUR"),
        )
        for name in DEMO_PEOPLE[1:]:
            await group_service.add_member(session, group, MemberAdd(email=people[name].email))
        group = await group_service.get_group(session, group.id)

        everyone = [people[name].id for name in DEMO_PEOPLE]
        await expense_service.create_expense(
            session,
            group,
            owner,
            ExpenseCreate(
                description="Apartment, three nights",
                amount=Decimal("900.00"),
                paid_by=owner.id,
                strategy=SplitStrategy.EQUAL,
                participants=everyone,
            ),
        )
        await expense_service.create_expense(
            session,
            group,
            people["Blaise"],
            ExpenseCreate(
                description="Rental car",
                amount=Decimal("240.00"),
                paid_by=people["Blaise"].id,
                strategy=SplitStrategy.SHARES,
                shares=[
                    ShareInput(user_id=people["Blaise"].id, value=Decimal(2)),
                    ShareInput(user_id=people["Cleo"].id, value=Decimal(1)),
                    ShareInput(user_id=people["Dmitri"].id, value=Decimal(1)),
                ],
            ),
        )
        await expense_service.create_expense(
            session,
            group,
            people["Esther"],
            ExpenseCreate(
                description="Tasting menu",
                amount=Decimal("310.00"),
                paid_by=people["Esther"].id,
                strategy=SplitStrategy.EXACT,
                shares=[
                    ShareInput(user_id=people["Ada"].id, value=Decimal("120.00")),
                    ShareInput(user_id=people["Cleo"].id, value=Decimal("95.50")),
                    ShareInput(user_id=people["Esther"].id, value=Decimal("94.50")),
                ],
            ),
        )

        report = await balance_service.balance_report(session, group)
        plan = await balance_service.plan_for_group(
            session, group, exact_threshold=settings.settlement_exact_threshold
        )
        names = {people[name].id: name for name in DEMO_PEOPLE}

        print(f"\nGroup: {group.name} ({group.currency})\n")
        print("Balances")
        for entry in report:
            print(f"  {entry.display_name:<8} {entry.net:>10}")
        print(f"\nSettlement plan ({plan.method}, {plan.transfer_count} transfers)")
        for transfer in plan.transfers:
            payer = names.get(transfer.payer, str(transfer.payer))
            payee = names.get(transfer.payee, str(transfer.payee))
            print(f"  {payer:<8} -> {payee:<8} {to_major_units(transfer.amount_minor):>10}")
        print()

    await dispose_engine()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="splitledger", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="run the API with uvicorn")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="reload on source changes")

    subcommands.add_parser("init-db", help="create tables directly (dev shortcut for Alembic)")
    subcommands.add_parser("demo", help="seed a demo group and print its settlement plan")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        import uvicorn

        uvicorn.run("splitledger.main:app", host=args.host, port=args.port, reload=args.reload)
    elif args.command == "init-db":
        asyncio.run(_create_schema())
        print("Tables created.")
    else:
        asyncio.run(_seed_demo())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
