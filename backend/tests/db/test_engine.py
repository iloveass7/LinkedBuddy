import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_db_errors_hide_parameters(engine: AsyncEngine) -> None:
    """jobs.error and logs carry str(exc); bound values (README text, post bodies) must not leak."""
    assert engine.sync_engine.hide_parameters
    async with engine.connect() as conn:
        with pytest.raises(DBAPIError) as caught:
            await conn.execute(text("SELECT :v::text, 1/0"), {"v": "secret-readme-text"})
    assert "secret-readme-text" not in str(caught.value)
