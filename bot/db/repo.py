from sqlalchemy import select

from bot.db.base import get_session_maker
from bot.db.models import Application, ApplicationStatus, User, UserRole


async def get_or_create_user(tg_id: int) -> User:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is not None:
            return user
        user = User(tg_id=tg_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def set_user_role(tg_id: int, role: UserRole) -> User:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            user = User(tg_id=tg_id, role=role)
            session.add(user)
        else:
            user.role = role
        await session.commit()
        await session.refresh(user)
        return user


async def create_application_for_client(tg_id: int, description: str | None = None) -> Application:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            user = User(tg_id=tg_id, role=UserRole.client)
            session.add(user)
            await session.flush()

        app = Application(client_user_id=user.id, status=ApplicationStatus.new, description=description)
        session.add(app)
        await session.commit()
        await session.refresh(app)
        return app


async def list_incoming_applications(limit: int = 20) -> list[Application]:
    async with get_session_maker()() as session:
        res = await session.execute(
            select(Application).where(Application.status == ApplicationStatus.new).order_by(Application.created_at.desc()).limit(limit)
        )
        return list(res.scalars().all())
