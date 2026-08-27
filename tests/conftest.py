import os
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# TEST_DATABASE_URL всегда побеждает: тесты обязаны работать со своей базой,
# даже если DATABASE_URL уже экспортирован в шелле (например, для локальных
# alembic-команд) — иначе session-фикстура _schema снесла бы схему рабочей
# базы через Base.metadata.drop_all. Имя базы дополнительно проверяется на
# суффикс _test как последний рубеж защиты.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5433/access_test"
)
assert (
    os.environ["DATABASE_URL"].rsplit("/", 1)[-1].endswith("_test")
), "тесты отказываются работать с базой, имя которой не оканчивается на _test"

# Приложение читает конфиг при импорте, поэтому окружение готовим до импортов app.*
from app.api.dependencies import hash_token  # noqa: E402
from app.application.provisioning import ProvisioningService  # noqa: E402
from app.domain.enums import Criticality, UserRole  # noqa: E402
from app.infrastructure.db import repositories as repos  # noqa: E402
from app.infrastructure.db import session as app_db  # noqa: E402
from app.infrastructure.db.models import Base, Resource, User  # noqa: E402
from app.infrastructure.provisioning.fake import FakeProvisioningProvider  # noqa: E402
from app.main import create_app  # noqa: E402

# NullPool вместо дефолтного пула: каждый checkout открывает новое asyncpg-
# соединение вместо переиспользования уже закрытого пулом соединения, ранее
# открытого в другом event loop'е (тесты pytest-asyncio по умолчанию
# выполняются в разных per-function loop'ах — переиспользование соединения
# из чужого loop'а падает с asyncpg RuntimeError "attached to a different
# loop"). Подмена глобалов app.db резолвится в момент вызова, поэтому
# покрывает и код приложения (FastAPI-зависимость get_session), не только
# тестовые фикстуры. На проде используется обычный QueuePool с
# pool_pre_ping — эта подмена не затрагивает app/db.py.
app_db.engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
app_db.session_factory = async_sessionmaker(app_db.engine, expire_on_commit=False, autoflush=False)
engine, session_factory = app_db.engine, app_db.session_factory

TABLES = "users, resources, access_requests, provisioning_jobs, external_access_grants"


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables():
    async with session_factory() as session:
        await session.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        await session.commit()


@pytest.fixture
async def db_session() -> AsyncSession:
    async with session_factory() as session:
        yield session


@dataclass
class Seeded:
    alice: User
    bob: User
    security: User
    gitlab: Resource
    analytics: Resource
    production_db: Resource
    broken: Resource


@pytest.fixture
async def seeded(db_session) -> Seeded:
    alice = User(username="alice", role=UserRole.EMPLOYEE, token_hash=hash_token("token-alice"))
    bob = User(username="bob", role=UserRole.EMPLOYEE, token_hash=hash_token("token-bob"))
    security = User(
        username="security-user", role=UserRole.SECURITY, token_hash=hash_token("token-sec")
    )
    db_session.add_all([alice, bob, security])
    await db_session.flush()

    gitlab = Resource(name="gitlab", owner_id=bob.id, criticality=Criticality.NORMAL)
    analytics = Resource(
        name="analytics-dashboard", owner_id=alice.id, criticality=Criticality.NORMAL
    )
    production_db = Resource(
        name="production-database", owner_id=bob.id, criticality=Criticality.HIGH
    )
    broken = Resource(name="broken-service", owner_id=bob.id, criticality=Criticality.NORMAL)
    db_session.add_all([gitlab, analytics, production_db, broken])
    await db_session.commit()

    return Seeded(alice, bob, security, gitlab, analytics, production_db, broken)


@pytest.fixture
async def client() -> AsyncClient:
    app = create_app(run_seed=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def provisioning(db_session):
    """Фабрика сервиса выдачи: тесты управляют отказами и лимитом попыток."""

    def build(fail_names=frozenset(), delay=0.0, max_attempts=3, lease_seconds=30):
        return ProvisioningService(
            uow=repos.SqlAlchemyUnitOfWork(db_session),
            requests=repos.AccessRequestRepository(db_session),
            jobs=repos.ProvisioningJobRepository(db_session),
            provider=FakeProvisioningProvider(db_session, set(fail_names), delay),
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )

    return build
