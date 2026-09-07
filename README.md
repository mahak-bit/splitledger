# SplitLedger

[![CI](https://github.com/mahak-bit/splitledger/actions/workflows/ci.yml/badge.svg)](https://github.com/mahak-bit/splitledger/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-261230)](https://docs.astral.sh/ruff/)
[![Typing: mypy strict](https://img.shields.io/badge/mypy-strict-2a6db2)](https://mypy-lang.org/)

**Share expenses across a group, then settle up in the fewest possible payments.**

An async FastAPI service with a real algorithmic core: it reduces a group's whole
ledger to one number per person, then computes a *provably minimal* set of
payments that clears it.

---

## The interesting part: minimising payments

Five friends come back from a trip owing each other money. Their net balances,
in cents, are:

| Person | Net    |
| ------ | ------ |
| Ada    | `+700` |
| Blaise | `+600` |
| Cleo   | `-300` |
| Dmitri | `-400` |
| Esther | `-600` |

The obvious approach — repeatedly make the biggest debtor pay the biggest
creditor — needs **four** transfers. It pays Esther's 600 to Ada first, which
splinters Ada's balance and drags everyone into one long chain.

SplitLedger returns **three**, because it notices the ledger separates:

```
{Blaise, Esther}      600 and -600      -> 1 payment
{Ada, Cleo, Dmitri}   700, -300, -400   -> 2 payments
```

### Why three is the floor, not a lucky guess

Two facts pin down the optimum:

1. Any zero-sum set of `k` people can always be settled in exactly `k - 1`
   transfers — pay somebody off completely, drop them, repeat.
2. In *any* valid settlement, each connected component of the payment graph must
   itself be zero-sum. A settlement whose graph has `c` components therefore
   uses at least `n - c` transfers.

So the minimum is `n - g`, where `g` is the largest number of disjoint zero-sum
subsets the members can be partitioned into. Here `5 - 2 = 3`.

Maximising `g` contains the PARTITION problem, so it is NP-hard. SplitLedger
solves it exactly with a subset-sum DP over bitmasks — `O(3^n)`, which stays
comfortable up to about a dozen members — and above that threshold falls back to
the greedy match, which never needs more than `n - 1` transfers. The API tells
you which one you got:

```json
{
  "method": "exact",
  "transfer_count": 3,
  "total_amount": "13.00",
  "transfers": [{ "payer": "…", "payee": "…", "amount": "6.00" }]
}
```

The implementation lives in [`settlement.py`](src/splitledger/settlement.py), and
[`test_settlement.py`](tests/test_settlement.py) contains the case above as a
regression test against greedy.

## Money is never a float

Every amount is stored as an integer count of minor units. Splits go through a
largest-remainder (Hamilton) allocator, so the parts always add back up to the
total: `10.00` split three ways is `3.34 / 3.33 / 3.33`, deterministically, with
no cent invented or lost. That invariant is checked by randomised tests in
[`test_money.py`](tests/test_money.py) — because the settlement proof above only
holds if balances really do sum to zero.

Four split strategies are supported: `equal`, `exact`, `percentage`, and
`shares` (weights, e.g. `2:1:1` for a couple sharing with two singles).

## Quickstart

```bash
git clone https://github.com/mahak-bit/splitledger.git
cd splitledger
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

See the algorithm work on a seeded ledger:

```bash
splitledger demo
```

Run the API and open the interactive docs at <http://127.0.0.1:8000/docs>:

```bash
splitledger init-db && splitledger serve --reload
```

### Docker

```bash
docker compose up --build
```

That starts the API on port 8000 against Postgres, running migrations first.

## API

All endpoints sit under `/api/v1`. Authenticate by POSTing to `/auth/token` and
sending `Authorization: Bearer <token>`.

| Method   | Path                                | Purpose                                     |
| -------- | ----------------------------------- | ------------------------------------------- |
| `POST`   | `/auth/register`                    | Create an account                           |
| `POST`   | `/auth/token`                       | Exchange credentials for an access token    |
| `GET`    | `/auth/me`                          | The current user                            |
| `POST`   | `/groups`                           | Create a group (creator becomes owner)      |
| `GET`    | `/groups`                           | Groups you belong to                        |
| `GET`    | `/groups/{id}`                      | Group with its members                      |
| `POST`   | `/groups/{id}/members`              | Add a member *(owners only)*                |
| `DELETE` | `/groups/{id}/members/{user_id}`    | Remove a member *(only once they are square)* |
| `POST`   | `/groups/{id}/expenses`             | Record and split an expense                 |
| `GET`    | `/groups/{id}/expenses`             | List expenses, filterable by payer          |
| `DELETE` | `/groups/{id}/expenses/{id}`        | Delete *(author or owner)*                  |
| `GET`    | `/groups/{id}/balances`             | Net position of every member                |
| `GET`    | `/groups/{id}/settlement-plan`      | **The minimal set of payments**             |
| `POST`   | `/groups/{id}/settlements`          | Record a payment you made                   |
| `GET`    | `/groups/{id}/settlements`          | Payment history                             |
| `GET`    | `/healthz`, `/readyz`               | Liveness and readiness probes               |

Example — split a dinner three ways:

```bash
curl -X POST localhost:8000/api/v1/groups/$GROUP/expenses \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
        "description": "Dinner",
        "amount": "10.00",
        "paid_by": "'$ADA'",
        "strategy": "equal",
        "participants": ["'$ADA'", "'$BLAISE'", "'$CLEO'"]
      }'
```

Every failure returns the same envelope, so clients only parse one shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Exact split amounts must sum to the expense total.",
    "details": { "difference_minor": -1000 }
  }
}
```

## Design notes

- **Balances are always derived**, never cached on a row. Deleting an expense can
  therefore never leave a stale total behind.
- **Non-members get `404`, not `403`.** Whether a given group exists is not
  something an outsider should be able to probe.
- **Passwords use Argon2id**, with transparent rehashing when the parameters move
  on, and a dummy hash on unknown accounts so login timing does not leak which
  addresses are registered.
- **Only the payer may record a settlement.** Letting anyone credit themselves
  from someone else's account would make the ledger trivially forgeable.
- **A member with a non-zero balance cannot be removed**, and a group always
  keeps at least one owner.

## Layout

```
src/splitledger/
├── settlement.py     # the minimal-payment solver
├── money.py          # minor units + largest-remainder allocation
├── models.py         # SQLAlchemy 2.0 ORM
├── schemas.py        # pydantic request/response contract
├── deps.py           # auth and group-access dependencies
├── services/         # business logic (routers stay thin)
└── api/              # HTTP routers
tests/                # unit tests for the core, end-to-end tests for the API
migrations/           # Alembic
```

## Development

```bash
make test      # pytest with coverage
make lint      # ruff check + format check
make typecheck # mypy --strict
make check     # all three, the same set CI runs
```

CI runs the full suite on Python 3.10, 3.11 and 3.12, plus a Docker build.

## Configuration

Every setting is an environment variable prefixed `SPLITLEDGER_` (see
[`.env.example`](.env.example)).

| Variable                     | Default                            | Notes                                       |
| ---------------------------- | ---------------------------------- | ------------------------------------------- |
| `DATABASE_URL`               | `sqlite+aiosqlite:///./splitledger.db` | Or `postgresql+asyncpg://…`             |
| `JWT_SECRET`                 | insecure dev default               | Startup **fails** if unset in production     |
| `ACCESS_TOKEN_TTL_MINUTES`   | `720`                              |                                             |
| `SETTLEMENT_EXACT_THRESHOLD` | `12`                               | Group size above which planning goes greedy |
| `CORS_ORIGINS`               | `["http://localhost:3000"]`        | JSON list                                   |

## License

MIT — see [LICENSE](LICENSE).
