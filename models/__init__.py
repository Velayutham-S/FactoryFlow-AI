"""The FactoryFlow AI SQLAlchemy ORM layer.

53 models across three logical groups -- 29 master, 22 operational, 2 system -- in
one MetaData bound to one SQLite database file.

Import ``models.registry`` to configure every mapper and complete the MetaData. It
is Alembic's target and the single module that lists all 53 (§44.1).
"""
