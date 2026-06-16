"""add catalog caps support

Revision ID: e2f4a7b8c1d6
Revises: d7e1f3b2a9c4
Create Date: 2026-06-16

"""
from alembic import op
import sqlalchemy as sa

revision = 'e2f4a7b8c1d6'
down_revision = 'd7e1f3b2a9c4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('manual_caps', sa.Column('is_catalog', sa.Integer(), nullable=False, server_default='0'))

    op.create_table(
        'user_catalog_items',
        sa.Column('id',            sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('user_id',       sa.Integer(), sa.ForeignKey('users.id'),       nullable=False),
        sa.Column('manual_cap_id', sa.Integer(), sa.ForeignKey('manual_caps.id'), nullable=False),
        sa.Column('owned',         sa.Integer(), nullable=False, server_default='0'),
        sa.Column('wishlisted',    sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sold',          sa.Integer(), nullable=False, server_default='0'),
        sa.Column('notes',         sa.Text(),    nullable=True),
        sa.Column('sold_price',    sa.Float(),   nullable=True),
        sa.Column('sold_date',     sa.Text(),    nullable=True),
        sa.Column('updated_at',    sa.Text(),    nullable=True),
        sa.UniqueConstraint('user_id', 'manual_cap_id', name='uq_user_catalog_item'),
    )
    op.create_index('ix_uci_user_id', 'user_catalog_items', ['user_id'])
    op.create_index('ix_uci_cap_id',  'user_catalog_items', ['manual_cap_id'])


def downgrade():
    op.drop_index('ix_uci_cap_id',  table_name='user_catalog_items')
    op.drop_index('ix_uci_user_id', table_name='user_catalog_items')
    op.drop_table('user_catalog_items')
    op.drop_column('manual_caps', 'is_catalog')
