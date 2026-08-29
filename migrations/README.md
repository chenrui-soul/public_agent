# Database migrations

Alembic migrations are the only supported way to change the production schema.

```powershell
alembic upgrade head
alembic downgrade -1
```

