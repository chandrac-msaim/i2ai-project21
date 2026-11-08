"""Declarative base. Tables live in schema 'advisor' and are created only by db/schema.sql."""
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    metadata = MetaData(schema="advisor")
