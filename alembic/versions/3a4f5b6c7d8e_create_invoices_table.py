"""create invoices table

Revision ID: 3a4f5b6c7d8e
Revises: 2f3961ae1a36
Create Date: 2026-10-19 07:45:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a4f5b6c7d8e'
down_revision: Union[str, Sequence[str], None] = '2f3961ae1a36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create invoices table matching app/models/invoice.py if not already present."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "invoices" not in tables:
        op.create_table(
            'invoices',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Uuid(), nullable=False),
            sa.Column('file_name', sa.String(length=255), nullable=True),
            sa.Column('vendor_name', sa.String(length=255), nullable=True),
            sa.Column('vendor_address', sa.Text(), nullable=True),
            sa.Column('buyer_name', sa.String(length=255), nullable=True),
            sa.Column('buyer_address', sa.Text(), nullable=True),
            sa.Column('invoice_number', sa.String(length=100), nullable=True),
            sa.Column('invoice_date', sa.String(length=50), nullable=True),
            sa.Column('items', sa.Text(), nullable=True),
            sa.Column('subtotal', sa.Float(), nullable=True),
            sa.Column('tax', sa.Float(), nullable=True),
            sa.Column('discount', sa.Float(), nullable=True),
            sa.Column('total_amount', sa.Float(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('file_name', name='uq_invoices_file_name')
        )
        with op.batch_alter_table('invoices', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_invoices_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    """Drop invoices table if present."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "invoices" in tables:
        with op.batch_alter_table('invoices', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_invoices_user_id'))
        op.drop_table('invoices')
