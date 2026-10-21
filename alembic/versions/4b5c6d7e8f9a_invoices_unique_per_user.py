"""make invoices uniqueness per-user (user_id, file_name)

Revision ID: 4b5c6d7e8f9a
Revises: 3a4f5b6c7d8e
Create Date: 2026-10-21

The invoices table had a global UNIQUE constraint on file_name, and the
storage layer upserted ON CONFLICT(file_name) while reassigning user_id,
so a second user uploading a file with the same name silently stole the
first user's record. Uniqueness must be per user.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b5c6d7e8f9a'
down_revision: Union[str, Sequence[str], None] = '3a4f5b6c7d8e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "invoices" not in inspector.get_table_names():
        return
    with op.batch_alter_table('invoices', schema=None) as batch_op:
        batch_op.drop_constraint('uq_invoices_file_name', type_='unique')
        batch_op.create_unique_constraint('uq_invoices_user_file', ['user_id', 'file_name'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "invoices" not in inspector.get_table_names():
        return
    with op.batch_alter_table('invoices', schema=None) as batch_op:
        batch_op.drop_constraint('uq_invoices_user_file', type_='unique')
        batch_op.create_unique_constraint('uq_invoices_file_name', ['file_name'])
