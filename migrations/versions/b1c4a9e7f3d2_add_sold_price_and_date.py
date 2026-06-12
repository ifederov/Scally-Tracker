"""add sold_price and sold_date to user_items and manual_caps

Revision ID: b1c4a9e7f3d2
Revises: a7c4e1f9d2b3
Create Date: 2026-06-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1c4a9e7f3d2'
down_revision = 'a7c4e1f9d2b3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sold_price', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('sold_date', sa.Text(), nullable=True))

    with op.batch_alter_table('manual_caps', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sold_price', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('sold_date', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('manual_caps', schema=None) as batch_op:
        batch_op.drop_column('sold_date')
        batch_op.drop_column('sold_price')

    with op.batch_alter_table('user_items', schema=None) as batch_op:
        batch_op.drop_column('sold_date')
        batch_op.drop_column('sold_price')
